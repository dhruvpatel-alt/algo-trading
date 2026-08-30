"""
vt_markets/strategies/base_strategy.py
========================================
Abstract base for EMA20Strategy and EMA50Strategy in the VT Markets implementation.

This is INDEPENDENT of xau_algo/strategies/base_strategy.py.
No Twelve Data imports. No shared state with xau_algo.

State machine (per strategy instance):

    SCANNING
        │  setup candle detected (touch + close side)
        ▼
    WAITING_FOR_BREAKOUT
        │                     │
        │ breakout confirmed   │ next candle closes without breakout
        ▼                     ▼
    emits Signal           SCANNING  (setup expired)
        │
    SCANNING

Key rules:
  - Setup expires after ONLY ONE next candle (strict)
  - Breakout detected live from tick data (price crosses level)
  - Each strategy has its own independent state — they NEVER share state
  - No EMA crossover or relationship between EMA20 and EMA50
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from vt_markets.models.candle import Candle
    from vt_markets.models.tick import Tick
    from vt_markets.models.signal import Signal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal state dataclass
# ---------------------------------------------------------------------------

@dataclass
class PendingSetup:
    """State held between setup candle and breakout candle."""
    direction: str         # "BUY" | "SELL"
    setup_high: float
    setup_low: float
    setup_time: datetime
    ema_at_setup: float
    next_candle_started: bool = False   # True once we see the first tick of N+1


# ---------------------------------------------------------------------------
# Base strategy
# ---------------------------------------------------------------------------

class BaseVTStrategy:
    """
    Abstract base for EMA-touch-breakout strategies in VT Markets.

    Subclasses must set:
        ema_period   : int
        strategy_name: str
        magic        : int
    """

    ema_period: int = 0
    strategy_name: str = ""
    magic: int = 0

    def __init__(
        self,
        on_signal: Callable[["Signal"], None],
        daily_guard: "DailyTargetGuard",          # noqa: F821
    ) -> None:
        if self.ema_period == 0:
            raise NotImplementedError("Subclass must set ema_period")

        self._on_signal = on_signal
        self._daily_guard = daily_guard

        # Rolling close prices for incremental EMA
        self._closes: list[float] = []
        self._ema_values: list[float | None] = []

        # Pending setup (None when SCANNING)
        self._pending: PendingSetup | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_candle_closed(self, candle: "Candle") -> None:
        """
        Called when a 1-minute candle closes.

        1. Update EMA.
        2. If a setup is pending and this is the ONE breakout candle
           (next_candle_started=False in OHLC mode), evaluate it.
        3. If a setup is pending and we already started the breakout candle
           (live tick mode), the candle closed without breakout → expire.
        4. Scan closed candle for a fresh setup.
        """
        # Step 1 — EMA update
        self._closes.append(candle.close)
        ema = self._compute_latest_ema()
        self._ema_values.append(ema)

        _skip_scan = False

        # Step 2 — handle pending setup
        if self._pending is not None:
            if not self._pending.next_candle_started:
                # Historical / OHLC path: this is the ONE breakout candle
                self._check_ohlc_breakout(candle)
                self._pending = None
                _skip_scan = True  # this candle was the breakout candle
            else:
                # Live path: ticks were processed but breakout didn't fire
                logger.info(
                    "%s | %s %s setup EXPIRED — candle closed without breakout | "
                    "setup_high=%.5f setup_low=%.5f",
                    candle.timestamp,
                    self.strategy_name,
                    self._pending.direction,
                    self._pending.setup_high,
                    self._pending.setup_low,
                )
                self._pending = None
                _skip_scan = True

        # Step 3 — scan for new setup
        if ema is not None and self._pending is None and not _skip_scan:
            self._detect_setup(candle, ema)

    def on_tick(self, tick: "Tick") -> None:
        """
        Called on every live price tick.
        Used for immediate breakout detection without waiting for candle close.
        """
        if self._pending is None:
            return

        self._pending.next_candle_started = True
        mid = tick.mid    # use mid-price for setup detection

        if self._pending.direction == "BUY" and mid > self._pending.setup_high:
            logger.info(
                "%s | %s BUY live breakout | price=%.5f > setup_high=%.5f",
                tick.timestamp, self.strategy_name, mid, self._pending.setup_high,
            )
            self._emit_signal("BUY", tick.ask, tick.timestamp)  # BUY at ASK
            self._pending = None

        elif self._pending.direction == "SELL" and mid < self._pending.setup_low:
            logger.info(
                "%s | %s SELL live breakout | price=%.5f < setup_low=%.5f",
                tick.timestamp, self.strategy_name, mid, self._pending.setup_low,
            )
            self._emit_signal("SELL", tick.bid, tick.timestamp)  # SELL at BID
            self._pending = None

    # ------------------------------------------------------------------
    # Internal — setup detection
    # ------------------------------------------------------------------

    def _detect_setup(self, candle: "Candle", ema: float) -> None:
        """
        BUY setup:   close > ema  AND  low <= ema <= high
        SELL setup:  close < ema  AND  low <= ema <= high
        close == ema → NO setup (strict inequality)
        """
        if not (candle.low <= ema <= candle.high):
            return

        if candle.close > ema:
            direction = "BUY"
        elif candle.close < ema:
            direction = "SELL"
        else:
            return   # close exactly on EMA — no setup

        self._pending = PendingSetup(
            direction=direction,
            setup_high=candle.high,
            setup_low=candle.low,
            setup_time=candle.timestamp,
            ema_at_setup=ema,
        )

        logger.info(
            "%s | %s %s setup detected | "
            "high=%.5f low=%.5f close=%.5f EMA%d=%.5f",
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

    def _check_ohlc_breakout(self, next_candle: "Candle") -> None:
        """
        Check if next_candle's OHLC confirms the breakout (historical mode).
        Entry is modelled at the breakout level (setup_high / setup_low).
        """
        assert self._pending is not None

        if self._pending.direction == "BUY":
            if next_candle.high > self._pending.setup_high:
                logger.info(
                    "%s | %s BUY OHLC breakout | next_high=%.5f > setup_high=%.5f",
                    next_candle.timestamp, self.strategy_name,
                    next_candle.high, self._pending.setup_high,
                )
                self._emit_signal("BUY", self._pending.setup_high, next_candle.timestamp)
            else:
                logger.info(
                    "%s | %s BUY setup EXPIRED | "
                    "next_high=%.5f ≤ setup_high=%.5f",
                    next_candle.timestamp, self.strategy_name,
                    next_candle.high, self._pending.setup_high,
                )
        else:
            if next_candle.low < self._pending.setup_low:
                logger.info(
                    "%s | %s SELL OHLC breakout | next_low=%.5f < setup_low=%.5f",
                    next_candle.timestamp, self.strategy_name,
                    next_candle.low, self._pending.setup_low,
                )
                self._emit_signal("SELL", self._pending.setup_low, next_candle.timestamp)
            else:
                logger.info(
                    "%s | %s SELL setup EXPIRED | "
                    "next_low=%.5f ≥ setup_low=%.5f",
                    next_candle.timestamp, self.strategy_name,
                    next_candle.low, self._pending.setup_low,
                )

    # ------------------------------------------------------------------
    # Internal — emit signal
    # ------------------------------------------------------------------

    def _emit_signal(
        self, direction: str, entry_price: float, signal_time: datetime
    ) -> None:
        """Build a Signal and fire the on_signal callback."""
        from vt_markets.models.signal import Signal
        from vt_markets.trading.daily_target import VTDailyTargetGuard

        assert self._pending is not None

        if not self._daily_guard.is_entry_allowed():
            logger.warning(
                "%s | %s %s signal BLOCKED — daily target reached.",
                signal_time, self.strategy_name, direction,
            )
            return

        if direction == "BUY":
            sl = self._pending.setup_low
        else:
            sl = self._pending.setup_high

        signal = Signal(
            strategy=self.strategy_name,
            direction=direction,
            entry_price=entry_price,
            stop_loss=sl,
            setup_high=self._pending.setup_high,
            setup_low=self._pending.setup_low,
            setup_time=self._pending.setup_time,
            signal_time=signal_time,
            magic=self.magic,
        )
        self._on_signal(signal)

    # ------------------------------------------------------------------
    # Internal — incremental EMA
    # ------------------------------------------------------------------

    def _compute_latest_ema(self) -> float | None:
        n = len(self._closes)
        if n < self.ema_period:
            return None
        if n == self.ema_period:
            seed = sum(self._closes) / self.ema_period
            return seed
        prev = self._ema_values[-1]
        if prev is None:
            seed = sum(self._closes[-self.ema_period:]) / self.ema_period
            k = 2.0 / (self.ema_period + 1)
            return self._closes[-1] * k + seed * (1.0 - k)
        k = 2.0 / (self.ema_period + 1)
        return self._closes[-1] * k + prev * (1.0 - k)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @property
    def current_ema(self) -> float | None:
        return self._ema_values[-1] if self._ema_values else None

    @property
    def has_pending_setup(self) -> bool:
        return self._pending is not None

    @property
    def pending_setup(self) -> PendingSetup | None:
        return self._pending
