"""
xau_algo/tests/test_paper_broker.py
=====================================
Unit tests for PaperBroker.

Tests:
  - BUY SL hit
  - BUY TP hit
  - SELL SL hit
  - SELL TP hit
  - Correct PnL calculation for 1:1 RR
  - Correct PnL calculation for 1:2 RR
  - Correct PnL calculation for 1:2.5 RR
  - Multiple simultaneous positions (all tracked independently)
  - Opposite-direction positions (BUY and SELL open at same time)
  - Same-candle SL/TP conflict → SL first (default conservative assumption)
  - Same-candle SL/TP conflict → TP first (when ASSUME_SL_FIRST_ON_CONFLICT=False)
  - daily_pnl accumulates correctly
  - reset_daily() zeroes daily_pnl
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from datetime import datetime, timezone
from unittest.mock import patch
import pytest

with patch.dict(os.environ, {"TWELVE_DATA_API_KEY_1": "testkey1"}):
    from xau_algo.trading.paper_broker import PaperBroker
    from xau_algo.trading.position import Position


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
_CONTRACT = 100.0   # 1 lot = 100 oz


def _buy_position(
    entry: float = 3350.0,
    sl: float = 3340.0,
    tp: float = 3360.0,
    lot: float = 0.06,
    rr: float = 1.0,
) -> Position:
    return Position(
        strategy="EMA20",
        side="BUY",
        lot=lot,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward=rr,
        setup_time=_TS,
        entry_time=_TS,
    )


def _sell_position(
    entry: float = 3350.0,
    sl: float = 3360.0,
    tp: float = 3340.0,
    lot: float = 0.06,
    rr: float = 1.0,
) -> Position:
    return Position(
        strategy="EMA20",
        side="SELL",
        lot=lot,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward=rr,
        setup_time=_TS,
        entry_time=_TS,
    )


def _broker() -> PaperBroker:
    return PaperBroker(initial_capital=10_000.0, contract_size=_CONTRACT)


# ---------------------------------------------------------------------------
# Tests — BUY position
# ---------------------------------------------------------------------------

class TestBuyPosition:

    def test_buy_sl_hit(self):
        """Price falls to SL → position closes with loss."""
        b = _broker()
        pos = _buy_position(entry=3350.0, sl=3340.0, tp=3360.0, lot=0.06)
        b.open_position(pos)

        b.on_price(3340.0, _TS)   # price == SL

        assert len(b.open_positions) == 0
        assert len(b.closed_positions) == 1
        closed = b.closed_positions[0]
        assert closed.status == "CLOSED_SL"
        # PnL = (3340 - 3350) * 0.06 * 100 = -$60
        assert closed.pnl == pytest.approx(-60.0)

    def test_buy_tp_hit(self):
        """Price rises to TP → position closes with profit."""
        b = _broker()
        pos = _buy_position(entry=3350.0, sl=3340.0, tp=3360.0, lot=0.06)
        b.open_position(pos)

        b.on_price(3360.0, _TS)   # price == TP

        assert len(b.closed_positions) == 1
        closed = b.closed_positions[0]
        assert closed.status == "CLOSED_TP"
        # PnL = (3360 - 3350) * 0.06 * 100 = +$60
        assert closed.pnl == pytest.approx(60.0)

    def test_buy_sl_below_triggers(self):
        """Price drops BELOW SL → SL triggered."""
        b = _broker()
        pos = _buy_position(entry=3350.0, sl=3340.0, tp=3380.0, lot=0.04)
        b.open_position(pos)

        b.on_price(3339.5, _TS)   # below SL

        assert b.closed_positions[0].status == "CLOSED_SL"

    def test_buy_tp_above_triggers(self):
        """Price rises ABOVE TP → TP triggered."""
        b = _broker()
        pos = _buy_position(entry=3350.0, sl=3340.0, tp=3370.0, lot=0.02)
        b.open_position(pos)

        b.on_price(3371.0, _TS)   # above TP

        assert b.closed_positions[0].status == "CLOSED_TP"


# ---------------------------------------------------------------------------
# Tests — SELL position
# ---------------------------------------------------------------------------

class TestSellPosition:

    def test_sell_sl_hit(self):
        """Price rises to SL → SELL position closes with loss."""
        b = _broker()
        pos = _sell_position(entry=3350.0, sl=3360.0, tp=3340.0, lot=0.06)
        b.open_position(pos)

        b.on_price(3360.0, _TS)

        assert b.closed_positions[0].status == "CLOSED_SL"
        # PnL = (3350 - 3360) * 0.06 * 100 = -$60
        assert b.closed_positions[0].pnl == pytest.approx(-60.0)

    def test_sell_tp_hit(self):
        """Price falls to TP → SELL position closes with profit."""
        b = _broker()
        pos = _sell_position(entry=3350.0, sl=3360.0, tp=3340.0, lot=0.06)
        b.open_position(pos)

        b.on_price(3340.0, _TS)

        assert b.closed_positions[0].status == "CLOSED_TP"
        # PnL = (3350 - 3340) * 0.06 * 100 = +$60
        assert b.closed_positions[0].pnl == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# Tests — RR PnL verification
# ---------------------------------------------------------------------------

class TestRRPnL:
    """
    Verify PnL for all three lot/RR tiers on a BUY signal.
    Entry = 3350, SL = 3340, risk = 10 pts
    """

    def test_1r_pnl(self):
        """0.06 lot × 1R = entry+10 TP"""
        b = _broker()
        # risk=10, TP1 = 3350+10 = 3360
        pos = _buy_position(entry=3350.0, sl=3340.0, tp=3360.0, lot=0.06, rr=1.0)
        b.open_position(pos)
        b.on_price(3360.0, _TS)
        # PnL = 10 * 0.06 * 100 = $60
        assert b.closed_positions[0].pnl == pytest.approx(60.0)

    def test_2r_pnl(self):
        """0.04 lot × 2R = entry+20 TP"""
        b = _broker()
        pos = _buy_position(entry=3350.0, sl=3340.0, tp=3370.0, lot=0.04, rr=2.0)
        b.open_position(pos)
        b.on_price(3370.0, _TS)
        # PnL = 20 * 0.04 * 100 = $80
        assert b.closed_positions[0].pnl == pytest.approx(80.0)

    def test_2_5r_pnl(self):
        """0.02 lot × 2.5R = entry+25 TP"""
        b = _broker()
        pos = _buy_position(entry=3350.0, sl=3340.0, tp=3375.0, lot=0.02, rr=2.5)
        b.open_position(pos)
        b.on_price(3375.0, _TS)
        # PnL = 25 * 0.02 * 100 = $50
        assert b.closed_positions[0].pnl == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Tests — Multiple simultaneous positions
# ---------------------------------------------------------------------------

class TestMultiplePositions:

    def test_multiple_buy_positions_independent(self):
        """Three open BUY positions at different TPs — each closes independently."""
        b = _broker()
        p1 = _buy_position(entry=3350.0, sl=3340.0, tp=3360.0, lot=0.06)
        p2 = _buy_position(entry=3350.0, sl=3340.0, tp=3370.0, lot=0.04)
        p3 = _buy_position(entry=3350.0, sl=3340.0, tp=3375.0, lot=0.02)
        b.open_position(p1)
        b.open_position(p2)
        b.open_position(p3)

        assert len(b.open_positions) == 3

        # TP1 hit
        b.on_price(3360.0, _TS)
        assert len(b.closed_positions) == 1
        assert len(b.open_positions) == 2

        # TP2 hit
        b.on_price(3370.0, _TS)
        assert len(b.closed_positions) == 2
        assert len(b.open_positions) == 1

        # TP3 hit
        b.on_price(3375.0, _TS)
        assert len(b.closed_positions) == 3
        assert len(b.open_positions) == 0

    def test_opposite_direction_positions_open_simultaneously(self):
        """A BUY and a SELL can coexist — opposite signal does NOT close existing trade."""
        b = _broker()
        buy = _buy_position(entry=3350.0, sl=3340.0, tp=3360.0, lot=0.06)
        sell = _sell_position(entry=3355.0, sl=3365.0, tp=3345.0, lot=0.06)

        b.open_position(buy)
        b.open_position(sell)

        # Price is between both TPs — neither closes yet
        b.on_price(3352.0, _TS)
        assert len(b.open_positions) == 2
        assert len(b.closed_positions) == 0

        # Price hits BUY TP
        b.on_price(3360.0, _TS)
        assert len(b.closed_positions) == 1
        assert b.closed_positions[0].side == "BUY"
        # SELL still open
        assert len(b.open_positions) == 1
        assert b.open_positions[0].side == "SELL"


# ---------------------------------------------------------------------------
# Tests — Same-candle SL/TP conflict (backtest OHLC)
# ---------------------------------------------------------------------------

class TestOHLCConflict:

    def test_sl_first_by_default(self):
        """Both SL and TP touched in same candle → SL wins (default)."""
        b = _broker()
        pos = _buy_position(entry=3350.0, sl=3340.0, tp=3360.0, lot=0.06)
        b.open_position(pos)

        # Candle low <= SL AND candle high >= TP — conflict
        b.check_ohlc(
            candle_open=3350.0,
            candle_high=3365.0,   # >= TP
            candle_low=3338.0,    # <= SL
            candle_close=3355.0,
            timestamp=_TS,
            assume_sl_first=True,
        )

        assert b.closed_positions[0].status == "CLOSED_SL"
        assert b.closed_positions[0].exit_price == pytest.approx(3340.0)

    def test_tp_first_when_configured(self):
        """With assume_sl_first=False, TP wins on conflict."""
        b = _broker()
        pos = _buy_position(entry=3350.0, sl=3340.0, tp=3360.0, lot=0.06)
        b.open_position(pos)

        b.check_ohlc(
            candle_open=3350.0,
            candle_high=3365.0,
            candle_low=3338.0,
            candle_close=3355.0,
            timestamp=_TS,
            assume_sl_first=False,
        )

        assert b.closed_positions[0].status == "CLOSED_TP"
        assert b.closed_positions[0].exit_price == pytest.approx(3360.0)


# ---------------------------------------------------------------------------
# Tests — Daily P&L and reset
# ---------------------------------------------------------------------------

class TestDailyPnL:

    def test_daily_pnl_accumulates(self):
        b = _broker()
        p1 = _buy_position(entry=3350.0, sl=3340.0, tp=3360.0, lot=0.06)
        p2 = _buy_position(entry=3350.0, sl=3340.0, tp=3370.0, lot=0.04)
        b.open_position(p1)
        b.open_position(p2)

        b.on_price(3360.0, _TS)   # closes p1 → +$60
        b.on_price(3370.0, _TS)   # closes p2 → +$80

        assert b.daily_pnl == pytest.approx(140.0)

    def test_reset_daily_zeroes_daily_pnl(self):
        b = _broker()
        p = _buy_position(entry=3350.0, sl=3340.0, tp=3360.0, lot=0.06)
        b.open_position(p)
        b.on_price(3360.0, _TS)   # +$60

        assert b.daily_pnl == pytest.approx(60.0)

        b.reset_daily()
        assert b.daily_pnl == pytest.approx(0.0)
        # Balance should still reflect the gain
        assert b.balance == pytest.approx(10_060.0)
