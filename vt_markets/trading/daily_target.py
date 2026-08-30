"""
vt_markets/trading/daily_target.py
=====================================
Daily P&L target guard for VT Markets.

Rules:
  - Daily target = initial_capital × daily_target_pct / 100
  - Once daily_pnl >= daily_target → block ALL new entries (both strategies)
  - Existing open positions continue to SL/TP unaffected
  - At 03:45 IST each trading day → reset()
"""

from __future__ import annotations

import logging

from vt_markets import config

logger = logging.getLogger(__name__)


class VTDailyTargetGuard:
    """Account-level daily P&L halt guard."""

    def __init__(
        self,
        initial_capital: float | None = None,
        daily_target_pct: float | None = None,
    ) -> None:
        self._capital = initial_capital or config.INITIAL_CAPITAL
        self._target_pct = daily_target_pct or config.DAILY_TARGET_PERCENT
        self._daily_target: float = self._capital * self._target_pct / 100.0
        self._target_reached: bool = False
        self._daily_pnl: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_closed_pnl(self, pnl: float) -> None:
        """Add realised PnL from a closed position to the daily counter."""
        self._daily_pnl += pnl
        if not self._target_reached and self._daily_pnl >= self._daily_target:
            self._target_reached = True
            logger.warning(
                "Daily target reached: %.4f >= %.4f — new entries HALTED.",
                self._daily_pnl,
                self._daily_target,
            )

    def is_entry_allowed(self) -> bool:
        """Return True if new trade entries are permitted."""
        return not self._target_reached

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def daily_target(self) -> float:
        return self._daily_target

    @property
    def target_reached(self) -> bool:
        return self._target_reached

    def reset(self, current_balance: float | None = None) -> None:
        """
        Reset for a new trading day.
        Optionally update the capital for compound daily-target growth.
        """
        if current_balance is not None:
            self._capital = current_balance
            self._daily_target = self._capital * self._target_pct / 100.0
        self._target_reached = False
        self._daily_pnl = 0.0
        logger.info(
            "Daily target reset. New target: $%.2f (%.1f%% of $%.2f)",
            self._daily_target,
            self._target_pct,
            self._capital,
        )
