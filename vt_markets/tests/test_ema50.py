"""
vt_markets/tests/test_ema50.py
================================
Unit tests for VT Markets EMA50Strategy.
Completely independent from EMA20 tests.
EMA20 is never referenced here.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from vt_markets.strategies.ema50_strategy import EMA50Strategy
from vt_markets.models.candle import Candle
from vt_markets.models.tick import Tick


# ---------------------------------------------------------------------------
# Helpers (identical structure to test_ema20.py but uses EMA50)
# ---------------------------------------------------------------------------

def _ts(n: int = 0) -> datetime:
    h, m = divmod(n, 60)
    return datetime(2026, 1, 1, h, m, 0, tzinfo=timezone.utc)


def _candle(close: float, high: float, low: float, n: int = 0) -> Candle:
    return Candle(timestamp=_ts(n), open=close, high=high, low=low, close=close)


def _tick(bid: float, ask: float) -> Tick:
    return Tick(
        timestamp=datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc),
        bid=bid, ask=ask,
    )


def _make_strategy():
    signals = []
    guard = MagicMock()
    guard.is_entry_allowed.return_value = True
    strat = EMA50Strategy(on_signal=signals.append, daily_guard=guard)
    return strat, signals, guard


def _warm_up(strat, n=50, base=3000.0):
    for i in range(n):
        price = base + i * 0.01
        strat.on_candle_closed(_candle(price, price + 0.01, price - 0.01, n=i))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEMA50BuySetup:

    def test_valid_buy_setup(self):
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=50)
        ema = strat.current_ema
        assert ema is not None

        candle = _candle(ema + 2.0, ema + 5.0, ema - 1.0, n=55)
        strat.on_candle_closed(candle)
        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "BUY"
        assert strat.strategy_name == "EMA50"

    def test_no_ema_touch_no_setup(self):
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=50)
        ema = strat.current_ema

        candle = _candle(ema + 10.0, ema + 15.0, ema + 5.0, n=55)
        strat.on_candle_closed(candle)
        assert not strat.has_pending_setup

    def test_close_exactly_on_ema50_no_setup(self):
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=50)
        ema = strat.current_ema

        candle = _candle(ema, ema + 2.0, ema - 2.0, n=55)
        strat.on_candle_closed(candle)
        assert not strat.has_pending_setup

    def test_magic_is_ema50_magic(self):
        from vt_markets import config
        strat, _, _ = _make_strategy()
        assert strat.magic == config.EMA50_MAGIC

    def test_ema50_independent_of_ema20(self):
        """EMA50Strategy must NOT import or call EMA20 functionality."""
        import re
        import vt_markets.strategies.ema50_strategy as m
        import vt_markets.strategies.base_strategy as base

        # Check that the class body (not docstrings) has no EMA20 magic number
        from vt_markets import config
        src = m.EMA50Strategy.__module__
        # Key invariant: the EMA50 magic and period are EMA50-specific
        assert m.EMA50Strategy.ema_period == config.EMA50_PERIOD
        assert m.EMA50Strategy.magic == config.EMA50_MAGIC
        assert m.EMA50Strategy.strategy_name == "EMA50"
        # Ensure EMA20's magic/period constants are NOT hard-coded in the class
        assert not hasattr(m.EMA50Strategy, 'ema20_period')
        # Ensure the class does not reference EMA20's magic number literal
        import inspect
        class_src = inspect.getsource(m.EMA50Strategy)
        assert str(config.EMA20_MAGIC) not in class_src


class TestEMA50SellSetup:

    def test_valid_sell_setup(self):
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=50)
        ema = strat.current_ema

        candle = _candle(ema - 2.0, ema + 1.0, ema - 5.0, n=55)
        strat.on_candle_closed(candle)
        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "SELL"


class TestEMA50TouchVariants:

    def test_wick_touch_buy(self):
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=50)
        ema = strat.current_ema

        candle = _candle(ema + 2.0, ema + 4.0, ema - 1.0, n=55)
        strat.on_candle_closed(candle)
        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "BUY"

    def test_body_touch_sell(self):
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=50)
        ema = strat.current_ema

        candle = Candle(_ts(55), ema + 1.0, ema + 2.0, ema - 3.0, ema - 1.0)
        strat.on_candle_closed(candle)
        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "SELL"


class TestEMA50OHLCBreakout:

    def _buy_setup(self):
        strat, signals, _ = _make_strategy()
        _warm_up(strat, n=50)
        ema = strat.current_ema
        setup = _candle(ema + 2.0, ema + 5.0, ema - 1.0, n=55)
        strat.on_candle_closed(setup)
        return strat, signals, ema

    def test_buy_breakout_fires(self):
        strat, signals, ema = self._buy_setup()
        sh = strat.pending_setup.setup_high
        strat.on_candle_closed(_candle(sh + 2.0, sh + 3.0, ema + 1.0, n=56))
        assert len(signals) == 1
        assert signals[0].direction == "BUY"
        assert signals[0].strategy == "EMA50"

    def test_buy_no_breakout_expires(self):
        strat, signals, ema = self._buy_setup()
        sh = strat.pending_setup.setup_high
        strat.on_candle_closed(_candle(ema + 3.0, sh - 0.5, ema + 1.0, n=56))
        assert len(signals) == 0
        assert not strat.has_pending_setup

    def test_setup_cannot_trigger_on_n_plus_2(self):
        strat, signals, ema = self._buy_setup()
        sh = strat.pending_setup.setup_high

        strat.on_candle_closed(_candle(ema + 1.0, sh - 0.5, ema, n=56))
        assert not strat.has_pending_setup

        strat.on_candle_closed(_candle(sh + 5.0, sh + 10.0, ema + 1.0, n=57))
        assert len(signals) == 0


class TestEMA50LiveBreakout:

    def test_buy_live_tick_breakout(self):
        strat, signals, _ = _make_strategy()
        _warm_up(strat, n=50)
        ema = strat.current_ema

        strat.on_candle_closed(_candle(ema + 2.0, ema + 5.0, ema - 1.0, n=55))
        sh = strat.pending_setup.setup_high

        strat.on_tick(_tick(bid=sh + 0.2, ask=sh + 0.5))
        assert len(signals) == 1
        assert signals[0].direction == "BUY"
        assert signals[0].entry_price == pytest.approx(sh + 0.5)

    def test_sell_live_tick_breakout(self):
        strat, signals, _ = _make_strategy()
        _warm_up(strat, n=50)
        ema = strat.current_ema

        strat.on_candle_closed(_candle(ema - 2.0, ema + 1.0, ema - 5.0, n=55))
        sl = strat.pending_setup.setup_low

        strat.on_tick(_tick(bid=sl - 0.5, ask=sl - 0.2))
        assert len(signals) == 1
        assert signals[0].direction == "SELL"
        assert signals[0].entry_price == pytest.approx(sl - 0.5)


class TestEMA50MultipleSignals:

    def test_multiple_buy_signals(self):
        strat, signals, _ = _make_strategy()
        _warm_up(strat, n=50)
        total = 0

        for i in range(3):
            ema = strat.current_ema
            if ema is None:
                continue
            strat.on_candle_closed(_candle(ema + 2.0, ema + 5.0, ema - 1.0, n=55 + i * 3))
            if strat.has_pending_setup:
                sh = strat.pending_setup.setup_high
                strat.on_candle_closed(_candle(sh + 2.0, sh + 3.0, ema + 1.0, n=56 + i * 3))
                total += 1

        assert len(signals) == total
        assert total > 0

    def test_multiple_sell_signals(self):
        strat, signals, _ = _make_strategy()
        _warm_up(strat, n=50)
        total = 0

        for i in range(3):
            ema = strat.current_ema
            if ema is None:
                continue
            strat.on_candle_closed(_candle(ema - 2.0, ema + 1.0, ema - 5.0, n=55 + i * 3))
            if strat.has_pending_setup:
                sl = strat.pending_setup.setup_low
                strat.on_candle_closed(_candle(sl - 3.0, ema, sl - 5.0, n=56 + i * 3))
                total += 1

        assert len(signals) == total
        assert total > 0
