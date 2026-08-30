"""
xau_algo/tests/test_ema50_strategy.py
=======================================
Unit tests for EMA50Strategy — COMPLETELY INDEPENDENT from EMA20Strategy.

EMA20Strategy is never imported or referenced here.
These tests mirror the EMA20 test suite but use EMA50 with period=50.

Tested behaviours (identical structure to EMA20 tests):
  - Valid BUY setup (EMA50 touch + close above)
  - Invalid BUY setup (wrong direction)
  - Valid SELL setup (EMA50 touch + close below)
  - Invalid SELL setup (wrong direction)
  - Wick-only touch
  - Body touch
  - Close exactly on EMA50 → NO setup
  - Breakout on next candle → trade
  - No breakout on next candle → setup expires
  - Setup expires (cannot trigger on N+2)
  - Repeated BUY signals
  - Repeated SELL signals
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

with patch.dict(os.environ, {
    "TWELVE_DATA_API_KEY_1": "testkey1",
}):
    from xau_algo.strategies.ema50_strategy import EMA50Strategy
    from xau_algo.strategies.base_strategy import Candle
    from xau_algo.trading.paper_broker import PaperBroker
    from xau_algo.trading.daily_target import DailyTargetGuard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(minute: int = 0) -> datetime:
    """Create a UTC timestamp. minute can exceed 59 (wraps into hours)."""
    h, m = divmod(minute, 60)
    return datetime(2026, 1, 1, h, m, 0, tzinfo=timezone.utc)


def _make_strategy():
    broker = MagicMock(spec=PaperBroker)
    broker.daily_pnl = 0.0
    broker.open_position = MagicMock()

    daily_guard = MagicMock(spec=DailyTargetGuard)
    daily_guard.is_entry_allowed.return_value = True

    strat = EMA50Strategy(broker=broker, daily_guard=daily_guard)
    return strat, broker, daily_guard


def _warm_up(strat: EMA50Strategy, n: int = 50, base_price: float = 3000.0) -> None:
    """Feed n candles that do NOT form a setup."""
    for i in range(n):
        price = base_price + i * 0.01
        c = Candle(
            timestamp=_ts(i),
            open=price,
            high=price + 0.01,
            low=price - 0.01,
            close=price,
        )
        strat.on_candle_closed(c)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEMA50BuySetup:

    def test_valid_buy_setup(self):
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=50)

        ema = strat.current_ema
        assert ema is not None

        candle = Candle(
            timestamp=_ts(55),
            open=ema + 1.0,
            high=ema + 5.0,
            low=ema - 1.0,
            close=ema + 2.0,
        )
        strat.on_candle_closed(candle)

        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "BUY"
        assert strat.strategy_name == "EMA50"

    def test_invalid_buy_no_touch(self):
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=50)

        ema = strat.current_ema
        assert ema is not None

        candle = Candle(
            timestamp=_ts(55),
            open=ema + 10.0,
            high=ema + 15.0,
            low=ema + 5.0,   # entirely above EMA
            close=ema + 12.0,
        )
        strat.on_candle_closed(candle)
        assert not strat.has_pending_setup

    def test_close_exactly_on_ema50_no_setup(self):
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=50)

        ema = strat.current_ema
        assert ema is not None

        candle = Candle(
            timestamp=_ts(55),
            open=ema - 1.0,
            high=ema + 2.0,
            low=ema - 2.0,
            close=ema,   # exactly on EMA50
        )
        strat.on_candle_closed(candle)
        assert not strat.has_pending_setup


class TestEMA50SellSetup:

    def test_valid_sell_setup(self):
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=50)

        ema = strat.current_ema
        assert ema is not None

        candle = Candle(
            timestamp=_ts(55),
            open=ema - 1.0,
            high=ema + 2.0,
            low=ema - 4.0,
            close=ema - 2.0,
        )
        strat.on_candle_closed(candle)

        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "SELL"

    def test_invalid_sell_wrong_direction(self):
        """close > EMA → should be BUY setup, not SELL."""
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=50)

        ema = strat.current_ema
        assert ema is not None

        candle = Candle(
            timestamp=_ts(55),
            open=ema,
            high=ema + 3.0,
            low=ema - 1.0,
            close=ema + 2.0,
        )
        strat.on_candle_closed(candle)
        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "BUY"


class TestEMA50TouchVariants:

    def test_wick_only_touch_buy(self):
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=50)
        ema = strat.current_ema

        candle = Candle(
            timestamp=_ts(55),
            open=ema + 2.0,
            high=ema + 5.0,
            low=ema - 1.0,   # lower wick touches EMA
            close=ema + 3.0,
        )
        strat.on_candle_closed(candle)
        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "BUY"

    def test_body_touch_sell(self):
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=50)
        ema = strat.current_ema

        candle = Candle(
            timestamp=_ts(55),
            open=ema + 1.0,
            high=ema + 3.0,
            low=ema - 3.0,
            close=ema - 1.0,   # body crosses EMA
        )
        strat.on_candle_closed(candle)
        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "SELL"


class TestEMA50Breakout:

    def _buy_setup(self):
        strat, broker, daily_guard = _make_strategy()
        _warm_up(strat, n=50)
        ema = strat.current_ema

        setup = Candle(
            timestamp=_ts(55),
            open=ema + 1.0,
            high=ema + 5.0,
            low=ema - 1.0,
            close=ema + 2.0,
        )
        strat.on_candle_closed(setup)
        assert strat.has_pending_setup
        return strat, broker, ema

    def test_buy_breakout_fires(self):
        strat, broker, ema = self._buy_setup()
        setup_high = strat.pending_setup.setup_high

        next_c = Candle(
            timestamp=_ts(56),
            open=ema + 3.0,
            high=setup_high + 1.0,
            low=ema + 1.0,
            close=ema + 4.0,
        )
        strat.on_candle_closed(next_c)

        assert broker.open_position.call_count == 3
        assert not strat.has_pending_setup

    def test_buy_no_breakout_expires(self):
        strat, broker, ema = self._buy_setup()
        setup_high = strat.pending_setup.setup_high

        next_c = Candle(
            timestamp=_ts(56),
            open=ema + 1.0,
            high=setup_high - 0.5,   # does NOT break
            low=ema,
            close=ema + 0.5,
        )
        strat.on_candle_closed(next_c)

        assert broker.open_position.call_count == 0
        assert not strat.has_pending_setup

    def test_setup_cannot_trigger_on_n_plus_2(self):
        strat, broker, ema = self._buy_setup()
        setup_high = strat.pending_setup.setup_high

        # N+1: no breakout
        strat.on_candle_closed(Candle(
            timestamp=_ts(56),
            open=ema + 1.0,
            high=setup_high - 0.5,
            low=ema,
            close=ema + 0.5,
        ))
        assert not strat.has_pending_setup

        # N+2: breaks old setup_high — must NOT fire old trade
        strat.on_candle_closed(Candle(
            timestamp=_ts(57),
            open=ema + 2.0,
            high=setup_high + 10.0,
            low=ema + 1.0,
            close=setup_high + 5.0,
        ))
        assert broker.open_position.call_count == 0


class TestEMA50RepeatedSignals:

    def test_multiple_buy_signals(self):
        strat, broker, _ = _make_strategy()
        _warm_up(strat, n=50)
        total = 0

        for i in range(3):
            ema = strat.current_ema
            if ema is None:
                continue
            setup = Candle(_ts(55 + i * 3), ema + 1.0, ema + 5.0, ema - 1.0, ema + 2.0)
            strat.on_candle_closed(setup)
            if strat.has_pending_setup:
                sh = strat.pending_setup.setup_high
                strat.on_candle_closed(Candle(_ts(56 + i * 3), ema + 3.0, sh + 1.0, ema + 1.0, ema + 4.0))
                total += 3

        assert broker.open_position.call_count == total

    def test_multiple_sell_signals(self):
        strat, broker, _ = _make_strategy()
        _warm_up(strat, n=50)
        total = 0

        for i in range(3):
            ema = strat.current_ema
            if ema is None:
                continue
            setup = Candle(_ts(55 + i * 3), ema - 1.0, ema + 2.0, ema - 4.0, ema - 2.0)
            strat.on_candle_closed(setup)
            if strat.has_pending_setup:
                sl = strat.pending_setup.setup_low
                strat.on_candle_closed(Candle(_ts(56 + i * 3), ema - 2.0, ema - 0.5, sl - 1.0, ema - 3.0))
                total += 3

        assert broker.open_position.call_count == total
