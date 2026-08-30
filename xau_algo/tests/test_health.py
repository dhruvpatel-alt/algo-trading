"""
tests/test_health.py
====================
Unit tests for Health API, HealthState,staleness detection, reconnect logging,
and graceful shutdown.

All 11 required test cases from the specification are implemented here.
Runs without requiring live network connections or real Twelve Data / Supabase instances.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure xau_algo is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from xau_algo.health.state import HealthState
from xau_algo.health.server import _build_app, HealthServer
from xau_algo import config


# ---------------------------------------------------------------------------
# Test 1: /health returns 200 when healthy
# ---------------------------------------------------------------------------
def test_health_returns_200_when_healthy():
    state = HealthState(symbol="XAU/USD", trading_mode="paper")
    state.set_twelve_data_connected(True)
    state.set_database_connected(True)
    state.set_last_tick(datetime.now(tz=timezone.utc))

    app = _build_app(state)
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["application"] == "running"
    assert data["twelve_data"] == "connected"
    assert data["database"] == "connected"
    assert data["market_data"] == "fresh"
    assert data["symbol"] == "XAU/USD"


# ---------------------------------------------------------------------------
# Test 2: /health returns 503 when Twelve Data is disconnected
# ---------------------------------------------------------------------------
def test_health_returns_503_when_twelve_data_disconnected():
    state = HealthState()
    state.set_twelve_data_connected(False)
    state.set_database_connected(True)
    state.set_last_tick(datetime.now(tz=timezone.utc))

    app = _build_app(state)
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "unhealthy"
    assert data["twelve_data"] == "disconnected"


# ---------------------------------------------------------------------------
# Test 3: /health returns 503 when market data is stale
# ---------------------------------------------------------------------------
def test_health_returns_503_when_market_data_stale():
    state = HealthState()
    state.set_twelve_data_connected(True)
    state.set_database_connected(True)
    
    # Tick from 200 seconds ago (> MAX_TICK_STALENESS_SECONDS = 120)
    stale_time = datetime.now(tz=timezone.utc) - timedelta(seconds=200)
    state.set_last_tick(stale_time)

    app = _build_app(state)
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "unhealthy"
    assert data["twelve_data"] == "stale"
    assert data["market_data"] == "stale"
    assert data["last_tick_seconds_ago"] >= 195


# ---------------------------------------------------------------------------
# Test 4: /health reports database disconnected (and 503)
# ---------------------------------------------------------------------------
def test_health_reports_database_disconnected():
    state = HealthState()
    state.set_twelve_data_connected(True)
    state.set_database_connected(False)
    state.set_last_tick(datetime.now(tz=timezone.utc))

    app = _build_app(state)
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "unhealthy"
    assert data["database"] == "disconnected"


# ---------------------------------------------------------------------------
# Test 5: Health state updates after a tick
# ---------------------------------------------------------------------------
def test_health_state_updates_after_tick():
    state = HealthState()
    tick_time = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)
    state.set_last_tick(tick_time)
    
    assert state.last_tick_time == tick_time


# ---------------------------------------------------------------------------
# Test 6: Health state updates after candle creation
# ---------------------------------------------------------------------------
def test_health_state_updates_after_candle():
    state = HealthState()
    candle_time = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)
    state.set_last_candle(candle_time)

    assert state.last_candle_time == candle_time


# ---------------------------------------------------------------------------
# Test 7: Twelve Data reconnect logic updates counters
# ---------------------------------------------------------------------------
def test_twelve_data_reconnect_counters():
    state = HealthState()
    assert state.reconnect_count == 0
    state.record_reconnect()
    assert state.reconnect_count == 1
    state.record_error()
    assert state.error_count == 1


# ---------------------------------------------------------------------------
# Test 8: Database reconnect logic / failure handling
# ---------------------------------------------------------------------------
def test_database_reconnect_state_handling():
    state = HealthState()
    state.set_database_connected(True)
    assert state.database_connected is True

    # Simulate write error
    state.set_database_connected(False)
    state.record_error()
    assert state.database_connected is False
    assert state.error_count == 1

    # Simulate successful reconnect / write
    now = datetime.now(tz=timezone.utc)
    state.set_last_database_success(now)
    assert state.database_connected is True


# ---------------------------------------------------------------------------
# Test 9: Graceful SIGTERM handling (HealthServer teardown check)
# ---------------------------------------------------------------------------
def test_graceful_shutdown_server():
    state = HealthState()
    server = HealthServer(health_state=state, port=8888)
    server.start()
    time.sleep(0.1)
    # Stop cleanly
    server.stop()
    assert server._thread is not None and not server._thread.is_alive()


# ---------------------------------------------------------------------------
# Test 10: No secrets appear in health response
# ---------------------------------------------------------------------------
def test_no_secrets_in_health_response():
    state = HealthState()
    state.set_twelve_data_connected(True)
    state.set_database_connected(True)
    state.set_last_tick(datetime.now(tz=timezone.utc))

    app = _build_app(state)
    client = TestClient(app)

    response = client.get("/health")
    raw_json_str = response.text.lower()

    # Check for keys / passwords / secrets
    forbidden_terms = [
        "key",
        "password",
        "secret",
        "token",
        "postgres://",
        "postgresql://",
        "api_key",
        "apikey",
        "6f2c06e6",  # sample key fragment from env
    ]
    for term in forbidden_terms:
        assert term not in raw_json_str, f"Forbidden term '{term}' found in health response"


# ---------------------------------------------------------------------------
# Test 11: /health/live and /health/ready endpoints
# ---------------------------------------------------------------------------
def test_liveness_and_readiness_endpoints():
    state = HealthState()
    app = _build_app(state)
    client = TestClient(app)

    # Liveness is always 200
    live_res = client.get("/health/live")
    assert live_res.status_code == 200
    assert live_res.json()["status"] == "alive"

    # Readiness is 503 initially (not connected)
    ready_res = client.get("/health/ready")
    assert ready_res.status_code == 503
    assert ready_res.json()["ready"] is False

    # Mark connected
    state.set_twelve_data_connected(True)
    state.set_database_connected(True)
    ready_res_2 = client.get("/health/ready")
    assert ready_res_2.status_code == 200
    assert ready_res_2.json()["ready"] is True
