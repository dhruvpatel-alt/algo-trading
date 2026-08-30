"""
xau_algo/backtest/engine.py
============================
Historical backtesting engine for EMA20 and EMA50 strategies.

How it works
------------
1. Loads historical 1-minute OHLC candles (chronological order).
2. Feeds each candle to both strategies via on_candle_closed().
   - The strategy detects a setup on candle N.
   - The NEXT candle (N+1) is used as the breakout check via on_candle_closed().
     Inside BaseEMAStrategy.on_candle_closed(), when _pending_setup is set and
     _next_candle_seen is False, the OHLC breakout check fires before scanning
     for new setups.
3. After each candle, the broker checks open positions for SL/TP via check_ohlc().
4. At the end, a summary report is printed.

Assumptions (documented)
------------------------
- Entry at breakout level (config.ENTRY_AT_BREAKOUT_LEVEL = True)
- SL hit first on same-candle SL/TP conflict (config.ASSUME_SL_FIRST_ON_CONFLICT = True)
- No tick-level data; OHLC range used for SL/TP detection
- No slippage or spread modelled

Look-ahead bias prevention
--------------------------
- EMA is calculated incrementally — the strategy only sees closes UP TO candle N
  when evaluating candle N.
- setup_candle and breakout_candle are always distinct candles.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Sequence

import pandas as pd
import pytz

from xau_algo import config
from xau_algo.data.twelve_data_client import TwelveDataClient
from xau_algo.strategies.base_strategy import Candle
from xau_algo.strategies.ema20_strategy import EMA20Strategy
from xau_algo.strategies.ema50_strategy import EMA50Strategy
from xau_algo.trading.paper_broker import PaperBroker
from xau_algo.trading.daily_target import DailyTargetGuard
from xau_algo.storage.trade_repository import TradeRepository

logger = logging.getLogger(__name__)

_IST = pytz.timezone(config.TIMEZONE)


class BacktestEngine:
    """
    Runs a full historical backtest of the EMA20 and EMA50 strategies.

    Parameters
    ----------
    candles : list[Candle]
        Historical candles in chronological order (oldest first).
    initial_capital : float
        Starting account balance.
    """

    def __init__(
        self,
        candles: list[Candle],
        initial_capital: float | None = None,
        trade_repository: TradeRepository | None = None,
    ) -> None:
        self._candles = candles

        self._broker = PaperBroker(
            initial_capital=initial_capital,
            on_trade_closed=self._on_trade_closed,
        )
        self._daily_guard = DailyTargetGuard(
            initial_capital=initial_capital or config.INITIAL_CAPITAL
        )

        self._ema20 = EMA20Strategy(broker=self._broker, daily_guard=self._daily_guard)
        self._ema50 = EMA50Strategy(broker=self._broker, daily_guard=self._daily_guard)

        self._repo = trade_repository or TradeRepository()

        # Track current trading day for daily reset
        self._current_day: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """
        Execute the backtest over all candles.

        Returns
        -------
        dict
            Summary statistics.
        """
        logger.info(
            "Starting backtest: %d candles | capital=$%.2f",
            len(self._candles),
            self._broker.balance,
        )

        for i, candle in enumerate(self._candles):
            self._check_daily_reset(candle.timestamp)

            # Feed to both strategies INDEPENDENTLY
            self._ema20.on_candle_closed(candle)
            self._ema50.on_candle_closed(candle)

            # Check SL/TP for all open positions against this candle's OHLC
            self._broker.check_ohlc(
                candle_open=candle.open,
                candle_high=candle.high,
                candle_low=candle.low,
                candle_close=candle.close,
                timestamp=candle.timestamp,
            )

        return self._build_summary()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_daily_reset(self, timestamp: datetime) -> None:
        """Reset daily counters at session start (03:45 IST) on trading days."""
        # Convert to IST
        if timestamp.tzinfo is None:
            ts_ist = _IST.localize(timestamp)
        else:
            ts_ist = timestamp.astimezone(_IST)

        day_str = ts_ist.strftime("%Y-%m-%d")
        weekday = ts_ist.weekday()

        if day_str != self._current_day and weekday in config.TRADING_DAYS:
            # Check if we are at or past the session start time
            session_start_h, session_start_m = map(int, config.START_TIME.split(":"))
            session_start_minutes = session_start_h * 60 + session_start_m
            current_minutes = ts_ist.hour * 60 + ts_ist.minute

            if current_minutes >= session_start_minutes:
                self._current_day = day_str
                self._broker.reset_daily()
                self._daily_guard.reset(current_balance=self._broker.balance)
                logger.info(
                    "=== Trading session started: %s (%s) ===",
                    day_str,
                    ts_ist.strftime("%A"),
                )

    def _on_trade_closed(self, position) -> None:
        """Callback from broker when a position closes."""
        self._repo.save_trade(position)

    def _build_summary(self) -> dict:
        """Compute and log backtest summary statistics."""
        closed = self._broker.closed_positions
        open_pos = self._broker.open_positions

        wins = [p for p in closed if p.status == "CLOSED_TP"]
        losses = [p for p in closed if p.status == "CLOSED_SL"]

        total_pnl = sum(p.pnl for p in closed)
        win_pnl = sum(p.pnl for p in wins)
        loss_pnl = sum(p.pnl for p in losses)

        win_rate = len(wins) / len(closed) * 100 if closed else 0.0

        ema20_closed = [p for p in closed if p.strategy == "EMA20"]
        ema50_closed = [p for p in closed if p.strategy == "EMA50"]

        summary = {
            "total_candles": len(self._candles),
            "total_trades_closed": len(closed),
            "trades_open_at_end": len(open_pos),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(win_rate, 2),
            "total_pnl": round(total_pnl, 4),
            "win_pnl": round(win_pnl, 4),
            "loss_pnl": round(loss_pnl, 4),
            "final_balance": round(self._broker.balance, 4),
            "ema20_trades": len(ema20_closed),
            "ema50_trades": len(ema50_closed),
        }

        logger.info("=" * 60)
        logger.info("BACKTEST COMPLETE")
        logger.info("=" * 60)
        for k, v in summary.items():
            logger.info("  %-30s %s", k, v)
        logger.info("=" * 60)

        return summary


# ---------------------------------------------------------------------------
# Convenience loader
# ---------------------------------------------------------------------------

def load_candles_from_api(
    days: int = 7,
    client: TwelveDataClient | None = None,
) -> list[Candle]:
    """
    Fetch historical candles from Twelve Data REST API.

    Parameters
    ----------
    days : int
        Approximate number of calendar days of 1-minute data to fetch.
        Each trading day has ~1440 candles; adjust outputsize accordingly.
    """
    client = client or TwelveDataClient()
    outputsize = min(days * 1440, config.HISTORICAL_OUTPUTSIZE)

    raw = client.get_time_series(outputsize=outputsize)

    candles = []
    for r in raw:
        try:
            dt = datetime.strptime(r["datetime"], "%Y-%m-%d %H:%M:%S")
            dt = _IST.localize(dt)
        except ValueError:
            dt = datetime.fromisoformat(r["datetime"])
            if dt.tzinfo is None:
                dt = _IST.localize(dt)

        candles.append(Candle(
            timestamp=dt,
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
        ))

    return candles


def load_candles_from_df(df: pd.DataFrame) -> list[Candle]:
    """
    Convert a pandas DataFrame to a list of Candle objects.

    Expected columns: datetime, open, high, low, close
    """
    candles = []
    for _, row in df.iterrows():
        dt = row["datetime"]
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        if isinstance(dt, pd.Timestamp):
            dt = dt.to_pydatetime()
        if dt.tzinfo is None:
            dt = _IST.localize(dt)

        candles.append(Candle(
            timestamp=dt,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
        ))
    return candles
