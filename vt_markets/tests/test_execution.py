"""
vt_markets/tests/test_execution.py
=====================================
Tests for VTMarketsExecutor and execution safety.

All MT5 API calls are mocked — no real orders placed.

Tests:
  - BUY order uses ASK price
  - SELL order uses BID price
  - Lot validation passes for valid lots
  - Lot validation rejects below-minimum lots
  - Lot validation rejects above-maximum lots
  - Successful order result parsed correctly
  - Rejected order result → status FAILED
  - order_send None → status FAILED
  - Magic number included in order request
  - LIVE account blocked when allow_live=False
  - DEMO account allowed always
  - Risk manager computes correct TP levels for BUY
  - Risk manager computes correct TP levels for SELL
  - SL/TP calculation (1R, 2R, 2.5R)
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

from vt_markets.mt5.execution import VTMarketsExecutor
from vt_markets.trading.risk_manager import RiskManager
from vt_markets.models.signal import Signal
from vt_markets.models.position import VTPosition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


def _executor(allow_live: bool = False) -> VTMarketsExecutor:
    ex = VTMarketsExecutor(symbol="XAUUSD", allow_live=allow_live, deviation=20)
    ex._symbol_info = {
        "digits": 2,
        "point": 0.01,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "trade_tick_size": 0.01,
        "trade_tick_value": 1.0,
        "trade_contract_size": 100.0,
    }
    return ex


def _buy_signal(entry=3350.0, sl=3340.0) -> Signal:
    return Signal(
        strategy="EMA20",
        direction="BUY",
        entry_price=entry,
        stop_loss=sl,
        setup_high=3355.0,
        setup_low=sl,
        setup_time=_TS,
        signal_time=_TS,
        magic=20020,
    )


# ---------------------------------------------------------------------------
# Lot validation
# ---------------------------------------------------------------------------

class TestLotValidation:

    def test_valid_lots_pass(self):
        ex = _executor()
        ex.validate_lots([0.06, 0.04, 0.02])   # should not raise

    def test_lot_below_minimum_raises(self):
        ex = _executor()
        ex._symbol_info["volume_min"] = 0.1
        with pytest.raises(ValueError, match="below broker minimum"):
            ex.validate_lots([0.06])

    def test_lot_above_maximum_raises(self):
        ex = _executor()
        ex._symbol_info["volume_max"] = 0.03
        with pytest.raises(ValueError, match="exceeds broker maximum"):
            ex.validate_lots([0.06])

    def test_invalid_step_raises(self):
        ex = _executor()
        ex._symbol_info["volume_step"] = 0.05
        # 0.03 is between valid steps 0.01 and 0.06 — not a valid step
        with pytest.raises(ValueError, match="valid step"):
            ex.validate_lots([0.03])


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------

class TestBuyOrder:

    def test_buy_uses_ask_price(self, mock_mt5, demo_account_info, mock_tick, successful_order_result):
        mock_mt5.account_info.return_value = demo_account_info
        mock_mt5.symbol_info_tick.return_value = mock_tick
        mock_mt5.order_send.return_value = successful_order_result

        ex = _executor(allow_live=False)
        pos = ex.buy(lot=0.06, stop_loss=3340.0, take_profit=3360.0, magic=20020)

        # Buy must use ASK (3350.30)
        sent_request = mock_mt5.order_send.call_args[0][0]
        assert sent_request["price"] == mock_tick.ask
        assert sent_request["type"] == mock_mt5.ORDER_TYPE_BUY

    def test_buy_success_sets_status_open(self, mock_mt5, demo_account_info, mock_tick, successful_order_result):
        mock_mt5.account_info.return_value = demo_account_info
        mock_mt5.symbol_info_tick.return_value = mock_tick
        mock_mt5.order_send.return_value = successful_order_result

        ex = _executor()
        pos = ex.buy(lot=0.06, stop_loss=3340.0, take_profit=3360.0, magic=20020)
        assert pos.status == "OPEN"
        assert pos.ticket == successful_order_result.order

    def test_buy_includes_magic_number(self, mock_mt5, demo_account_info, mock_tick, successful_order_result):
        mock_mt5.account_info.return_value = demo_account_info
        mock_mt5.symbol_info_tick.return_value = mock_tick
        mock_mt5.order_send.return_value = successful_order_result

        ex = _executor()
        ex.buy(lot=0.06, stop_loss=3340.0, take_profit=3360.0, magic=20020)

        sent_request = mock_mt5.order_send.call_args[0][0]
        assert sent_request["magic"] == 20020


class TestSellOrder:

    def test_sell_uses_bid_price(self, mock_mt5, demo_account_info, mock_tick, successful_order_result):
        mock_mt5.account_info.return_value = demo_account_info
        mock_mt5.symbol_info_tick.return_value = mock_tick
        mock_mt5.order_send.return_value = successful_order_result

        ex = _executor()
        ex.sell(lot=0.06, stop_loss=3360.0, take_profit=3340.0, magic=50050)

        sent_request = mock_mt5.order_send.call_args[0][0]
        assert sent_request["price"] == mock_tick.bid
        assert sent_request["type"] == mock_mt5.ORDER_TYPE_SELL


class TestOrderRejection:

    def test_rejected_order_sets_status_failed(self, mock_mt5, demo_account_info, mock_tick, rejected_order_result):
        mock_mt5.account_info.return_value = demo_account_info
        mock_mt5.symbol_info_tick.return_value = mock_tick
        mock_mt5.order_send.return_value = rejected_order_result

        ex = _executor()
        pos = ex.buy(lot=0.06, stop_loss=3340.0, take_profit=3360.0, magic=20020)
        assert pos.status == "FAILED"

    def test_order_send_none_sets_status_failed(self, mock_mt5, demo_account_info, mock_tick):
        mock_mt5.account_info.return_value = demo_account_info
        mock_mt5.symbol_info_tick.return_value = mock_tick
        mock_mt5.order_send.return_value = None

        ex = _executor()
        pos = ex.buy(lot=0.06, stop_loss=3340.0, take_profit=3360.0, magic=20020)
        assert pos.status == "FAILED"


# ---------------------------------------------------------------------------
# Live trading safety
# ---------------------------------------------------------------------------

class TestLiveTradingSafety:

    def test_demo_account_always_allowed(self, mock_mt5, demo_account_info, mock_tick, successful_order_result):
        mock_mt5.account_info.return_value = demo_account_info
        mock_mt5.symbol_info_tick.return_value = mock_tick
        mock_mt5.order_send.return_value = successful_order_result

        ex = _executor(allow_live=False)   # allow_live=False but it's a DEMO account
        pos = ex.buy(lot=0.06, stop_loss=3340.0, take_profit=3360.0, magic=20020)
        assert pos.status == "OPEN"

    def test_live_account_blocked_without_flag(self, mock_mt5, live_account_info, mock_tick):
        mock_mt5.account_info.return_value = live_account_info
        mock_mt5.symbol_info_tick.return_value = mock_tick

        ex = _executor(allow_live=False)
        with pytest.raises(RuntimeError, match="SAFETY BLOCK"):
            ex.buy(lot=0.06, stop_loss=3340.0, take_profit=3360.0, magic=20020)

    def test_live_account_allowed_with_explicit_flag(self, mock_mt5, live_account_info, mock_tick, successful_order_result):
        mock_mt5.account_info.return_value = live_account_info
        mock_mt5.symbol_info_tick.return_value = mock_tick
        mock_mt5.order_send.return_value = successful_order_result

        ex = _executor(allow_live=True)   # explicitly allowed
        pos = ex.buy(lot=0.06, stop_loss=3340.0, take_profit=3360.0, magic=20020)
        assert pos.status == "OPEN"


# ---------------------------------------------------------------------------
# Risk manager — SL/TP calculation
# ---------------------------------------------------------------------------

class TestRiskManager:

    def test_buy_risk_calculation(self):
        rm = RiskManager()
        sig = _buy_signal(entry=3350.0, sl=3340.0)
        specs = rm.compute_order_specs(sig)

        assert len(specs) == 3
        risk = 3350.0 - 3340.0   # = 10

        # Tier 1: 0.06 lot, 1R
        assert specs[0].lot == pytest.approx(0.06)
        assert specs[0].take_profit == pytest.approx(3350.0 + risk * 1.0)

        # Tier 2: 0.04 lot, 2R
        assert specs[1].lot == pytest.approx(0.04)
        assert specs[1].take_profit == pytest.approx(3350.0 + risk * 2.0)

        # Tier 3: 0.02 lot, 2.5R
        assert specs[2].lot == pytest.approx(0.02)
        assert specs[2].take_profit == pytest.approx(3350.0 + risk * 2.5)

    def test_sell_risk_calculation(self):
        rm = RiskManager()
        sig = Signal(
            strategy="EMA20", direction="SELL",
            entry_price=3350.0, stop_loss=3360.0,
            setup_high=3360.0, setup_low=3340.0,
            setup_time=_TS, signal_time=_TS, magic=20020,
        )
        specs = rm.compute_order_specs(sig)

        assert len(specs) == 3
        risk = 3360.0 - 3350.0   # = 10

        assert specs[0].take_profit == pytest.approx(3350.0 - risk * 1.0)
        assert specs[1].take_profit == pytest.approx(3350.0 - risk * 2.0)
        assert specs[2].take_profit == pytest.approx(3350.0 - risk * 2.5)

    def test_zero_risk_buy_returns_empty(self):
        rm = RiskManager()
        sig = _buy_signal(entry=3340.0, sl=3350.0)  # SL above entry = invalid
        specs = rm.compute_order_specs(sig)
        assert specs == []

    def test_stop_loss_set_correctly_buy(self):
        rm = RiskManager()
        sig = _buy_signal(entry=3350.0, sl=3340.0)
        specs = rm.compute_order_specs(sig)
        for s in specs:
            assert s.stop_loss == pytest.approx(3340.0)

    def test_magic_number_propagated(self):
        rm = RiskManager()
        sig = _buy_signal(entry=3350.0, sl=3340.0)
        specs = rm.compute_order_specs(sig)
        for s in specs:
            assert s.magic == 20020
