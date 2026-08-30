"""
xau_algo/health/server.py
==========================
Lightweight FastAPI health server for the xau_algo trading system.

Endpoints
---------
  GET /health        — Full operational status (200 healthy / 503 unhealthy)
  GET /health/live   — Liveness probe: is the Python process alive? (always 200)
  GET /health/ready  — Readiness probe: is the system ready to process data?

Design
------
* Runs in a background **daemon thread** — never blocks the trading loop.
* All responses are built from the in-memory ``HealthState`` snapshot.
* No DB queries, no WS pings inside endpoint handlers.
* No secrets (API keys, passwords, connection strings) are ever exposed.
* HTTP status:
    200  →  healthy / live / ready
    503  →  unhealthy / not ready
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from xau_algo import config

if TYPE_CHECKING:
    from xau_algo.health.state import HealthState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app factory
# ---------------------------------------------------------------------------

def _build_app(health_state: "HealthState") -> FastAPI:
    app = FastAPI(
        title="XAU/USD Trading Bot Health API",
        description=(
            "Operational status endpoints for the XAU/USD paper-trading system. "
            "No trading controls are exposed here."
        ),
        version="1.0.0",
        docs_url=None,   # disable Swagger UI in production
        redoc_url=None,
    )

    @app.get("/health/live", tags=["health"])
    def liveness() -> JSONResponse:
        """
        Liveness probe.

        Returns HTTP 200 if the Python process is alive and the HTTP server
        is responding.  Does NOT check Twelve Data or database connectivity.
        """
        return JSONResponse(
            status_code=200,
            content={"status": "alive", "application": "running"},
        )

    @app.get("/health/ready", tags=["health"])
    def readiness() -> JSONResponse:
        """
        Readiness probe.

        Returns HTTP 200 when the system is ready to process market data:
          - Twelve Data WebSocket is connected and data is fresh
          - Database is available

        Returns HTTP 503 when the system is not yet ready or has degraded.
        """
        snapshot = health_state.as_dict(
            max_staleness_seconds=config.MAX_TICK_STALENESS_SECONDS
        )
        ready = (
            snapshot["twelve_data"] == "connected"
            and snapshot["database"] == "connected"
        )
        status_code = 200 if ready else 503
        return JSONResponse(
            status_code=status_code,
            content={
                "ready": ready,
                "twelve_data": snapshot["twelve_data"],
                "database": snapshot["database"],
            },
        )

    @app.get("/health", tags=["health"])
    def health() -> JSONResponse:
        """
        Full health status.

        Returns HTTP 200 when healthy, HTTP 503 when unhealthy.

        A healthy response requires:
          - Twelve Data WebSocket connected
          - Market data received within MAX_TICK_STALENESS_SECONDS
          - Database connected

        No secrets or internal credentials are included in the response.
        """
        snapshot = health_state.as_dict(
            max_staleness_seconds=config.MAX_TICK_STALENESS_SECONDS
        )
        status_code = 200 if snapshot["status"] == "healthy" else 503
        return JSONResponse(status_code=status_code, content=snapshot)

    return app


# ---------------------------------------------------------------------------
# Health server — runs uvicorn in a daemon thread
# ---------------------------------------------------------------------------

class HealthServer:
    """
    Wraps a FastAPI/uvicorn HTTP server in a background daemon thread.

    The server starts immediately on ``start()`` and stops cleanly on
    ``stop()`` using uvicorn's built-in signal handling.

    Parameters
    ----------
    health_state : HealthState
        Shared, thread-safe health state instance.
    port : int
        TCP port to listen on (default from config: ``HEALTH_API_PORT``).
    host : str
        Bind address (default ``0.0.0.0`` for external UptimeRobot access).
    """

    def __init__(
        self,
        health_state: "HealthState",
        port: int | None = None,
        host: str = "0.0.0.0",
    ) -> None:
        self._health_state = health_state
        self._port = port if port is not None else config.HEALTH_API_PORT
        self._host = host
        self._app = _build_app(health_state)
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the HTTP server in a background daemon thread."""
        uvicorn_config = uvicorn.Config(
            app=self._app,
            host=self._host,
            port=self._port,
            log_level="warning",   # suppress uvicorn access logs; use our logger
            access_log=False,
        )
        self._server = uvicorn.Server(uvicorn_config)

        self._thread = threading.Thread(
            target=self._server.run,
            name="health-server",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Health API started on http://%s:%d/health",
            self._host,
            self._port,
        )

    def stop(self) -> None:
        """Gracefully shut down the HTTP server."""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        logger.info("Health API stopped.")
