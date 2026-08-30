"""
xau_algo/storage/supabase_repository.py
=========================================
Asynchronous Supabase repository for the xau_algo trading system.

Connection
----------
Uses the official ``supabase-py`` SDK (v2+).  Requires two env variables:

  SUPABASE_DB_URL  – your project URL  e.g. https://abcxyz.supabase.co
  SUPABASE_DB_KEY  – service-role key from Supabase > Settings > API

Design principles
-----------------
* **Non-blocking**: All DB writes are dispatched to an internal ``queue.Queue``
  and processed by a single daemon thread.  The trading loop is NEVER stalled
  by network latency or a slow Supabase endpoint.
* **Graceful degradation**: If either env variable is missing the repository
  becomes a no-op.  The system continues to write CSV / JSONL as normal.
* **UPSERT semantics**: candles / signals / trades use ``upsert(on_conflict=…)``
  so duplicate delivery (e.g. reconnect) is harmless.  Ticks use plain
  ``insert`` (append-only, no unique constraint).
* **Tables must exist**: The supabase-py client cannot run DDL.
  Run ``storage/schema.sql`` once in your Supabase SQL Editor before starting.
* **Health state**: On every successful write, ``health_state.set_last_database_success``
  is called.  On persistent failure, the writer attempts to reconnect the
  Supabase client using exponential backoff before giving up on that item.

Tables
------
  candles   – every completed 1-minute OHLC candle
  ticks     – every raw WebSocket price tick  (~1 tick/sec)
  signals   – every EMA breakout signal fired (pre-position)
  trades    – every closed position (mirrors trades.csv)

Usage (main.py)
---------------
  repo = SupabaseRepository(health_state=health_state)
  repo.ensure_tables()   # blocks until tables exist (no-op if disabled)
  ws_client.start()
  # on shutdown:
  repo.close()           # drains queue then stops writer thread
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from xau_algo import config

if TYPE_CHECKING:
    from xau_algo.strategies.base_strategy import Candle
    from xau_algo.trading.position import Position
    from xau_algo.health.state import HealthState

logger = logging.getLogger(__name__)

# Sentinel — inserted into the queue to stop the writer thread
_STOP = object()

# Exponential backoff limits for DB reconnection
_DB_RECONNECT_BASE: float = 2.0
_DB_RECONNECT_MAX: float = 60.0


class SupabaseRepository:
    """
    Async-write Supabase repository.

    All ``save_*`` methods push work onto an internal queue and return
    immediately (never blocking the trading loop).

    If ``SUPABASE_DB_URL`` or ``SUPABASE_DB_KEY`` is unset the instance is
    a complete no-op (``enabled`` is ``False``).
    """

    def __init__(
        self,
        health_state: "HealthState | None" = None,
    ) -> None:
        self._url = config.SUPABASE_DB_URL
        self._key = config.SUPABASE_DB_KEY
        self._client = None
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._health = health_state
        self._reconnect_delay: float = _DB_RECONNECT_BASE

        if not self._url or not self._key:
            logger.warning(
                "SUPABASE_DB_URL or SUPABASE_DB_KEY not set — "
                "Supabase writes disabled.  Trades/candles/signals/ticks "
                "will only be written to CSV/JSONL."
            )
            return

        try:
            from supabase import create_client  # optional dependency

            self._client = create_client(self._url, self._key)
            logger.info(
                "DATABASE_CONNECTED | Supabase client created. Project: %s",
                self._url,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to create Supabase client: %s — "
                "Supabase writes disabled.",
                exc,
            )
            self._client = None
            return

        # Start background writer thread
        self._thread = threading.Thread(
            target=self._writer_loop,
            name="supabase-writer",
            daemon=True,
        )
        self._thread.start()
        logger.info("Supabase writer thread started.")

        # Mark DB as connected in health state
        if self._health is not None:
            self._health.set_database_connected(True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True when the Supabase client is available."""
        return self._client is not None

    def ensure_tables(self) -> None:
        """
        Create tables if they don't exist yet.  Call once at startup.

        Strategy
        --------
        * If ``SUPABASE_DB_DIRECT_URL`` is set  → connects via psycopg2 and
          runs ``CREATE TABLE IF NOT EXISTS`` for all four tables.  This is
          the recommended path: tables are guaranteed to exist before trading
          starts even on a fresh Supabase project.
        * Otherwise  → does a lightweight REST ping to verify connectivity and
          logs a reminder to run ``storage/schema.sql`` manually.

        This call blocks until complete; it is safe to call before starting
        the trading loop.
        """
        if not self.enabled:
            return

        done = threading.Event()
        self._queue.put(("_ensure_tables_or_ping", {}, done))
        if not done.wait(timeout=30):
            logger.warning(
                "ensure_tables timed out (30 s). "
                "DB writes will continue in the background."
            )

    def save_candle(self, candle: "Candle") -> None:
        """Queue a completed 1-minute OHLC candle for persistence."""
        if not self.enabled:
            return
        self._queue.put(("_upsert_candle", {
            "timestamp": candle.timestamp.isoformat(),
            "symbol": config.SYMBOL,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
        }, None))

    def save_tick(self, price: float, timestamp: datetime) -> None:
        """Queue a raw price tick for persistence."""
        if not self.enabled:
            return
        self._queue.put(("_insert_tick", {
            "timestamp": timestamp.isoformat(),
            "symbol": config.SYMBOL,
            "price": price,
        }, None))

    def save_signal(
        self,
        signal_id: str,
        strategy: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        setup_time: datetime | None,
        signal_time: datetime | None,
    ) -> None:
        """Queue an EMA breakout signal for persistence."""
        if not self.enabled:
            return
        self._queue.put(("_upsert_signal", {
            "id": signal_id,
            "strategy": strategy,
            "direction": direction,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "setup_time": setup_time.isoformat() if setup_time else None,
            "signal_time": signal_time.isoformat() if signal_time else None,
        }, None))

    def save_trade(self, position: "Position") -> None:
        """Queue a closed position for persistence."""
        if not self.enabled:
            return
        record = position.to_dict()
        # Convert empty strings back to None for nullable timestamp / price cols
        for field in ("setup_time", "entry_time", "exit_time"):
            if record.get(field) == "":
                record[field] = None
        if record.get("exit_price") == "":
            record["exit_price"] = None
        self._queue.put(("_upsert_trade", record, None))

    def close(self, timeout: float = 15.0) -> None:
        """
        Drain the write queue and stop the writer thread.
        Call this from the shutdown handler before exiting.
        """
        if not self.enabled:
            return
        logger.info(
            "Supabase repository closing — draining queue (%d items)...",
            self._queue.qsize(),
        )
        self._queue.put(_STOP)
        if self._thread:
            self._thread.join(timeout=timeout)
        logger.info("Supabase writer thread stopped.")

    # ------------------------------------------------------------------
    # Background writer loop
    # ------------------------------------------------------------------

    def _writer_loop(self) -> None:
        """Daemon thread: dequeues and executes Supabase operations sequentially."""
        while True:
            item = self._queue.get()
            if item is _STOP:
                break
            method_name, payload, event = item
            try:
                getattr(self, method_name)(**payload)
                # Successful write — reset backoff, update health state
                self._reconnect_delay = _DB_RECONNECT_BASE
                if self._health is not None:
                    self._health.set_last_database_success(
                        datetime.now(tz=timezone.utc)
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "DATABASE_DISCONNECTED | Supabase write error [%s]: %s",
                    method_name,
                    exc,
                )
                if self._health is not None:
                    self._health.set_database_connected(False)
                    self._health.record_error()
                # Attempt to recover the client connection
                self._attempt_db_reconnect()
            finally:
                if event is not None:
                    event.set()
                self._queue.task_done()

    # ------------------------------------------------------------------
    # Database reconnection (called only from writer thread)
    # ------------------------------------------------------------------

    def _attempt_db_reconnect(self) -> None:
        """
        Attempt to recreate the Supabase client after a write failure.

        Uses exponential backoff (capped at 60 s).  If reconnection succeeds,
        the health state is updated.  If it fails, the error is logged and the
        writer continues (the next enqueued item will try again).
        """
        logger.warning(
            "DATABASE_RECONNECTING | Waiting %.0fs before reconnect attempt…",
            self._reconnect_delay,
        )
        time.sleep(self._reconnect_delay)
        self._reconnect_delay = min(self._reconnect_delay * 2, _DB_RECONNECT_MAX)

        try:
            from supabase import create_client  # noqa: PLC0415

            self._client = create_client(self._url, self._key)
            logger.info(
                "DATABASE_CONNECTED | Supabase client reconnected successfully."
            )
            if self._health is not None:
                self._health.set_database_connected(True)
                self._health.set_last_database_success(
                    datetime.now(tz=timezone.utc)
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "DATABASE_RECONNECTING | Reconnect failed: %s — will retry on next write.",
                exc,
            )
            if self._health is not None:
                self._health.record_error()

    # ------------------------------------------------------------------
    # Internal Supabase operations  (called only from writer thread)
    # ------------------------------------------------------------------

    def _ensure_tables_or_ping(self) -> None:
        """
        Try to create tables via psycopg2 (direct Postgres connection).
        Falls back to a REST ping if no direct URL is configured.
        """
        direct_url = config.SUPABASE_DB_DIRECT_URL
        if direct_url:
            self._create_tables_via_psycopg2(direct_url)
        else:
            # No direct URL — just verify the REST connection is alive
            self._check_connectivity()
            logger.info(
                "Supabase connected (REST only).  "
                "Tables were NOT auto-created — "
                "set SUPABASE_DB_DIRECT_URL or run storage/schema.sql manually."
            )

    def _create_tables_via_psycopg2(self, dsn: str) -> None:
        """Run CREATE TABLE IF NOT EXISTS for all four tables using psycopg2."""
        try:
            import psycopg2  # noqa: PLC0415
        except ImportError:
            logger.error(
                "psycopg2 is not installed — cannot auto-create tables. "
                "Run: pip install psycopg2-binary"
            )
            return

        try:
            conn = psycopg2.connect(dsn, connect_timeout=15)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(_DDL_CANDLES)
                cur.execute(_DDL_TICKS)
                cur.execute(_DDL_SIGNALS)
                cur.execute(_DDL_TRADES)
                cur.execute(_DDL_VIEW_DAILY_PNL)
                cur.execute(_DDL_TRIGGERS)
            conn.close()
            logger.info(
                "Supabase tables verified / created — "
                "candles, ticks, signals, trades are ready."
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to auto-create Supabase tables: %s — "
                "run storage/schema.sql manually in Supabase SQL Editor.",
                exc,
            )

    def _check_connectivity(self) -> None:
        """Lightweight REST ping: fetch at most 1 row from candles."""
        try:
            self._client.table("candles").select("id").limit(1).execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Supabase connectivity check failed: %s  "
                "(Tables may not exist yet — run storage/schema.sql)",
                exc,
            )

    def _upsert_candle(
        self,
        timestamp: str,
        symbol: str,
        open: float,
        high: float,
        low: float,
        close: float,
    ) -> None:
        self._client.table("candles").upsert(
            {
                "timestamp": timestamp,
                "symbol": symbol,
                "open": open,
                "high": high,
                "low": low,
                "close": close,
            },
            on_conflict="timestamp,symbol",
        ).execute()

    def _insert_tick(
        self,
        timestamp: str,
        symbol: str,
        price: float,
    ) -> None:
        self._client.table("ticks").insert(
            {"timestamp": timestamp, "symbol": symbol, "price": price}
        ).execute()

    def _upsert_signal(
        self,
        id: str,
        strategy: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        setup_time: str | None,
        signal_time: str | None,
    ) -> None:
        self._client.table("signals").upsert(
            {
                "id": id,
                "strategy": strategy,
                "direction": direction,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "setup_time": setup_time,
                "signal_time": signal_time,
            },
            on_conflict="id",
        ).execute()

    def _upsert_trade(self, **record) -> None:
        self._client.table("trades").upsert(
            record,
            on_conflict="id",
        ).execute()


# ---------------------------------------------------------------------------
# DDL — executed once at startup via psycopg2 (CREATE TABLE IF NOT EXISTS)
# ---------------------------------------------------------------------------

_DDL_CANDLES = """
CREATE TABLE IF NOT EXISTS candles (
    id        BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    symbol    TEXT        NOT NULL DEFAULT 'XAU/USD',
    open      NUMERIC(12,5),
    high      NUMERIC(12,5),
    low       NUMERIC(12,5),
    close     NUMERIC(12,5),
    UNIQUE (timestamp, symbol)
);
"""

_DDL_TICKS = """
CREATE TABLE IF NOT EXISTS ticks (
    id        BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    symbol    TEXT        NOT NULL DEFAULT 'XAU/USD',
    price     NUMERIC(12,5) NOT NULL
);
CREATE INDEX IF NOT EXISTS ticks_timestamp_idx ON ticks (timestamp DESC);
"""

_DDL_SIGNALS = """
CREATE TABLE IF NOT EXISTS signals (
    id           UUID PRIMARY KEY,
    strategy     TEXT        NOT NULL,
    direction    TEXT        NOT NULL,
    entry_price  NUMERIC(12,5),
    stop_loss    NUMERIC(12,5),
    setup_time   TIMESTAMPTZ,
    signal_time  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
"""

_DDL_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id           UUID PRIMARY KEY,
    strategy     TEXT          NOT NULL,
    side         TEXT          NOT NULL,
    lot          NUMERIC(8,4),
    entry_price  NUMERIC(12,5),
    stop_loss    NUMERIC(12,5),
    take_profit  NUMERIC(12,5),
    risk_reward  NUMERIC(6,2),
    setup_time   TIMESTAMPTZ,
    entry_time   TIMESTAMPTZ,
    exit_time    TIMESTAMPTZ,
    exit_price   NUMERIC(12,5),
    status       TEXT,
    pnl          NUMERIC(12,4),
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
"""

_DDL_VIEW_DAILY_PNL = """
CREATE OR REPLACE VIEW daily_pnl AS
SELECT
    DATE(exit_time AT TIME ZONE 'Asia/Kolkata') AS trade_date,
    strategy,
    COUNT(*)                                    AS trade_count,
    SUM(pnl)                                    AS total_pnl,
    SUM(CASE WHEN status = 'CLOSED_TP' THEN 1 ELSE 0 END) AS wins,
    SUM(CASE WHEN status = 'CLOSED_SL' THEN 1 ELSE 0 END) AS losses
FROM trades
WHERE exit_time IS NOT NULL
GROUP BY 1, 2
ORDER BY 1 DESC, 2;
"""

_DDL_TRIGGERS = """
CREATE OR REPLACE FUNCTION notify_chart_event()
RETURNS TRIGGER AS $$
DECLARE
    event_type TEXT;
    payload JSON;
BEGIN
    IF TG_TABLE_NAME = 'ticks' THEN
        event_type := 'tick';
        payload := json_build_object(
            'instrument', NEW.symbol,
            'price', NEW.price
        );
    ELSIF TG_TABLE_NAME = 'candles' THEN
        event_type := 'candle_update';
        payload := json_build_object(
            'instrument', NEW.symbol,
            'open', NEW.open,
            'high', NEW.high,
            'low', NEW.low,
            'close', NEW.close
        );
    ELSIF TG_TABLE_NAME = 'signals' THEN
        event_type := 'signal';
        payload := json_build_object(
            'strategy_id', NEW.strategy,
            'type', NEW.direction,
            'price', NEW.entry_price
        );
    ELSIF TG_TABLE_NAME = 'trades' THEN
        event_type := 'trade';
        payload := json_build_object(
            'strategy_id', NEW.strategy,
            'side', NEW.side,
            'status', NEW.status,
            'profit_loss', NEW.pnl
        );
    END IF;

    -- Publish event
    PERFORM pg_notify('chart_events', json_build_object(
        'event', event_type,
        'timestamp', CASE WHEN TG_TABLE_NAME = 'candles' THEN NEW.timestamp
                          WHEN TG_TABLE_NAME = 'ticks' THEN NEW.timestamp
                          ELSE CURRENT_TIMESTAMP END,
        'data', payload
    )::text);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_notify_ticks ON ticks;
CREATE TRIGGER trigger_notify_ticks
AFTER INSERT ON ticks
FOR EACH ROW EXECUTE FUNCTION notify_chart_event();

DROP TRIGGER IF EXISTS trigger_notify_candles ON candles;
CREATE TRIGGER trigger_notify_candles
AFTER INSERT OR UPDATE ON candles
FOR EACH ROW EXECUTE FUNCTION notify_chart_event();

DROP TRIGGER IF EXISTS trigger_notify_signals ON signals;
CREATE TRIGGER trigger_notify_signals
AFTER INSERT ON signals
FOR EACH ROW EXECUTE FUNCTION notify_chart_event();

DROP TRIGGER IF EXISTS trigger_notify_trades ON trades;
CREATE TRIGGER trigger_notify_trades
AFTER INSERT OR UPDATE ON trades
FOR EACH ROW EXECUTE FUNCTION notify_chart_event();
"""
