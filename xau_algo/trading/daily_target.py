"""
xau_algo/trading/daily_target.py
=================================
Daily P&L target guard (account-level, both strategies combined).

Rules
-----
- Daily target = INITIAL_CAPITAL * DAILY_TARGET_PERCENT / 100
- Once daily_pnl >= daily_target → block all new entries
- Existing open positions are NOT touched (they continue to SL/TP)
- At session start (03:45 IST) each trading day → reset
"""

from __future__ import annotations

import logging

from xau_algo import config

logger = logging.getLogger(__name__)


class DailyTargetGuard:
    """
    Tracks whether the daily profit target has been reached and
    blocks new trade entries when it has.
    """

    def __init__(
        self,
        initial_capital: float | None = None,
        daily_target_pct: float | None = None,
    ) -> None:
        self._capital = initial_capital or config.INITIAL_CAPITAL
        self._target_pct = daily_target_pct or config.DAILY_TARGET_PERCENT
        self._daily_target: float = self._capital * self._target_pct / 100.0
        self._target_reached: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def daily_target(self) -> float:
        """Dollar amount that triggers the halt."""
        return self._daily_target

    @property
    def target_reached(self) -> bool:
        return self._target_reached

    def is_entry_allowed(self, current_daily_pnl: float) -> bool:
        """
        Return True if new entries are permitted.

        Parameters
        ----------
        current_daily_pnl : float
            broker.daily_pnl — realised P&L for the current session.
        """
        if self._target_reached:
            return False

        if current_daily_pnl >= self._daily_target:
            self._target_reached = True
            logger.warning(
                "Daily target reached: %.4f >= %.4f — new entries HALTED for today.",
                current_daily_pnl,
                self._daily_target,
            )
            return False

        return True

    def reset(self, current_balance: float | None = None) -> None:
        """
        Reset for the new trading day.

        If current_balance is provided, the daily target is recalculated
        based on the updated account balance (compound growth).
        """
        if current_balance is not None:
            self._capital = current_balance
            self._daily_target = self._capital * self._target_pct / 100.0

        self._target_reached = False
        logger.info(
            "Daily target guard reset. New daily target: $%.2f (%.1f%% of $%.2f)",
            self._daily_target,
            self._target_pct,
            self._capital,
        )
