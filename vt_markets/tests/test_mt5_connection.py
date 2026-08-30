"""
vt_markets/tests/test_mt5_connection.py
=========================================
Tests for MT5Connection and demo/live safety gate.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from vt_markets.mt5.connection import MT5Connection


class TestMT5Connection:

    def test_connect_success(self, mock_mt5, demo_account_info):
        mock_mt5.account_info.return_value = demo_account_info
        conn = MT5Connection(login=123456, password="secret", server="VTMarkets-Demo")
        conn.connect()
        assert conn.is_connected

    def test_connect_initialize_failure(self, mock_mt5):
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (-1, "Terminal not found")
        conn = MT5Connection(login=123456, password="x", server="srv")
        with pytest.raises(RuntimeError, match="mt5.initialize"):
            conn.connect()

    def test_connect_login_failure(self, mock_mt5, demo_account_info):
        mock_mt5.login.return_value = False
        mock_mt5.last_error.return_value = (5, "Invalid credentials")
        conn = MT5Connection(login=999, password="wrong", server="srv")
        with pytest.raises(RuntimeError, match="mt5.login"):
            conn.connect()

    def test_disconnect_calls_shutdown(self, mock_mt5, demo_account_info):
        mock_mt5.account_info.return_value = demo_account_info
        conn = MT5Connection(login=123456, password="x", server="srv")
        conn.connect()
        conn.disconnect()
        mock_mt5.shutdown.assert_called()
        assert not conn.is_connected

    def test_get_account_info_returns_dict(self, mock_mt5, demo_account_info):
        mock_mt5.account_info.return_value = demo_account_info
        conn = MT5Connection(login=123456, password="x", server="srv")
        conn.connect()
        info = conn.get_account_info()
        assert info["balance"] == 10000.0
        assert info["trade_mode"] == "DEMO"
        assert "password" not in info

    def test_password_not_in_account_info(self, mock_mt5, demo_account_info):
        mock_mt5.account_info.return_value = demo_account_info
        conn = MT5Connection(login=123456, password="SuperSecret123", server="srv")
        conn.connect()
        info = conn.get_account_info()
        # Password must NEVER appear in account info
        for v in info.values():
            assert "SuperSecret123" not in str(v)

    def test_assert_demo_passes_on_demo_account(self, mock_mt5, demo_account_info):
        mock_mt5.account_info.return_value = demo_account_info
        conn = MT5Connection(login=123456, password="x", server="srv")
        conn.connect()
        conn.assert_demo_or_allowed(allow_live=False)  # should not raise

    def test_assert_demo_blocks_live_account_without_flag(self, mock_mt5, live_account_info):
        mock_mt5.account_info.return_value = live_account_info
        conn = MT5Connection(login=123456, password="x", server="srv")
        conn.connect()
        with pytest.raises(RuntimeError, match="SAFETY"):
            conn.assert_demo_or_allowed(allow_live=False)

    def test_assert_demo_allows_live_account_with_flag(self, mock_mt5, live_account_info):
        mock_mt5.account_info.return_value = live_account_info
        conn = MT5Connection(login=123456, password="x", server="srv")
        conn.connect()
        conn.assert_demo_or_allowed(allow_live=True)  # should not raise
