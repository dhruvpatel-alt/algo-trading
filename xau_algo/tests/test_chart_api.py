import os
import sys
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient
from xau_algo.api.server import create_app
from xau_algo.api.schemas import (
    ChartResponse, StrategyInfo, StrategyPerformance, StrategyPerformanceResponse
)

@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)

def test_schemas_instantiation():
    s = StrategyInfo(id="EMA20Strategy", name="EMA 20", description="Test", color="#22c55e")
    assert s.id == "EMA20Strategy"
    
    p = StrategyPerformance(
        strategy_id="EMA20Strategy",
        total_trades=10,
        winning_trades=6,
        losing_trades=4,
        win_rate=60.0,
        total_pnl=150.5,
        gross_profit=300.0,
        gross_loss=149.5,
        profit_factor=2.01,
        open_positions=1
    )
    assert p.win_rate == 60.0

@patch("xau_algo.api.chart_api.get_available_strategies")
def test_list_strategies_endpoint(mock_get_strats, client):
    mock_get_strats.return_value = [
        StrategyInfo(id="EMA20Strategy", name="EMA 20 Breakout", color="#22c55e"),
        StrategyInfo(id="EMA50Strategy", name="EMA 50 Breakout", color="#3b82f6")
    ]
    response = client.get("/api/v1/strategies")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["id"] == "EMA20Strategy"

@patch("xau_algo.api.chart_api.get_strategies_performance")
def test_performance_endpoint(mock_get_perf, client):
    mock_get_perf.return_value = StrategyPerformanceResponse(
        timestamp=datetime.now(timezone.utc),
        strategies=[
            StrategyPerformance(strategy_id="EMA20Strategy", total_trades=5, win_rate=80.0, total_pnl=250.0)
        ]
    )
    response = client.get("/api/v1/strategies/performance")
    assert response.status_code == 200
    data = response.json()
    assert "strategies" in data
    assert data["strategies"][0]["strategy_id"] == "EMA20Strategy"
