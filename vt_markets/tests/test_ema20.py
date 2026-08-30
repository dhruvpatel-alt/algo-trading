"""
vt_markets/tests/test_ema20.py
================================
Unit tests for VT Markets EMA20Strategy.

EMA50 is never referenced here.
All tests use mocked MT5 — no real orders placed.

Tests:
  - Valid BUY setup
  - Invalid BUY (no EMA touch)
  - Valid SELL setup
  - Close exactly on EMA → no setup
  - Wick touch BUY
  - Body touch SELL
  - Breakout on next candle (OHLC) → signal fired
  - No breakout on next candle → setup expires
  - Setup cannot trigger on N+2
  - Live tick BUY breakout
  - Live tick SELL breakout
  - Multiple BUY signals
  - Multiple SELL signals
  - Daily target blocks signal
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from vt_markets.strategies.ema20_strategy import EMA20Strategy
from vt_markets.models.candle import Candle
from vt_markets.models.tick import Tick
from vt_markets.models.signal import Signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(n: int = 0) -> datetime:
    h, m = divmod(n, 60)
    return datetime(2026, 1, 1, h, m, 0, tzinfo=timezone.utc)


def _candle(close: float, high: float, low: float, n: int = 0) -> Candle:
    return Candle(timestamp=_ts(n), open=close, high=high, low=low, close=close)


def _tick(bid: float, ask: float, n_sec: int = 0) -> Tick:
    ts = datetime(2026, 1, 1, 0, 1, n_sec, tzinfo=timezone.utc)
    return Tick(timestamp=ts, bid=bid, ask=ask)


def _make_strategy():
    signals = []
    guard = MagicMock()
    guard.is_entry_allowed.return_value = True
    strat = EMA20Strategy(on_signal=signals.append, daily_guard=guard)
    return strat, signals, guard


def _warm_up(strat, n=20, base=3000.0):
    for i in range(n):
        price = base + i * 0.01
        strat.on_candle_closed(_candle(price, price + 0.01, price - 0.01, n=i))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEMA20BuySetup:

    def test_valid_buy_setup_creates_pending(self):
        strat, signals, _ = _make_strategy()
        _warm_up(strat, n=20)
        ema = strat.current_ema
        assert ema is not None

        candle = _candle(ema + 2.0, ema + 5.0, ema - 1.0, n=25)
        strat.on_candle_closed(candle)

        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "BUY"

    def test_no_ema_touch_no_setup(self):
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=20)
        ema = strat.current_ema

        # Candle entirely above EMA
        candle = _candle(ema + 10.0, ema + 15.0, ema + 5.0, n=25)
        strat.on_candle_closed(candle)
        assert not strat.has_pending_setup

    def test_close_exactly_on_ema_no_setup(self):
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=20)
        ema = strat.current_ema

        candle = _candle(ema, ema + 2.0, ema - 2.0, n=25)
        strat.on_candle_closed(candle)
        assert not strat.has_pending_setup

    def test_strategy_name_is_ema20(self):
        strat, _, _ = _make_strategy()
        assert strat.strategy_name == "EMA20"

    def test_magic_is_ema20_magic(self):
        from vt_markets import config
        strat, _, _ = _make_strategy()
        assert strat.magic == config.EMA20_MAGIC


class TestEMA20SellSetup:

    def test_valid_sell_setup(self):
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=20)
        ema = strat.current_ema

        candle = _candle(ema - 2.0, ema + 1.0, ema - 5.0, n=25)
        strat.on_candle_closed(candle)
        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "SELL"


class TestEMATouchVariants:

    def test_wick_touch_buy(self):
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=20)
        ema = strat.current_ema

        # Body above EMA, wick dips below
        candle = _candle(ema + 2.0, ema + 4.0, ema - 1.0, n=25)
        strat.on_candle_closed(candle)
        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "BUY"

    def test_body_touch_sell(self):
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=20)
        ema = strat.current_ema

        # Open above EMA, close below (body crosses EMA)
        candle = Candle(
            timestamp=_ts(25), open=ema + 1.0,
            high=ema + 2.0, low=ema - 3.0, close=ema - 1.0
        )
        strat.on_candle_closed(candle)
        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "SELL"


class TestOHLCBreakout:

    def _buy_setup(self):
        strat, signals, _ = _make_strategy()
        _warm_up(strat, n=20)
        ema = strat.current_ema
        setup = _candle(ema + 2.0, ema + 5.0, ema - 1.0, n=25)
        strat.on_candle_closed(setup)
        assert strat.has_pending_setup
        return strat, signals, ema

    def test_buy_breakout_fires_signal(self):
        strat, signals, ema = self._buy_setup()
        sh = strat.pending_setup.setup_high

        next_c = _candle(sh + 2.0, sh + 3.0, ema + 1.0, n=26)
        strat.on_candle_closed(next_c)

        assert len(signals) == 1
        assert signals[0].direction == "BUY"
        assert signals[0].strategy == "EMA20"

    def test_buy_no_breakout_expires(self):
        strat, signals, ema = self._buy_setup()
        sh = strat.pending_setup.setup_high

        next_c = _candle(ema + 3.0, sh - 0.5, ema + 1.0, n=26)
        strat.on_candle_closed(next_c)

        assert len(signals) == 0
        assert not strat.has_pending_setup

    def test_setup_cannot_trigger_on_n_plus_2(self):
        strat, signals, ema = self._buy_setup()
        sh = strat.pending_setup.setup_high

        # N+1: no breakout
        strat.on_candle_closed(_candle(ema + 1.0, sh - 0.5, ema, n=26))
        assert not strat.has_pending_setup

        # N+2: breaks old level → must NOT fire
        strat.on_candle_closed(_candle(sh + 5.0, sh + 10.0, ema + 1.0, n=27))
        assert len(signals) == 0

    def test_sell_breakout_fires_signal(self):
        strat, signals, _ = _make_strategy()
        _warm_up(strat, n=20)
        ema = strat.current_ema

        setup = _candle(ema - 2.0, ema + 1.0, ema - 5.0, n=25)
        strat.on_candle_closed(setup)
        sl = strat.pending_setup.setup_low

        next_c = _candle(sl - 3.0, ema, sl - 5.0, n=26)
        strat.on_candle_closed(next_c)

        assert len(signals) == 1
        assert signals[0].direction == "SELL"


class TestLiveTickBreakout:

    def test_buy_tick_breakout_fires_signal(self):
        strat, signals, _ = _make_strategy()
        _warm_up(strat, n=20)
        ema = strat.current_ema

        setup = _candle(ema + 2.0, ema + 5.0, ema - 1.0, n=25)
        strat.on_candle_closed(setup)
        sh = strat.pending_setup.setup_high

        # Live tick above setup_high
        tick = _tick(bid=sh + 0.5, ask=sh + 0.8)
        strat.on_tick(tick)

        assert len(signals) == 1
        assert signals[0].direction == "BUY"
        assert signals[0].entry_price == pytest.approx(sh + 0.8)  # ASK price

    def test_sell_tick_breakout_fires_signal(self):
        strat, signals, _ = _make_strategy()
        _warm_up(strat, n=20)
        ema = strat.current_ema

        setup = _candle(ema - 2.0, ema + 1.0, ema - 5.0, n=25)
        strat.on_candle_closed(setup)
        sl = strat.pending_setup.setup_low

        tick = _tick(bid=sl - 0.5, ask=sl - 0.2)
        strat.on_tick(tick)

        assert len(signals) == 1
        assert signals[0].direction == "SELL"
        assert signals[0].entry_price == pytest.approx(sl - 0.5)  # BID price

    def test_tick_below_setup_high_no_buy(self):
        strat, signals, _ = _make_strategy()
        _warm_up(strat, n=20)
        ema = strat.current_ema

        setup = _candle(ema + 2.0, ema + 5.0, ema - 1.0, n=25)
        strat.on_candle_closed(setup)
        sh = strat.pending_setup.setup_high

        # Tick does NOT break setup_high
        tick = _tick(bid=sh - 0.3, ask=sh - 0.1)
        strat.on_tick(tick)
        assert len(signals) == 0
        assert strat.has_pending_setup  # still waiting


class TestMultipleSignals:

    def test_multiple_buy_signals_all_fire(self):
        strat, signals, _ = _make_strategy()
        _warm_up(strat, n=20)

        for i in range(3):
            ema = strat.current_ema
            if ema is None:
                continue
            setup = _candle(ema + 2.0, ema + 5.0, ema - 1.0, n=25 + i * 3)
            strat.on_candle_closed(setup)
            if strat.has_pending_setup:
                sh = strat.pending_setup.setup_high
                strat.on_candle_closed(_candle(sh + 2.0, sh + 3.0, ema + 1.0, n=26 + i * 3))

        assert len(signals) == 3

    def test_multiple_sell_signals_all_fire(self):
        strat, signals, _ = _make_strategy()
        _warm_up(strat, n=20)

        for i in range(3):
            ema = strat.current_ema
            if ema is None:
                continue
            setup = _candle(ema - 2.0, ema + 1.0, ema - 5.0, n=25 + i * 3)
            strat.on_candle_closed(setup)
            if strat.has_pending_setup:
                sl = strat.pending_setup.setup_low
                strat.on_candle_closed(_candle(sl - 3.0, ema, sl - 5.0, n=26 + i * 3))

        assert len(signals) == 3


class TestDailyTargetBlock:

    def test_signal_blocked_when_daily_target_reached(self):
        signals = []
        guard = MagicMock()
        guard.is_entry_allowed.return_value = False  # target reached
        strat = EMA20Strategy(on_signal=signals.append, daily_guard=guard)
        _warm_up(strat, n=20)

        ema = strat.current_ema
        setup = _candle(ema + 2.0, ema + 5.0, ema - 1.0, n=25)
        strat.on_candle_closed(setup)

        if strat.has_pending_setup:
            sh = strat.pending_setup.setup_high
            strat.on_candle_closed(_candle(sh + 2.0, sh + 3.0, ema + 1.0, n=26))

        assert len(signals) == 0
