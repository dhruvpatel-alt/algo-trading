"""
xau_algo/tests/test_ema20_strategy.py
=======================================
Unit tests for EMA20Strategy.

Tests are completely independent from EMA50Strategy.
EMA50 is never imported or referenced here.

Tested behaviours:
  - Valid BUY setup
  - Invalid BUY setup (close < EMA)
  - Valid SELL setup
  - Invalid SELL setup (close > EMA, EMA touches, but wrong direction)
  - Wick-only touch (EMA touches wick but not body)
  - Body touch (EMA touches body)
  - Close exactly on EMA → NO setup (strict inequality)
  - Next candle breaks setup_high → BUY fired
  - Next candle does NOT break setup_high → setup expires
  - Setup expires after next candle (cannot trigger on candle N+2)
  - Repeated BUY signals (multiple consecutive setups → multiple trades)
  - Repeated SELL signals
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

# Patch config before importing strategies to avoid .env requirement
with patch.dict(os.environ, {
    "TWELVE_DATA_API_KEY_1": "testkey1",
}):
    from xau_algo.strategies.ema20_strategy import EMA20Strategy
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


def _candle(
    close: float,
    high: float,
    low: float,
    open_: float | None = None,
    minute: int = 0,
) -> Candle:
    return Candle(
        timestamp=_ts(minute),
        open=open_ if open_ is not None else close,
        high=high,
        low=low,
        close=close,
    )


def _make_strategy():
    """Return a fresh EMA20Strategy with mock broker and real daily guard."""
    broker = MagicMock(spec=PaperBroker)
    broker.daily_pnl = 0.0
    broker.open_position = MagicMock()

    daily_guard = MagicMock(spec=DailyTargetGuard)
    daily_guard.is_entry_allowed.return_value = True

    strat = EMA20Strategy(broker=broker, daily_guard=daily_guard)
    return strat, broker, daily_guard


def _warm_up(strat: EMA20Strategy, n: int = 20, base_price: float = 3000.0) -> None:
    """
    Feed n candles that do NOT form a setup (EMA never touches candle range).
    Prices rise linearly so EMA stays well below/above — no setup triggered.
    """
    for i in range(n):
        # High EMA relative to candle → candle far above EMA during warmup
        # Just feed neutral candles where EMA doesn't touch
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

class TestEMA20BuySetup:

    def test_valid_buy_setup(self):
        """
        close > EMA20  AND  low <= EMA20 <= high  → BUY setup created
        """
        strat, broker, _ = _make_strategy()
        _warm_up(strat, n=20, base_price=3000.0)

        # After warmup, EMA20 ≈ 3000.1 (small linear drift)
        # Feed a candle that touches EMA from above
        ema_approx = strat.current_ema
        assert ema_approx is not None

        setup_candle = Candle(
            timestamp=_ts(25),
            open=ema_approx + 1.0,
            high=ema_approx + 5.0,
            low=ema_approx - 1.0,   # EMA touches wick below body
            close=ema_approx + 2.0,  # close ABOVE EMA
        )
        strat.on_candle_closed(setup_candle)

        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "BUY"

    def test_invalid_buy_setup_close_below_ema(self):
        """
        EMA touches candle BUT close < EMA → should be SELL setup, not BUY.
        """
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=20, base_price=3000.0)

        ema_approx = strat.current_ema
        assert ema_approx is not None

        candle = Candle(
            timestamp=_ts(25),
            open=ema_approx - 1.0,
            high=ema_approx + 1.0,
            low=ema_approx - 3.0,
            close=ema_approx - 0.5,  # close BELOW EMA → SELL setup
        )
        strat.on_candle_closed(candle)

        # Should be SELL, not BUY
        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "SELL"

    def test_invalid_buy_no_touch(self):
        """
        close > EMA BUT EMA does NOT touch candle → no setup.
        """
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=20, base_price=3000.0)

        ema_approx = strat.current_ema
        assert ema_approx is not None

        # Candle entirely above EMA → no touch
        candle = Candle(
            timestamp=_ts(25),
            open=ema_approx + 10.0,
            high=ema_approx + 15.0,
            low=ema_approx + 5.0,   # low ABOVE EMA → no touch
            close=ema_approx + 12.0,
        )
        strat.on_candle_closed(candle)
        assert not strat.has_pending_setup

    def test_close_exactly_on_ema_no_setup(self):
        """
        close == EMA20 → strict inequality → NO setup.
        """
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=20, base_price=3000.0)

        ema_approx = strat.current_ema
        assert ema_approx is not None

        candle = Candle(
            timestamp=_ts(25),
            open=ema_approx - 1.0,
            high=ema_approx + 2.0,
            low=ema_approx - 2.0,
            close=ema_approx,   # EXACTLY on EMA
        )
        strat.on_candle_closed(candle)
        assert not strat.has_pending_setup


class TestEMA20SellSetup:

    def test_valid_sell_setup(self):
        """
        close < EMA20  AND  low <= EMA20 <= high → SELL setup created.
        """
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=20, base_price=3000.0)

        ema_approx = strat.current_ema
        assert ema_approx is not None

        candle = Candle(
            timestamp=_ts(25),
            open=ema_approx - 1.0,
            high=ema_approx + 2.0,   # EMA touches wick above body
            low=ema_approx - 4.0,
            close=ema_approx - 2.0,  # close BELOW EMA
        )
        strat.on_candle_closed(candle)

        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "SELL"

    def test_invalid_sell_setup_close_above_ema(self):
        """
        EMA touches but close > EMA → should be BUY setup, not SELL.
        """
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=20, base_price=3000.0)

        ema_approx = strat.current_ema
        assert ema_approx is not None

        candle = Candle(
            timestamp=_ts(25),
            open=ema_approx,
            high=ema_approx + 3.0,
            low=ema_approx - 1.0,
            close=ema_approx + 2.0,  # close ABOVE EMA
        )
        strat.on_candle_closed(candle)

        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "BUY"


class TestEMATouchVariants:

    def test_wick_only_touch_buy(self):
        """
        EMA touches only the lower wick (not the body), close above → BUY setup.
        """
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=20, base_price=3000.0)

        ema = strat.current_ema
        assert ema is not None

        # Body: open=ema+2 to close=ema+3 (above EMA)
        # Wick: low=ema-1 (below EMA) → EMA is in the wick
        candle = Candle(
            timestamp=_ts(25),
            open=ema + 2.0,
            high=ema + 5.0,
            low=ema - 1.0,      # lower wick dips below EMA
            close=ema + 3.0,    # close above EMA
        )
        strat.on_candle_closed(candle)
        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "BUY"

    def test_body_touch_sell(self):
        """
        EMA is inside the candle body, close below → SELL setup.
        """
        strat, _, _ = _make_strategy()
        _warm_up(strat, n=20, base_price=3000.0)

        ema = strat.current_ema
        assert ema is not None

        # open above EMA, close below EMA → EMA inside body
        candle = Candle(
            timestamp=_ts(25),
            open=ema + 1.0,
            high=ema + 3.0,
            low=ema - 3.0,
            close=ema - 1.0,   # close BELOW EMA
        )
        strat.on_candle_closed(candle)
        assert strat.has_pending_setup
        assert strat.pending_setup.direction == "SELL"


class TestBreakout:

    def _setup_with_buy_pending(self):
        strat, broker, daily_guard = _make_strategy()
        _warm_up(strat, n=20, base_price=3000.0)

        ema = strat.current_ema
        # Create BUY setup candle
        setup = Candle(
            timestamp=_ts(25),
            open=ema + 1.0,
            high=ema + 5.0,     # setup_high = ema + 5.0
            low=ema - 1.0,
            close=ema + 2.0,
        )
        strat.on_candle_closed(setup)
        assert strat.has_pending_setup
        return strat, broker, ema

    def test_buy_breakout_next_candle(self):
        """
        Next candle high > setup_high → BUY triggered (3 positions opened).
        """
        strat, broker, ema = self._setup_with_buy_pending()
        setup_high = strat.pending_setup.setup_high

        # Next candle breaks the high
        next_c = Candle(
            timestamp=_ts(26),
            open=ema + 3.0,
            high=setup_high + 1.0,   # BREAKS above setup_high
            low=ema + 1.0,
            close=ema + 4.0,
        )
        strat.on_candle_closed(next_c)

        # 3 positions must have been opened
        assert broker.open_position.call_count == 3
        assert not strat.has_pending_setup

    def test_buy_no_breakout_setup_expires(self):
        """
        Next candle high <= setup_high → NO trade, setup expires.
        """
        strat, broker, ema = self._setup_with_buy_pending()
        setup_high = strat.pending_setup.setup_high

        # Next candle does NOT break the high
        next_c = Candle(
            timestamp=_ts(26),
            open=ema + 1.0,
            high=setup_high - 0.5,   # DOES NOT break
            low=ema,
            close=ema + 0.5,
        )
        strat.on_candle_closed(next_c)

        assert broker.open_position.call_count == 0
        assert not strat.has_pending_setup

    def test_setup_expires_cannot_trigger_on_n_plus_2(self):
        """
        Setup fires on candle N. Candle N+1 does not break.
        Candle N+2 breaks setup_high → NO TRADE (setup is dead).
        """
        strat, broker, ema = self._setup_with_buy_pending()
        setup_high = strat.pending_setup.setup_high

        # N+1: no breakout
        next_c1 = Candle(
            timestamp=_ts(26),
            open=ema + 1.0,
            high=setup_high - 0.5,
            low=ema,
            close=ema + 0.5,
        )
        strat.on_candle_closed(next_c1)
        assert not strat.has_pending_setup

        # N+2: breaks old setup_high — must NOT fire
        next_c2 = Candle(
            timestamp=_ts(27),
            open=ema + 2.0,
            high=setup_high + 5.0,   # would trigger if setup was still alive
            low=ema + 1.0,
            close=setup_high + 3.0,
        )
        strat.on_candle_closed(next_c2)
        # No new setup formed on next_c2 (EMA likely not touching)
        # No trade should have been opened from the expired setup
        assert broker.open_position.call_count == 0


class TestRepeatedSignals:

    def test_multiple_buy_signals(self):
        """
        Multiple consecutive BUY setups each produce 3 positions.
        No restriction on repeated same-direction signals.
        """
        strat, broker, daily_guard = _make_strategy()
        _warm_up(strat, n=20, base_price=3000.0)

        total_calls = 0

        for i in range(3):
            ema = strat.current_ema
            if ema is None:
                continue

            setup = Candle(
                timestamp=_ts(25 + i * 3),
                open=ema + 1.0,
                high=ema + 5.0,
                low=ema - 1.0,
                close=ema + 2.0,
            )
            strat.on_candle_closed(setup)

            if strat.has_pending_setup:
                setup_high = strat.pending_setup.setup_high
                trigger = Candle(
                    timestamp=_ts(26 + i * 3),
                    open=ema + 3.0,
                    high=setup_high + 1.0,
                    low=ema + 1.0,
                    close=ema + 4.0,
                )
                strat.on_candle_closed(trigger)
                total_calls += 3   # 3 positions per signal

        assert broker.open_position.call_count == total_calls

    def test_multiple_sell_signals(self):
        """
        Multiple consecutive SELL setups each produce 3 positions.
        """
        strat, broker, daily_guard = _make_strategy()
        _warm_up(strat, n=20, base_price=3000.0)

        total_calls = 0

        for i in range(3):
            ema = strat.current_ema
            if ema is None:
                continue

            setup = Candle(
                timestamp=_ts(25 + i * 3),
                open=ema - 1.0,
                high=ema + 2.0,
                low=ema - 4.0,
                close=ema - 2.0,
            )
            strat.on_candle_closed(setup)

            if strat.has_pending_setup:
                setup_low = strat.pending_setup.setup_low
                trigger = Candle(
                    timestamp=_ts(26 + i * 3),
                    open=ema - 2.0,
                    high=ema - 0.5,
                    low=setup_low - 1.0,
                    close=ema - 3.0,
                )
                strat.on_candle_closed(trigger)
                total_calls += 3

        assert broker.open_position.call_count == total_calls
