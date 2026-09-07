"""
tests/test_supabase_repository.py
===================================
Unit tests for SupabaseRepository.

All tests run WITHOUT a real Supabase instance by mocking supabase-py.
"""

from __future__ import annotations

import time
import threading
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candle():
    from xau_algo.strategies.base_strategy import Candle
    return Candle(
        timestamp=datetime(2025, 1, 2, 10, 0, tzinfo=timezone.utc),
        open=2700.0,
        high=2705.0,
        low=2698.0,
        close=2703.0,
    )


def _make_position():
    from xau_algo.trading.position import Position
    return Position(
        id=str(uuid.uuid4()),
        strategy="EMA20",
        side="BUY",
        lot=0.06,
        entry_price=2700.0,
        stop_loss=2690.0,
        take_profit=2710.0,
        risk_reward=1.0,
        setup_time=datetime(2025, 1, 2, 9, 59, tzinfo=timezone.utc),
        entry_time=datetime(2025, 1, 2, 10, 0, tzinfo=timezone.utc),
        exit_time=datetime(2025, 1, 2, 10, 5, tzinfo=timezone.utc),
        exit_price=2710.0,
        status="CLOSED_TP",
        pnl=60.0,
    )


def _make_mock_client():
    """
    Return a mock supabase Client whose table()/upsert()/insert()/execute()
    chain all return MagicMocks (so calls don't raise).
    """
    mock_execute = MagicMock(return_value=MagicMock(data=[]))
    mock_builder = MagicMock()
    mock_builder.select.return_value = mock_builder
    mock_builder.upsert.return_value = mock_builder
    mock_builder.insert.return_value = mock_builder
    mock_builder.limit.return_value = mock_builder
    mock_builder.execute.return_value = mock_execute

    mock_client = MagicMock()
    mock_client.table.return_value = mock_builder
    return mock_client, mock_builder


# ---------------------------------------------------------------------------
# Tests: disabled (no URL or KEY)
# ---------------------------------------------------------------------------

class TestSupabaseRepositoryDisabled:
    """When env vars are missing the repo must be a complete no-op."""

    def test_enabled_is_false_when_url_missing(self, monkeypatch):
        monkeypatch.setattr("xau_algo.config.SUPABASE_DB_URL", "")
        monkeypatch.setattr("xau_algo.config.SUPABASE_DB_KEY", "somekey")
        from xau_algo.storage.supabase_repository import SupabaseRepository
        repo = SupabaseRepository()
        assert repo.enabled is False

    def test_enabled_is_false_when_key_missing(self, monkeypatch):
        monkeypatch.setattr("xau_algo.config.SUPABASE_DB_URL", "https://x.supabase.co")
        monkeypatch.setattr("xau_algo.config.SUPABASE_DB_KEY", "")
        from xau_algo.storage.supabase_repository import SupabaseRepository
        repo = SupabaseRepository()
        assert repo.enabled is False

    def test_save_candle_noop(self, monkeypatch):
        monkeypatch.setattr("xau_algo.config.SUPABASE_DB_URL", "")
        monkeypatch.setattr("xau_algo.config.SUPABASE_DB_KEY", "")
        from xau_algo.storage.supabase_repository import SupabaseRepository
        repo = SupabaseRepository()
        repo.save_candle(_make_candle())   # must not raise

    def test_save_tick_noop(self, monkeypatch):
        monkeypatch.setattr("xau_algo.config.SUPABASE_DB_URL", "")
        monkeypatch.setattr("xau_algo.config.SUPABASE_DB_KEY", "")
        from xau_algo.storage.supabase_repository import SupabaseRepository
        repo = SupabaseRepository()
        repo.save_tick(2700.0, datetime.now(tz=timezone.utc))

    def test_save_signal_noop(self, monkeypatch):
        monkeypatch.setattr("xau_algo.config.SUPABASE_DB_URL", "")
        monkeypatch.setattr("xau_algo.config.SUPABASE_DB_KEY", "")
        from xau_algo.storage.supabase_repository import SupabaseRepository
        repo = SupabaseRepository()
        repo.save_signal(
            signal_id=str(uuid.uuid4()),
            strategy="EMA20",
            direction="BUY",
            entry_price=2700.0,
            stop_loss=2690.0,
            setup_time=None,
            signal_time=None,
        )

    def test_save_trade_noop(self, monkeypatch):
        monkeypatch.setattr("xau_algo.config.SUPABASE_DB_URL", "")
        monkeypatch.setattr("xau_algo.config.SUPABASE_DB_KEY", "")
        from xau_algo.storage.supabase_repository import SupabaseRepository
        repo = SupabaseRepository()
        repo.save_trade(_make_position())

    def test_close_noop(self, monkeypatch):
        monkeypatch.setattr("xau_algo.config.SUPABASE_DB_URL", "")
        monkeypatch.setattr("xau_algo.config.SUPABASE_DB_KEY", "")
        from xau_algo.storage.supabase_repository import SupabaseRepository
        repo = SupabaseRepository()
        repo.close()   # must not raise


# ---------------------------------------------------------------------------
# Tests: enabled (mocked supabase-py client)
# ---------------------------------------------------------------------------

class TestSupabaseRepositoryEnabled:
    """Tests with a mocked supabase-py Client."""

    @pytest.fixture(autouse=True)
    def patch_env(self, monkeypatch):
        monkeypatch.setattr("xau_algo.config.SUPABASE_DB_URL", "https://fake.supabase.co")
        monkeypatch.setattr("xau_algo.config.SUPABASE_DB_KEY", "fake-service-role-key")

    def _make_repo(self, mock_client):
        """Patch supabase.create_client and return an enabled SupabaseRepository."""
        with patch("supabase.create_client", return_value=mock_client):
            from importlib import reload
            import xau_algo.storage.supabase_repository as mod
            reload(mod)
            repo = mod.SupabaseRepository()
        time.sleep(0.05)   # let writer thread start
        return repo

    def test_enabled_is_true(self):
        mock_client, _ = _make_mock_client()
        with patch("supabase.create_client", return_value=mock_client):
            from importlib import reload
            import xau_algo.storage.supabase_repository as mod
            reload(mod)
            repo = mod.SupabaseRepository()
        assert repo.enabled is True
        repo.close(timeout=2)

    def test_ensure_tables_pings_candles_when_no_direct_url(self, monkeypatch):
        monkeypatch.setattr("xau_algo.config.SUPABASE_DB_DIRECT_URL", "")
        mock_client, mock_builder = _make_mock_client()
        repo = self._make_repo(mock_client)
        repo.ensure_tables()
        mock_client.table.assert_called_with("candles")
        mock_builder.select.assert_called_with("id")
        repo.close(timeout=2)

    def test_ensure_tables_runs_ddl_when_direct_url_set(self, monkeypatch):
        monkeypatch.setattr(
            "xau_algo.config.SUPABASE_DB_DIRECT_URL",
            "postgresql://fake/db",
        )
        mock_client, _ = _make_mock_client()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = lambda s: s
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur

        repo = self._make_repo(mock_client)
        with patch("psycopg2.connect", return_value=mock_conn):
            repo.ensure_tables()
        # Six DDL statements (4 tables + 1 view + 1 triggers)
        assert mock_cur.execute.call_count == 6
        repo.close(timeout=2)

    def test_save_candle_upserts(self):
        mock_client, mock_builder = _make_mock_client()
        repo = self._make_repo(mock_client)
        repo.save_candle(_make_candle())
        repo.close(timeout=2)
        mock_client.table.assert_any_call("candles")
        mock_builder.upsert.assert_called()
        call_kwargs = mock_builder.upsert.call_args
        row = call_kwargs[0][0]  # first positional arg is the dict
        assert row["symbol"] == "XAU/USD"
        assert "timestamp" in row

    def test_save_tick_inserts(self):
        mock_client, mock_builder = _make_mock_client()
        repo = self._make_repo(mock_client)
        repo.save_tick(2700.5, datetime.now(tz=timezone.utc))
        repo.close(timeout=2)
        mock_client.table.assert_any_call("ticks")
        mock_builder.insert.assert_called()

    def test_save_signal_upserts(self):
        mock_client, mock_builder = _make_mock_client()
        repo = self._make_repo(mock_client)
        repo.save_signal(
            signal_id=str(uuid.uuid4()),
            strategy="EMA50",
            direction="SELL",
            entry_price=2700.0,
            stop_loss=2710.0,
            setup_time=datetime.now(tz=timezone.utc),
            signal_time=datetime.now(tz=timezone.utc),
        )
        repo.close(timeout=2)
        mock_client.table.assert_any_call("signals")
        mock_builder.upsert.assert_called()

    def test_save_trade_upserts(self):
        mock_client, mock_builder = _make_mock_client()
        repo = self._make_repo(mock_client)
        repo.save_trade(_make_position())
        repo.close(timeout=2)
        mock_client.table.assert_any_call("trades")
        mock_builder.upsert.assert_called()

    def test_close_drains_queue(self):
        mock_client, _ = _make_mock_client()
        repo = self._make_repo(mock_client)
        for _ in range(5):
            repo.save_tick(2700.0, datetime.now(tz=timezone.utc))
        repo.close(timeout=5)
        # Writer thread should have exited
        assert repo._thread is None or not repo._thread.is_alive()

    def test_client_failure_disables_repo(self, monkeypatch):
        """If create_client raises, repo must degrade gracefully."""
        with patch("supabase.create_client", side_effect=Exception("connect refused")):
            from importlib import reload
            import xau_algo.storage.supabase_repository as mod
            reload(mod)
            repo = mod.SupabaseRepository()
        assert repo.enabled is False
        repo.save_tick(1.0, datetime.now(tz=timezone.utc))  # must not raise
        repo.close()


# ---------------------------------------------------------------------------
# Integration smoke test: trade_repository dual-write
# ---------------------------------------------------------------------------

class TestTradeRepositoryDualWrite:
    """Verify TradeRepository calls supabase_repo.save_trade() on close."""

    def test_dual_write_called(self, tmp_path):
        from xau_algo.storage.trade_repository import TradeRepository
        mock_supabase = MagicMock()
        mock_supabase.enabled = True

        repo = TradeRepository(
            csv_path=str(tmp_path / "trades.csv"),
            supabase_repo=mock_supabase,
        )
        pos = _make_position()
        repo.save_trade(pos)

        mock_supabase.save_trade.assert_called_once_with(pos)

    def test_no_supabase_still_writes_csv(self, tmp_path):
        from xau_algo.storage.trade_repository import TradeRepository
        repo = TradeRepository(csv_path=str(tmp_path / "trades.csv"))
        pos = _make_position()
        repo.save_trade(pos)
        trades = repo.load_trades()
        assert len(trades) == 1
        assert trades[0]["strategy"] == "EMA20"

