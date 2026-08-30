"""
xau_algo/tests/test_daily_target.py
=====================================
Unit tests for DailyTargetGuard.

Tests:
  - Daily P&L < 1% → entries allowed
  - Daily P&L exactly = 1% → entries blocked
  - Daily P&L > 1% → entries blocked
  - Once blocked, remains blocked for the day
  - reset() clears the block
  - reset() with new balance recalculates the target (compound growth)
  - Daily target is correctly computed from initial capital
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from unittest.mock import patch
import pytest

with patch.dict(os.environ, {"TWELVE_DATA_API_KEY_1": "testkey1"}):
    from xau_algo.trading.daily_target import DailyTargetGuard


class TestDailyTargetGuard:

    def test_initial_target_calculation(self):
        """1% of $10,000 = $100 daily target."""
        guard = DailyTargetGuard(initial_capital=10_000.0, daily_target_pct=1.0)
        assert guard.daily_target == pytest.approx(100.0)

    def test_entries_allowed_below_target(self):
        """P&L < target → new entries allowed."""
        guard = DailyTargetGuard(initial_capital=10_000.0, daily_target_pct=1.0)
        assert guard.is_entry_allowed(current_daily_pnl=50.0) is True
        assert guard.is_entry_allowed(current_daily_pnl=99.99) is True

    def test_entries_blocked_at_target(self):
        """P&L == target ($100) → entries BLOCKED."""
        guard = DailyTargetGuard(initial_capital=10_000.0, daily_target_pct=1.0)
        assert guard.is_entry_allowed(current_daily_pnl=100.0) is False

    def test_entries_blocked_above_target(self):
        """P&L > target → entries BLOCKED."""
        guard = DailyTargetGuard(initial_capital=10_000.0, daily_target_pct=1.0)
        assert guard.is_entry_allowed(current_daily_pnl=150.0) is False

    def test_remains_blocked_after_trigger(self):
        """
        Once blocked, subsequent calls with lower P&L still return False
        because target_reached flag is set.
        """
        guard = DailyTargetGuard(initial_capital=10_000.0, daily_target_pct=1.0)

        # Trigger the block
        guard.is_entry_allowed(current_daily_pnl=100.0)
        assert guard.target_reached is True

        # Even if we ask again with lower pnl, still blocked
        assert guard.is_entry_allowed(current_daily_pnl=50.0) is False

    def test_reset_clears_block(self):
        """reset() clears target_reached → entries allowed again next day."""
        guard = DailyTargetGuard(initial_capital=10_000.0, daily_target_pct=1.0)

        guard.is_entry_allowed(current_daily_pnl=100.0)
        assert guard.target_reached is True

        guard.reset()
        assert guard.target_reached is False
        assert guard.is_entry_allowed(current_daily_pnl=0.0) is True

    def test_reset_with_new_balance_compounds(self):
        """
        reset(current_balance=10_200.0) → new daily target = $102
        (1% of updated balance).
        """
        guard = DailyTargetGuard(initial_capital=10_000.0, daily_target_pct=1.0)

        guard.is_entry_allowed(current_daily_pnl=100.0)
        guard.reset(current_balance=10_200.0)

        assert guard.daily_target == pytest.approx(102.0)
        assert guard.target_reached is False

    def test_zero_pnl_always_allowed(self):
        guard = DailyTargetGuard(initial_capital=10_000.0, daily_target_pct=1.0)
        assert guard.is_entry_allowed(current_daily_pnl=0.0) is True

    def test_negative_pnl_allowed(self):
        """Negative daily P&L (drawdown day) → entries still allowed."""
        guard = DailyTargetGuard(initial_capital=10_000.0, daily_target_pct=1.0)
        assert guard.is_entry_allowed(current_daily_pnl=-50.0) is True

    def test_custom_target_percentage(self):
        """2% target on $5,000 capital = $100 target."""
        guard = DailyTargetGuard(initial_capital=5_000.0, daily_target_pct=2.0)
        assert guard.daily_target == pytest.approx(100.0)
        assert guard.is_entry_allowed(current_daily_pnl=99.99) is True
        assert guard.is_entry_allowed(current_daily_pnl=100.0) is False
