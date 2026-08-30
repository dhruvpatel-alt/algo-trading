"""
xau_algo/strategies/base_strategy.py
=====================================
Abstract base class shared by EMA20Strategy and EMA50Strategy.

State machine (per strategy instance, fully independent)
---------------------------------------------------------

    SCANNING
       │
       │  candle satisfies setup conditions
       ▼
    WAITING_FOR_BREAKOUT
       │                  │
       │ price breaks      │ next candle closes without breakout
       │ setup level       │
       ▼                  ▼
    (fires signal)     SCANNING   ← setup expired
       │
    SCANNING

The base class handles:
    - EMA series management (incremental update on each candle)
    - Setup detection logic (touch + close-side check)
    - Breakout detection (live tick and historical OHLC)
    - Position creation (3 lots per signal)
    - Logging

Subclasses only need to supply:
    - ema_period    (int)
    - strategy_name (str)  e.g. "EMA20" or "EMA50"
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, TYPE_CHECKING

from xau_algo import config
from xau_algo.trading.position import Position

if TYPE_CHECKING:
    from xau_algo.trading.paper_broker import PaperBroker
    from xau_algo.trading.daily_target import DailyTargetGuard
    from xau_algo.storage.supabase_repository import SupabaseRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------

@dataclass
class Candle:
    """Minimal representation of a completed 1-minute OHLC candle."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class SetupState:
    """Pending setup waiting for the immediately next candle's breakout."""
    direction: str          # "BUY" | "SELL"
    setup_high: float
    setup_low: float
    setup_time: datetime
    ema_at_setup: float
    # True once we have consumed the one allowed breakout candle
    consumed: bool = False


# ---------------------------------------------------------------------------
# Base strategy
# ---------------------------------------------------------------------------

class BaseEMAStrategy:
    """
    Abstract base for EMA-touch-breakout strategies.

    Subclasses must set:
        ema_period    : int
        strategy_name : str
    """

    ema_period: int = 0
    strategy_name: str = ""

    def __init__(
        self,
        broker: "PaperBroker",
        daily_guard: "DailyTargetGuard",
        supabase_repo: "SupabaseRepository | None" = None,
    ) -> None:
        if self.ema_period == 0:
            raise NotImplementedError("Subclass must set ema_period")

        self._broker = broker
        self._daily_guard = daily_guard
        self._supabase = supabase_repo

        # Rolling close prices for EMA calculation
        self._closes: list[float] = []
        self._ema_values: list[float | None] = []

        # Current pending setup (None when in SCANNING state)
        self._pending_setup: SetupState | None = None

        # Track whether the immediately-next candle has been observed
        self._next_candle_seen: bool = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def on_candle_closed(self, candle: Candle) -> None:
        """
        Called when a 1-minute candle closes.

        Steps:
        1. Update EMA series.
        2. If we were WAITING_FOR_BREAKOUT → this is the "next candle".
           Check its OHLC for a breakout (historical / end-of-candle fallback).
           After this check, the setup expires regardless of outcome.
        3. Check if this newly closed candle is a fresh setup candle.
        """
        # Step 1 — update EMA
        self._closes.append(candle.close)
        ema = self._compute_latest_ema()
        self._ema_values.append(ema)

        # Step 2 — handle pending setup expiry / OHLC breakout check
        _skip_new_setup_scan = False

        if self._pending_setup is not None and not self._next_candle_seen:
            # This is the ONE allowed breakout candle (historical OHLC path).
            # After this check the setup is consumed — regardless of outcome.
            self._check_ohlc_breakout(candle, ema)
            self._pending_setup = None
            # This candle was used as the breakout candle; do NOT also treat it
            # as a new setup candle in the same pass.
            _skip_new_setup_scan = True

        elif self._pending_setup is not None and self._next_candle_seen:
            # Live path: the breakout candle already started (ticks processed).
            # The candle has now closed WITHOUT a breakout (live tick would have
            # fired and cleared _pending_setup). Expire the setup.
            logger.info(
                "%s | %s %s setup EXPIRED — next candle closed without breakout | "
                "setup_high=%.5f | setup_low=%.5f",
                candle.timestamp,
                self.strategy_name,
                self._pending_setup.direction,
                self._pending_setup.setup_high,
                self._pending_setup.setup_low,
            )
            self._pending_setup = None
            self._next_candle_seen = False
            _skip_new_setup_scan = True

        # Step 3 — scan this candle for a new setup (only if not the breakout candle)
        if ema is not None and self._pending_setup is None and not _skip_new_setup_scan:
            self._detect_setup(candle, ema)

    def on_price_tick(self, price: float, timestamp: datetime) -> None:
        """
        Called on every live price tick WHILE a setup is pending.

        This is the live breakout detection path — fires immediately
        when price crosses the setup level, without waiting for candle close.
        """
        if self._pending_setup is None:
            return

        setup = self._pending_setup
        self._next_candle_seen = True  # we are now inside the breakout candle

        if setup.direction == "BUY" and price > setup.setup_high:
            logger.info(
                "%s | %s BUY breakout | price=%.5f > setup_high=%.5f",
                timestamp,
                self.strategy_name,
                price,
                setup.setup_high,
            )
            self._fire_signal(
                direction="BUY",
                entry_price=setup.setup_high,   # entry AT breakout level
                stop_loss=setup.setup_low,
                setup_time=setup.setup_time,
                entry_time=timestamp,
            )
            self._pending_setup = None
            self._next_candle_seen = False

        elif setup.direction == "SELL" and price < setup.setup_low:
            logger.info(
                "%s | %s SELL breakout | price=%.5f < setup_low=%.5f",
                timestamp,
                self.strategy_name,
                price,
                setup.setup_low,
            )
            self._fire_signal(
                direction="SELL",
                entry_price=setup.setup_low,    # entry AT breakout level
                stop_loss=setup.setup_high,
                setup_time=setup.setup_time,
                entry_time=timestamp,
            )
            self._pending_setup = None
            self._next_candle_seen = False

    # ------------------------------------------------------------------
    # Internal — setup detection
    # ------------------------------------------------------------------

    def _detect_setup(self, candle: Candle, ema: float) -> None:
        """
        Check if the closed candle qualifies as a BUY or SELL setup.

        BUY conditions (BOTH required):
            close > ema          (strict)
            low   <= ema <= high

        SELL conditions (BOTH required):
            close < ema          (strict)
            low   <= ema <= high
        """
        touches = candle.low <= ema <= candle.high

        if not touches:
            return

        if candle.close > ema:
            direction = "BUY"
        elif candle.close < ema:
            direction = "SELL"
        else:
            # close == ema → no setup (strict inequality required by spec)
            return

        self._pending_setup = SetupState(
            direction=direction,
            setup_high=candle.high,
            setup_low=candle.low,
            setup_time=candle.timestamp,
            ema_at_setup=ema,
        )

        logger.info(
            "%s | %s %s setup detected | "
            "high=%.5f | low=%.5f | close=%.5f | EMA%d=%.5f",
            candle.timestamp,
            self.strategy_name,
            direction,
            candle.high,
            candle.low,
            candle.close,
            self.ema_period,
            ema,
        )

    # ------------------------------------------------------------------
    # Internal — historical OHLC breakout check
    # ------------------------------------------------------------------

    def _check_ohlc_breakout(self, next_candle: Candle, _ema: float | None) -> None:
        """
        For historical backtesting: check if next_candle's OHLC crosses the
        setup level.  Entry is modelled at the breakout price (setup_high /
        setup_low), not at next_candle.open.

        Assumption: ENTRY_AT_BREAKOUT_LEVEL = True (configurable in config.py)
        """
        setup = self._pending_setup
        assert setup is not None

        if setup.direction == "BUY":
            if next_candle.high > setup.setup_high:
                logger.info(
                    "%s | %s BUY breakout (OHLC) | next_high=%.5f > setup_high=%.5f",
                    next_candle.timestamp,
                    self.strategy_name,
                    next_candle.high,
                    setup.setup_high,
                )
                entry = setup.setup_high if config.ENTRY_AT_BREAKOUT_LEVEL else next_candle.open
                self._fire_signal(
                    direction="BUY",
                    entry_price=entry,
                    stop_loss=setup.setup_low,
                    setup_time=setup.setup_time,
                    entry_time=next_candle.timestamp,
                )
            else:
                logger.info(
                    "%s | %s BUY setup EXPIRED — next_high=%.5f did not exceed setup_high=%.5f",
                    next_candle.timestamp,
                    self.strategy_name,
                    next_candle.high,
                    setup.setup_high,
                )

        else:  # SELL
            if next_candle.low < setup.setup_low:
                logger.info(
                    "%s | %s SELL breakout (OHLC) | next_low=%.5f < setup_low=%.5f",
                    next_candle.timestamp,
                    self.strategy_name,
                    next_candle.low,
                    setup.setup_low,
                )
                entry = setup.setup_low if config.ENTRY_AT_BREAKOUT_LEVEL else next_candle.open
                self._fire_signal(
                    direction="SELL",
                    entry_price=entry,
                    stop_loss=setup.setup_high,
                    setup_time=setup.setup_time,
                    entry_time=next_candle.timestamp,
                )
            else:
                logger.info(
                    "%s | %s SELL setup EXPIRED — next_low=%.5f did not break setup_low=%.5f",
                    next_candle.timestamp,
                    self.strategy_name,
                    next_candle.low,
                    setup.setup_low,
                )

    # ------------------------------------------------------------------
    # Internal — fire signal (create 3 positions)
    # ------------------------------------------------------------------

    def _fire_signal(
        self,
        direction: str,
        entry_price: float,
        stop_loss: float,
        setup_time: datetime,
        entry_time: datetime,
    ) -> None:
        """
        Create exactly THREE positions for every valid breakout signal.

        Lot / RR tiers (from config):
            0.06 lots  ->  1.0 R
            0.04 lots  ->  2.0 R
            0.02 lots  ->  2.5 R
        """
        if not self._daily_guard.is_entry_allowed(self._broker.daily_pnl):
            logger.warning(
                "%s | %s %s signal BLOCKED — daily target already reached.",
                entry_time,
                self.strategy_name,
                direction,
            )
            return

        # Persist the signal to Supabase (one record per signal, not per position)
        signal_id = str(uuid.uuid4())
        if self._supabase is not None:
            self._supabase.save_signal(
                signal_id=signal_id,
                strategy=self.strategy_name,
                direction=direction,
                entry_price=entry_price,
                stop_loss=stop_loss,
                setup_time=setup_time,
                signal_time=entry_time,
            )

        tiers = [
            (config.LOT_1, config.RR_1),
            (config.LOT_2, config.RR_2),
            (config.LOT_3, config.RR_3),
        ]

        if direction == "BUY":
            risk = entry_price - stop_loss
            if risk <= 0:
                logger.warning(
                    "%s | %s BUY signal skipped — risk <= 0 (entry=%.5f SL=%.5f)",
                    entry_time, self.strategy_name, entry_price, stop_loss,
                )
                return
            for lot, rr in tiers:
                tp = entry_price + risk * rr
                pos = Position(
                    strategy=self.strategy_name,
                    side="BUY",
                    lot=lot,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=tp,
                    risk_reward=rr,
                    setup_time=setup_time,
                    entry_time=entry_time,
                )
                self._broker.open_position(pos)

        else:  # SELL
            risk = stop_loss - entry_price
            if risk <= 0:
                logger.warning(
                    "%s | %s SELL signal skipped — risk <= 0 (entry=%.5f SL=%.5f)",
                    entry_time, self.strategy_name, entry_price, stop_loss,
                )
                return
            for lot, rr in tiers:
                tp = entry_price - risk * rr
                pos = Position(
                    strategy=self.strategy_name,
                    side="SELL",
                    lot=lot,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=tp,
                    risk_reward=rr,
                    setup_time=setup_time,
                    entry_time=entry_time,
                )
                self._broker.open_position(pos)

    # ------------------------------------------------------------------
    # Internal — EMA calculation
    # ------------------------------------------------------------------

    def _compute_latest_ema(self) -> float | None:
        """
        Compute the EMA value for the most recently appended close price.
        Uses incremental update when warmup is complete.
        """
        n = len(self._closes)
        if n < self.ema_period:
            return None

        if n == self.ema_period:
            # First valid EMA: simple mean seed
            return sum(self._closes) / self.ema_period

        # Incremental update
        prev_ema = self._ema_values[-1]  # last appended (before this call)
        if prev_ema is None:
            # Recompute from scratch if somehow not available
            from xau_algo.indicators.ema import calculate_ema
            series = calculate_ema(self._closes, self.ema_period)
            return series[-1]

        k = 2.0 / (self.ema_period + 1)
        return self._closes[-1] * k + prev_ema * (1.0 - k)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @property
    def current_ema(self) -> float | None:
        """Most recent EMA value (None during warmup)."""
        return self._ema_values[-1] if self._ema_values else None

    @property
    def has_pending_setup(self) -> bool:
        return self._pending_setup is not None

    @property
    def pending_setup(self) -> SetupState | None:
        return self._pending_setup
