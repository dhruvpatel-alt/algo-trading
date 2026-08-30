"""
xau_algo/health/state.py
=========================
Thread-safe, in-memory health state for the trading application.

Design principles
-----------------
* All fields are updated ONLY via the provided setter methods — never by
  direct attribute mutation from outside this class.
* A single ``threading.Lock`` protects the mutable state; reads are cheap
  (snapshot via ``as_dict``).
* No I/O occurs inside this class — it is a pure in-memory data structure.
* Secrets (API keys, passwords, connection strings) are NEVER stored here.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any


class HealthState:
    """
    Centralised, thread-safe health state for the xau_algo trading system.

    All ``set_*`` methods are safe to call from any thread.
    ``as_dict()`` returns an immutable snapshot suitable for JSON serialisation.
    """

    def __init__(self, symbol: str = "XAU/USD", trading_mode: str = "paper") -> None:
        self._lock = threading.Lock()

        # Startup timestamp — immutable after construction
        self._application_started_at: datetime = datetime.now(tz=timezone.utc)

        # Connection flags
        self._twelve_data_connected: bool = False
        self._database_connected: bool = False

        # Last-event timestamps (None until first event)
        self._last_tick_time: datetime | None = None
        self._last_candle_time: datetime | None = None
        self._last_strategy_execution_time: datetime | None = None
        self._last_database_success_time: datetime | None = None
        self._last_twelve_data_message_time: datetime | None = None

        # Metadata (safe to expose — no secrets)
        self._current_symbol: str = symbol
        self._current_trading_mode: str = trading_mode

        # Counters
        self._error_count: int = 0
        self._reconnect_count: int = 0

    # ------------------------------------------------------------------
    # Setters (all thread-safe)
    # ------------------------------------------------------------------

    def set_twelve_data_connected(self, connected: bool) -> None:
        with self._lock:
            self._twelve_data_connected = connected

    def set_database_connected(self, connected: bool) -> None:
        with self._lock:
            self._database_connected = connected

    def set_last_tick(self, timestamp: datetime) -> None:
        """Update both last_tick_time and last_twelve_data_message_time."""
        with self._lock:
            self._last_tick_time = timestamp
            self._last_twelve_data_message_time = timestamp

    def set_last_candle(self, timestamp: datetime) -> None:
        with self._lock:
            self._last_candle_time = timestamp

    def set_last_strategy_execution(self, timestamp: datetime) -> None:
        with self._lock:
            self._last_strategy_execution_time = timestamp

    def set_last_database_success(self, timestamp: datetime) -> None:
        with self._lock:
            self._last_database_success_time = timestamp
            self._database_connected = True

    def record_error(self) -> None:
        with self._lock:
            self._error_count += 1

    def record_reconnect(self) -> None:
        with self._lock:
            self._reconnect_count += 1

    # ------------------------------------------------------------------
    # Snapshot — safe to call from the HTTP handler thread
    # ------------------------------------------------------------------

    def as_dict(self, max_staleness_seconds: int = 120) -> dict[str, Any]:
        """
        Return an immutable snapshot of the current health state.

        Parameters
        ----------
        max_staleness_seconds :
            Threshold (seconds) after which Twelve Data market data is
            considered stale even if the WebSocket appears connected.

        Returns
        -------
        dict with keys:
            status                    "healthy" | "unhealthy"
            application               "running"
            twelve_data               "connected" | "disconnected" | "stale"
            database                  "connected" | "disconnected"
            market_data               "fresh" | "stale" | "no_data"
            symbol                    e.g. "XAU/USD"
            trading_mode              e.g. "paper"
            uptime_seconds            int
            last_tick_seconds_ago     int | None
            last_candle_seconds_ago   int | None
            error_count               int
            reconnect_count           int
        """
        with self._lock:
            now = datetime.now(tz=timezone.utc)
            uptime = int((now - self._application_started_at).total_seconds())

            # --- Twelve Data status ---
            if not self._twelve_data_connected:
                td_status = "disconnected"
            elif self._last_twelve_data_message_time is None:
                td_status = "connected"   # connected but no message yet
            else:
                age = (now - self._last_twelve_data_message_time).total_seconds()
                td_status = "stale" if age > max_staleness_seconds else "connected"

            # --- Market data status ---
            if self._last_tick_time is None:
                market_data = "no_data"
            else:
                tick_age = (now - self._last_tick_time).total_seconds()
                market_data = "stale" if tick_age > max_staleness_seconds else "fresh"

            # --- Database status ---
            db_status = "connected" if self._database_connected else "disconnected"

            # --- Overall status ---
            healthy = (
                td_status == "connected"
                and market_data in ("fresh", "no_data")   # no_data ok during warmup
                and db_status == "connected"
            )
            overall = "healthy" if healthy else "unhealthy"

            # --- Compute ages (safe ints, never negative) ---
            def _age(ts: datetime | None) -> int | None:
                if ts is None:
                    return None
                return max(0, int((now - ts).total_seconds()))

            return {
                "status": overall,
                "application": "running",
                "twelve_data": td_status,
                "database": db_status,
                "market_data": market_data,
                "symbol": self._current_symbol,
                "trading_mode": self._current_trading_mode,
                "uptime_seconds": uptime,
                "last_tick_seconds_ago": _age(self._last_tick_time),
                "last_candle_seconds_ago": _age(self._last_candle_time),
                "last_strategy_execution_seconds_ago": _age(
                    self._last_strategy_execution_time
                ),
                "error_count": self._error_count,
                "reconnect_count": self._reconnect_count,
            }

    # ------------------------------------------------------------------
    # Direct property access (for unit tests)
    # ------------------------------------------------------------------

    @property
    def twelve_data_connected(self) -> bool:
        with self._lock:
            return self._twelve_data_connected

    @property
    def database_connected(self) -> bool:
        with self._lock:
            return self._database_connected

    @property
    def last_tick_time(self) -> datetime | None:
        with self._lock:
            return self._last_tick_time

    @property
    def last_candle_time(self) -> datetime | None:
        with self._lock:
            return self._last_candle_time

    @property
    def reconnect_count(self) -> int:
        with self._lock:
            return self._reconnect_count

    @property
    def error_count(self) -> int:
        with self._lock:
            return self._error_count
