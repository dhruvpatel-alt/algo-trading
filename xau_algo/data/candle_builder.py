"""
xau_algo/data/candle_builder.py
================================
Builds complete 1-minute OHLC candles from live price ticks.

How it works
------------
Each incoming price tick is assigned to a 1-minute bucket based on its
timestamp. When the minute changes, the previous candle is "closed" and
the on_candle_closed callback is fired.

The on_price_tick callback fires on every tick (for live breakout detection).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, TYPE_CHECKING

from xau_algo.strategies.base_strategy import Candle

if TYPE_CHECKING:
    from xau_algo.storage.supabase_repository import SupabaseRepository
    from xau_algo.health.state import HealthState

logger = logging.getLogger(__name__)


class CandleBuilder:
    """
    Assembles live price ticks into 1-minute OHLC candles.

    Parameters
    ----------
    on_candle_closed : Callable[[Candle], None]
        Fired when a 1-minute candle completes.
    on_price_tick : Callable[[float, datetime], None]
        Fired on every incoming price tick (used for live breakout detection).
    supabase_repo : SupabaseRepository | None
        Optional — async candle persistence.
    health_state : HealthState | None
        Optional — updated when each candle closes.
    """

    def __init__(
        self,
        on_candle_closed: Callable[[Candle], None],
        on_price_tick: Callable[[float, datetime], None],
        supabase_repo: "SupabaseRepository | None" = None,
        health_state: "HealthState | None" = None,
    ) -> None:
        self._on_candle_closed = on_candle_closed
        self._on_price_tick = on_price_tick
        self._supabase = supabase_repo
        self._health = health_state

        self._current_minute: int | None = None   # Unix timestamp of current minute
        self._open: float | None = None
        self._high: float | None = None
        self._low: float | None = None
        self._close: float | None = None
        self._candle_time: datetime | None = None

    def on_tick(self, price: float, timestamp: datetime) -> None:
        """
        Process an incoming price tick.

        Parameters
        ----------
        price : float
            Current bid/last price.
        timestamp : datetime
            Tick timestamp (timezone-aware preferred).
        """
        # Determine which 1-minute bucket this tick belongs to
        ts_unix = int(timestamp.timestamp())
        minute_ts = ts_unix - (ts_unix % 60)

        if self._current_minute is None:
            # First tick ever
            self._start_new_candle(price, timestamp, minute_ts)

        elif minute_ts != self._current_minute:
            # New minute — close current candle, start fresh
            self._close_current_candle()
            self._start_new_candle(price, timestamp, minute_ts)

        else:
            # Same minute — update in-progress candle
            self._update_candle(price)

        # Always fire the price tick callback (for live breakout detection)
        self._on_price_tick(price, timestamp)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _start_new_candle(
        self, price: float, timestamp: datetime, minute_ts: int
    ) -> None:
        self._current_minute = minute_ts
        self._open = price
        self._high = price
        self._low = price
        self._close = price
        # Floor timestamp to the start of the minute
        self._candle_time = datetime.fromtimestamp(minute_ts, tz=timezone.utc)
        logger.debug("New candle started at %s | open=%.5f", self._candle_time, price)

    def _update_candle(self, price: float) -> None:
        assert self._high is not None and self._low is not None
        self._high = max(self._high, price)
        self._low = min(self._low, price)
        self._close = price

    def _close_current_candle(self) -> None:
        if (
            self._current_minute is None
            or self._open is None
            or self._high is None
            or self._low is None
            or self._close is None
            or self._candle_time is None
        ):
            return

        candle = Candle(
            timestamp=self._candle_time,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
        )

        logger.debug(
            "Candle closed: %s | O=%.5f H=%.5f L=%.5f C=%.5f",
            candle.timestamp,
            candle.open,
            candle.high,
            candle.low,
            candle.close,
        )

        self._on_candle_closed(candle)

        # Update health state with latest candle timestamp
        if self._health is not None:
            self._health.set_last_candle(candle.timestamp)

        # Async Supabase write — happens after strategy callback so it
        # never stalls signal detection
        if self._supabase is not None:
            self._supabase.save_candle(candle)
