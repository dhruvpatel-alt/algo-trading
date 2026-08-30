"""
vt_markets/tests/conftest.py
==============================
Shared pytest fixtures for VT Markets tests.

All MT5 calls are mocked — no real MetaTrader 5 terminal required.
No real orders are placed in any test.
"""

from __future__ import annotations

import sys
import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Make vt_markets importable from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# ---------------------------------------------------------------------------
# MT5 module mock — installed before vt_markets imports MT5
# ---------------------------------------------------------------------------

class MockMT5:
    """
    Complete mock of the MetaTrader5 module.
    All constants and functions are pre-configured with sensible defaults.
    Tests may override individual attributes as needed.
    """
    # Constants
    TIMEFRAME_M1 = 1
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_CONTEST = 1
    ACCOUNT_TRADE_MODE_REAL = 2
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 1
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009

    # Functions — defaults
    initialize = MagicMock(return_value=True)
    login = MagicMock(return_value=True)
    shutdown = MagicMock()
    last_error = MagicMock(return_value=(0, "Success"))
    account_info = MagicMock()
    symbol_info = MagicMock()
    symbol_info_tick = MagicMock()
    symbol_select = MagicMock(return_value=True)
    symbols_get = MagicMock(return_value=[])
    positions_get = MagicMock(return_value=[])
    copy_rates_from_pos = MagicMock()
    order_send = MagicMock()

    @classmethod
    def reset(cls):
        """Reset all mocks to default state."""
        cls.initialize = MagicMock(return_value=True)
        cls.login = MagicMock(return_value=True)
        cls.shutdown = MagicMock()
        cls.last_error = MagicMock(return_value=(0, "Success"))
        cls.account_info = MagicMock()
        cls.symbol_info = MagicMock()
        cls.symbol_info_tick = MagicMock()
        cls.symbol_select = MagicMock(return_value=True)
        cls.symbols_get = MagicMock(return_value=[])
        cls.positions_get = MagicMock(return_value=[])
        cls.copy_rates_from_pos = MagicMock()
        cls.order_send = MagicMock()


# Install the mock into sys.modules BEFORE any vt_markets imports
sys.modules.setdefault("MetaTrader5", MockMT5)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_mt5_mock():
    """Reset MT5 mock state before every test."""
    MockMT5.reset()
    yield


@pytest.fixture
def mock_mt5():
    return MockMT5


@pytest.fixture
def demo_account_info():
    """Mock MT5 account_info() for a DEMO account."""
    info = MagicMock()
    info.login = 123456
    info.name = "Test User"
    info.server = "VTMarkets-Demo"
    info.company = "VT Markets"
    info.currency = "USD"
    info.balance = 10000.0
    info.equity = 10000.0
    info.margin = 0.0
    info.margin_free = 10000.0
    info.leverage = 100
    info.trade_mode = MockMT5.ACCOUNT_TRADE_MODE_DEMO
    info.trade_allowed = True
    info.trade_expert = True
    return info


@pytest.fixture
def live_account_info(demo_account_info):
    """Mock account_info() for a LIVE account."""
    demo_account_info.trade_mode = MockMT5.ACCOUNT_TRADE_MODE_REAL
    return demo_account_info


@pytest.fixture
def mock_symbol_info():
    """Mock MT5 symbol_info() for XAUUSD."""
    info = MagicMock()
    info.name = "XAUUSD"
    info.digits = 2
    info.point = 0.01
    info.volume_min = 0.01
    info.volume_max = 100.0
    info.volume_step = 0.01
    info.trade_tick_size = 0.01
    info.trade_tick_value = 1.0
    info.trade_contract_size = 100.0
    info.spread = 30
    info.trade_mode = 4
    info.visible = True
    return info


@pytest.fixture
def mock_tick():
    """Mock MT5 symbol_info_tick() for XAUUSD."""
    tick = MagicMock()
    tick.bid = 3350.00
    tick.ask = 3350.30
    tick.last = 3350.15
    tick.volume = 100
    tick.time = int(datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp())
    return tick


@pytest.fixture
def successful_order_result():
    """Mock successful mt5.order_send() result."""
    result = MagicMock()
    result.retcode = MockMT5.TRADE_RETCODE_DONE
    result.order = 1001
    result.deal = 2001
    result.volume = 0.06
    result.price = 3350.30
    result.comment = "Request executed"
    return result


@pytest.fixture
def rejected_order_result():
    """Mock rejected mt5.order_send() result."""
    result = MagicMock()
    result.retcode = 10006   # TRADE_RETCODE_REJECT
    result.order = 0
    result.deal = 0
    result.volume = 0.0
    result.price = 0.0
    result.comment = "Request rejected"
    return result


@pytest.fixture
def daily_guard():
    """Real VTDailyTargetGuard with $10,000 capital and 1% target."""
    from vt_markets.trading.daily_target import VTDailyTargetGuard
    return VTDailyTargetGuard(initial_capital=10_000.0, daily_target_pct=1.0)


@pytest.fixture
def permissive_daily_guard():
    """Daily guard that never blocks entries."""
    guard = MagicMock()
    guard.is_entry_allowed.return_value = True
    return guard
