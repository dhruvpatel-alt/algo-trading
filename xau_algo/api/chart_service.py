from __future__ import annotations
import logging
from datetime import datetime, timezone
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Optional, List, Dict, Any
from xau_algo import config
from xau_algo.api.schemas import (
    ChartResponse, InstrumentInfo, RangeInfo, CandleData,
    IndicatorData, SignalData, TradeData
)
from xau_algo.indicators.ema import calculate_ema

logger = logging.getLogger(__name__)

def _get_db_connection():
    direct_url = config.SUPABASE_DB_DIRECT_URL
    if not direct_url:
        raise ValueError("SUPABASE_DB_DIRECT_URL must be set for the Chart API")
    conn = psycopg2.connect(direct_url)
    return conn

def get_chart_data(
    symbol: str,
    timeframe: str,
    start_time: datetime,
    end_time: datetime,
    strategy_id: Optional[str] = None,
    include_indicators: bool = True,
    include_signals: bool = True,
    include_trades: bool = True,
    limit: int = 1000
) -> ChartResponse:
    
    # We always need the actual start time and we may need extra data for indicators
    db_start_time = start_time
    if include_indicators:
        # We need extra candles to warm up the EMA (max EMA is 50)
        # Fetching roughly 100 periods before start_time should be enough
        pass # The query will just fetch limit+100 and order properly
    
    with _get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. Fetch Candles
            # Need to fetch candles starting from 100 periods before start_time to warm up EMA
            # We'll fetch them ascending, but we must cap the limit.
            # A safe way is to fetch limit candles within start/end, and separately fetch 100 before start.
            
            # Fetch the previous 100 candles for EMA warmup
            cur.execute("""
                SELECT * FROM candles 
                WHERE symbol = %s AND timestamp < %s 
                ORDER BY timestamp DESC 
                LIMIT 100
            """, (symbol, start_time))
            warmup_rows = cur.fetchall()
            warmup_rows.reverse() # chronological
            
            # Fetch the main requested candles
            cur.execute("""
                SELECT * FROM candles 
                WHERE symbol = %s AND timestamp >= %s AND timestamp <= %s 
                ORDER BY timestamp ASC 
                LIMIT %s
            """, (symbol, start_time, end_time, limit))
            main_rows = cur.fetchall()
            
            all_rows = warmup_rows + main_rows
            
            # Extract closes
            closes = [float(row['close']) for row in all_rows]
            
            # Compute EMA
            if include_indicators:
                ema20_list = calculate_ema(closes, config.EMA20_PERIOD)
                ema50_list = calculate_ema(closes, config.EMA50_PERIOD)
            else:
                ema20_list = [None] * len(all_rows)
                ema50_list = [None] * len(all_rows)
            
            candles = []
            indicators = []
            
            # Filter the main rows
            for i, row in enumerate(all_rows):
                if row['timestamp'] >= start_time:
                    c = CandleData(
                        timestamp=row['timestamp'],
                        open=float(row['open']),
                        high=float(row['high']),
                        low=float(row['low']),
                        close=float(row['close']),
                        volume=0.0, # Not in schema currently? Adjust if needed
                        is_complete=True
                    )
                    candles.append(c)
                    
                    if include_indicators:
                        ind = IndicatorData(
                            timestamp=row['timestamp'],
                            ema20=ema20_list[i],
                            ema50=ema50_list[i]
                        )
                        indicators.append(ind)
                        
            # 2. Fetch Signals
            signals = []
            if include_signals:
                if strategy_id:
                    cur.execute("""
                        SELECT * FROM signals 
                        WHERE signal_time >= %s AND signal_time <= %s AND strategy = %s
                        ORDER BY signal_time ASC LIMIT %s
                    """, (start_time, end_time, strategy_id, limit))
                else:
                    cur.execute("""
                        SELECT * FROM signals 
                        WHERE signal_time >= %s AND signal_time <= %s
                        ORDER BY signal_time ASC LIMIT %s
                    """, (start_time, end_time, limit))
                
                sig_rows = cur.fetchall()
                for row in sig_rows:
                    signals.append(SignalData(
                        id=str(row['id']),
                        strategy_id=row['strategy'],
                        type=row['direction'],
                        timestamp=row['signal_time'],
                        price=float(row['entry_price']) if row['entry_price'] else None,
                        status="TRIGGERED"
                    ))
                    
            # 3. Fetch Trades
            trades = []
            if include_trades:
                if strategy_id:
                    cur.execute("""
                        SELECT * FROM trades 
                        WHERE entry_time <= %s 
                          AND (exit_time IS NULL OR exit_time >= %s)
                          AND strategy = %s
                        ORDER BY entry_time ASC LIMIT %s
                    """, (end_time, start_time, strategy_id, limit))
                else:
                    cur.execute("""
                        SELECT * FROM trades 
                        WHERE entry_time <= %s 
                          AND (exit_time IS NULL OR exit_time >= %s)
                        ORDER BY entry_time ASC LIMIT %s
                    """, (end_time, start_time, limit))
                    
                trade_rows = cur.fetchall()
                for row in trade_rows:
                    trades.append(TradeData(
                        id=str(row['id']),
                        strategy_id=row['strategy'],
                        side=row['side'],
                        entry_time=row['entry_time'],
                        entry_price=float(row['entry_price']) if row['entry_price'] else None,
                        exit_time=row['exit_time'],
                        exit_price=float(row['exit_price']) if row['exit_price'] else None,
                        stop_loss=float(row['stop_loss']) if row['stop_loss'] else None,
                        take_profit=float(row['take_profit']) if row['take_profit'] else None,
                        volume=float(row['lot']) if row['lot'] else 0.0,
                        status=row['status'] if row['status'] else "OPEN",
                        profit_loss=float(row['pnl']) if row['pnl'] else None
                    ))

    inst = InstrumentInfo(
        id=symbol,
        symbol=symbol,
        display_name=f"{symbol} Instrument"
    )
    
    range_info = RangeInfo(
        start=start_time,
        end=end_time
    )

    return ChartResponse(
        instrument=inst,
        timeframe=timeframe,
        range=range_info,
        candles=candles,
        indicators=indicators,
        signals=signals,
        trades=trades
    )
