"""
vt_markets/mt5/market_data.py
==============================
Market data provider for VT Markets using MT5 only.

No Twelve Data. No external API keys.

Provides:
  - Historical 1-minute OHLC candles
  - Live tick data (bid/ask/last)
  - Real-time candle builder from ticks
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Callable

import pytz

from vt_markets.models.candle import Candle
from vt_markets.models.tick import Tick

logger = logging.getLogger(__name__)

_IST = pytz.timezone("Asia/Kolkata")


class MT5MarketData:
    """
    Fetches historical and live market data from MT5.

    Parameters
    ----------
    symbol : str
        Confirmed broker symbol (e.g., "XAUUSD").
    """

    def __init__(self, symbol: str) -> None:
        self._symbol = symbol

    # ------------------------------------------------------------------
    # Historical data
    # ------------------------------------------------------------------

    def get_historical_candles(self, count: int = 200) -> list[Candle]:
        """
        Fetch the most recent `count` 1-minute candles from MT5.

        Returns
        -------
        list[Candle]
            Chronological list (oldest first).
        """
        import MetaTrader5 as mt5

        rates = mt5.copy_rates_from_pos(
            self._symbol,
            mt5.TIMEFRAME_M1,   # type: ignore[attr-defined]
            0,                  # starting from the most recent
            count,
        )

        if rates is None or len(rates) == 0:
            error = mt5.last_error()
            raise RuntimeError(
                f"copy_rates_from_pos failed for '{self._symbol}': "
                f"code={error[0]} message='{error[1]}'"
            )

        candles: list[Candle] = []
        for r in rates:
            # MT5 timestamps are UTC Unix timestamps
            ts = datetime.fromtimestamp(r["time"], tz=timezone.utc)
            candles.append(Candle(
                timestamp=ts,
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=float(r.get("tick_volume", 0)),
            ))

        logger.info(
            "Fetched %d historical candles for %s (%s → %s)",
            len(candles),
            self._symbol,
            candles[0].timestamp.strftime("%Y-%m-%d %H:%M"),
            candles[-1].timestamp.strftime("%Y-%m-%d %H:%M"),
        )
        return candles  # already chronological from MT5

    # ------------------------------------------------------------------
    # Live tick data
    # ------------------------------------------------------------------

    def get_latest_tick(self) -> Tick:
        """
        Retrieve the most recent tick for the symbol.

        Returns
        -------
        Tick
            Current bid/ask/last with UTC timestamp.
        """
        import MetaTrader5 as mt5

        tick = mt5.symbol_info_tick(self._symbol)
        if tick is None:
            error = mt5.last_error()
            raise RuntimeError(
                f"symbol_info_tick failed for '{self._symbol}': "
                f"code={error[0]} message='{error[1]}'"
            )

        ts = datetime.fromtimestamp(tick.time, tz=timezone.utc)
        return Tick(
            timestamp=ts,
            bid=tick.bid,
            ask=tick.ask,
            last=tick.last,
            volume=tick.volume,
        )


class LiveCandleBuilder:
    """
    Builds 1-minute OHLC candles from a stream of live ticks.

    Fires `on_candle_closed(candle)` when a minute completes.
    Fires `on_tick(tick)` on every incoming tick.

    Usage
    -----
    builder = LiveCandleBuilder(on_candle_closed=..., on_tick=...)
    builder.process_tick(tick)
    """

    def __init__(
        self,
        on_candle_closed: Callable[[Candle], None],
        on_tick: Callable[[Tick], None],
    ) -> None:
        self._on_candle_closed = on_candle_closed
        self._on_tick = on_tick

        self._current_minute: int | None = None
        self._open: float | None = None
        self._high: float | None = None
        self._low: float | None = None
        self._close: float | None = None
        self._candle_time: datetime | None = None

    def process_tick(self, tick: Tick) -> None:
        """Process one incoming price tick."""
        ts_unix = int(tick.timestamp.timestamp())
        minute_ts = ts_unix - (ts_unix % 60)

        if self._current_minute is None:
            self._start_candle(tick, minute_ts)
        elif minute_ts != self._current_minute:
            self._close_candle()
            self._start_candle(tick, minute_ts)
        else:
            self._update_candle(tick)

        # Always fire raw tick callback
        self._on_tick(tick)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _start_candle(self, tick: Tick, minute_ts: int) -> None:
        mid = tick.mid
        self._current_minute = minute_ts
        self._open = mid
        self._high = mid
        self._low = mid
        self._close = mid
        self._candle_time = datetime.fromtimestamp(minute_ts, tz=timezone.utc)
        logger.debug("New candle @ %s open=%.5f", self._candle_time, mid)

    def _update_candle(self, tick: Tick) -> None:
        mid = tick.mid
        if self._high is not None:
            self._high = max(self._high, mid)
        if self._low is not None:
            self._low = min(self._low, mid)
        self._close = mid

    def _close_candle(self) -> None:
        if None in (self._current_minute, self._open, self._high,
                    self._low, self._close, self._candle_time):
            return
        candle = Candle(
            timestamp=self._candle_time,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
        )
        logger.debug(
            "Candle closed: %s O=%.5f H=%.5f L=%.5f C=%.5f",
            candle.timestamp,
            candle.open,
            candle.high,
            candle.low,
            candle.close,
        )
        self._on_candle_closed(candle)
