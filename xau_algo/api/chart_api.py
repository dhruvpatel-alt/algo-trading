from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
import logging
from xau_algo.api.schemas import ChartResponse
from xau_algo.api.chart_service import get_chart_data
from xau_algo.api.websocket_manager import manager

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/v1/charts/{instrument_symbol}", response_model=ChartResponse)
def get_chart(
    instrument_symbol: str,
    timeframe: str = Query("1m", description="Chart timeframe"),
    start_time: Optional[datetime] = Query(None, description="Start time (UTC)"),
    end_time: Optional[datetime] = Query(None, description="End time (UTC)"),
    strategy_id: Optional[str] = Query(None, description="Filter by strategy ID"),
    include_indicators: bool = Query(True, description="Include indicator data"),
    include_signals: bool = Query(True, description="Include signal data"),
    include_trades: bool = Query(True, description="Include trade data"),
    limit: int = Query(1000, description="Max candles to return")
):
    if not end_time:
        end_time = datetime.now(timezone.utc)
    if not start_time:
        # Default to a reasonable window if not provided
        start_time = end_time - timedelta(days=1)
        
    return get_chart_data(
        symbol=instrument_symbol,
        timeframe=timeframe,
        start_time=start_time,
        end_time=end_time,
        strategy_id=strategy_id,
        include_indicators=include_indicators,
        include_signals=include_signals,
        include_trades=include_trades,
        limit=limit
    )

@router.websocket("/v1/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await manager.handle_message(websocket, data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)
