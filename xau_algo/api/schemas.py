from __future__ import annotations
from datetime import datetime
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field

# ---------------------------------------------------------
# REST API Models
# ---------------------------------------------------------

class InstrumentInfo(BaseModel):
    id: str
    symbol: str
    display_name: str

class RangeInfo(BaseModel):
    start: datetime
    end: datetime

class CandleData(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    tick_count: int = 0
    is_complete: bool = True

class IndicatorData(BaseModel):
    timestamp: datetime
    ema20: Optional[float] = None
    ema50: Optional[float] = None

class SignalData(BaseModel):
    id: str
    strategy_id: str
    type: str  # "BUY" or "SELL"
    timestamp: datetime
    price: Optional[float] = None
    status: str

class TradeData(BaseModel):
    id: str
    strategy_id: str
    side: str  # "BUY" or "SELL"
    
    entry_time: Optional[datetime] = None
    entry_price: Optional[float] = None
    
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    
    volume: float = 0.0
    status: str  # "OPEN" or "CLOSED" etc
    profit_loss: Optional[float] = None

class ChartResponse(BaseModel):
    instrument: InstrumentInfo
    timeframe: str
    range: RangeInfo
    candles: List[CandleData]
    indicators: List[IndicatorData]
    signals: List[SignalData]
    trades: List[TradeData]

# ---------------------------------------------------------
# Strategy & Performance Models
# ---------------------------------------------------------

class StrategyInfo(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    color: Optional[str] = None

class StrategyPerformance(BaseModel):
    strategy_id: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    open_positions: int = 0

class StrategyPerformanceResponse(BaseModel):
    timestamp: datetime
    strategies: List[StrategyPerformance]

# ---------------------------------------------------------
# WebSocket Event Models
# ---------------------------------------------------------

class WSEvent(BaseModel):
    event: str
    timestamp: datetime
    data: Dict[str, Any]

class WSSubscription(BaseModel):
    action: str  # "subscribe" or "unsubscribe"
    instrument: str
    strategy_id: Optional[str] = None
    events: List[str]
