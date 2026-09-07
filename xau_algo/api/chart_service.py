from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Optional, List, Dict, Any
from xau_algo import config
from xau_algo.api.schemas import (
    ChartResponse, InstrumentInfo, RangeInfo, CandleData,
    IndicatorData, SignalData, TradeData, StrategyInfo,
    StrategyPerformance, StrategyPerformanceResponse
)
from xau_algo.indicators.ema import calculate_ema

logger = logging.getLogger(__name__)

def _get_db_connection():
    direct_url = config.SUPABASE_DB_DIRECT_URL
    if not direct_url:
        raise ValueError("SUPABASE_DB_DIRECT_URL must be set for the Chart API")
    conn = psycopg2.connect(direct_url)
    return conn

def _get_symbol_variants(symbol: str) -> List[str]:
    clean = (symbol or "").strip()
    variants = [clean]
    if clean.upper() in ("XAUUSD", "XAU_USD", "GOLD"):
        variants.append("XAU/USD")
    elif clean == "XAU/USD":
        variants.append("XAUUSD")
    variants.extend([v.upper() for v in variants])
    return list(dict.fromkeys(variants))

def get_chart_data(
    symbol: str,
    timeframe: str,
    start_time: datetime,
    end_time: datetime,
    strategy_id: Optional[str] = None,
    side: Optional[str] = None,
    trade_status: Optional[str] = None,
    include_indicators: bool = True,
    include_signals: bool = True,
    include_trades: bool = True,
    limit: int = 1000
) -> ChartResponse:
    
    # Parse potential comma-separated strategy_id list (e.g. "EMA20Strategy,EMA50Strategy")
    strategy_list = []
    if strategy_id:
        strategy_list = [s.strip() for s in strategy_id.split(",") if s.strip()]
    
    sym_variants = _get_symbol_variants(symbol)

    with _get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. Fetch Candles
            cur.execute("""
                SELECT * FROM candles 
                WHERE symbol = ANY(%s) AND timestamp < %s 
                ORDER BY timestamp DESC 
                LIMIT 100
            """, (sym_variants, start_time))
            warmup_rows = cur.fetchall()
            warmup_rows.reverse() # chronological
            
            # Fetch the main requested candles
            cur.execute("""
                SELECT * FROM candles 
                WHERE symbol = ANY(%s) AND timestamp >= %s AND timestamp <= %s 
                ORDER BY timestamp ASC 
                LIMIT %s
            """, (sym_variants, start_time, end_time, limit))
            main_rows = cur.fetchall()
            
            # Fallback: if main range returned no candles, fetch latest candles from DB
            if not main_rows:
                cur.execute("""
                    SELECT * FROM candles 
                    WHERE symbol = ANY(%s)
                    ORDER BY timestamp DESC 
                    LIMIT %s
                """, (sym_variants, limit))
                main_rows = cur.fetchall()
                main_rows.reverse()
                if main_rows:
                    start_time = main_rows[0]['timestamp']

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
                        volume=0.0,
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
                sig_query = "SELECT * FROM signals WHERE signal_time >= %s AND signal_time <= %s"
                sig_params: List[Any] = [start_time, end_time]
                if strategy_list:
                    sig_query += " AND strategy = ANY(%s)"
                    sig_params.append(strategy_list)
                sig_query += " ORDER BY signal_time ASC LIMIT %s"
                sig_params.append(limit)
                
                cur.execute(sig_query, tuple(sig_params))
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
                trade_query = "SELECT * FROM trades WHERE entry_time <= %s AND (exit_time IS NULL OR exit_time >= %s)"
                trade_params: List[Any] = [end_time, start_time]
                if strategy_list:
                    trade_query += " AND strategy = ANY(%s)"
                    trade_params.append(strategy_list)
                if side:
                    trade_query += " AND side = %s"
                    trade_params.append(side.upper())
                if trade_status:
                    trade_query += " AND status = %s"
                    trade_params.append(trade_status.upper())
                trade_query += " ORDER BY entry_time ASC LIMIT %s"
                trade_params.append(limit)

                cur.execute(trade_query, tuple(trade_params))
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

def get_available_strategies() -> List[StrategyInfo]:
    """Return all available trading strategies for filtering in GUI."""
    strategies = [
        StrategyInfo(id="EMA20Strategy", name="EMA 20 Breakout", description="20-period EMA breakout & trend strategy", color="#22c55e"),
        StrategyInfo(id="EMA50Strategy", name="EMA 50 Breakout", description="50-period EMA trend & pullback strategy", color="#3b82f6"),
    ]
    try:
        with _get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT strategy FROM trades WHERE strategy IS NOT NULL 
                    UNION 
                    SELECT DISTINCT strategy FROM signals WHERE strategy IS NOT NULL
                """)
                rows = cur.fetchall()
                existing_ids = {s.id for s in strategies}
                for row in rows:
                    strat_name = row[0]
                    if strat_name and strat_name not in existing_ids:
                        strategies.append(StrategyInfo(
                            id=strat_name,
                            name=f"{strat_name}",
                            description="Active Trading Strategy",
                            color="#a855f7"
                        ))
    except Exception as e:
        logger.warning(f"Could not fetch dynamic strategies from DB: {e}")
        
    return strategies

def get_strategies_performance(
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None
) -> StrategyPerformanceResponse:
    """Calculate and return aggregate performance metrics for all strategies."""
    if not end_time:
        end_time = datetime.now(timezone.utc)
    if not start_time:
        start_time = end_time - timedelta(days=30)
        
    perf_map: Dict[str, StrategyPerformance] = {}
    
    try:
        with _get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT 
                        strategy,
                        status,
                        pnl
                    FROM trades
                    WHERE entry_time >= %s AND entry_time <= %s
                """, (start_time, end_time))
                
                rows = cur.fetchall()
                for row in rows:
                    strat_id = row['strategy'] or "Unknown"
                    if strat_id not in perf_map:
                        perf_map[strat_id] = StrategyPerformance(strategy_id=strat_id)
                    
                    p = perf_map[strat_id]
                    status = (row['status'] or "OPEN").upper()
                    if status == "OPEN":
                        p.open_positions += 1
                    else:
                        p.total_trades += 1
                        pnl = float(row['pnl']) if row['pnl'] is not None else 0.0
                        p.total_pnl += pnl
                        if pnl > 0:
                            p.winning_trades += 1
                            p.gross_profit += pnl
                        elif pnl < 0:
                            p.losing_trades += 1
                            p.gross_loss += abs(pnl)
                            
                for p in perf_map.values():
                    if p.total_trades > 0:
                        p.win_rate = round((p.winning_trades / p.total_trades) * 100.0, 2)
                    if p.gross_loss > 0:
                        p.profit_factor = round(p.gross_profit / p.gross_loss, 2)
                    elif p.gross_profit > 0:
                        p.profit_factor = round(p.gross_profit, 2)
                        
    except Exception as e:
        logger.error(f"Error fetching strategy performance: {e}")

    # Ensure default strategies exist in performance response even if 0 trades recorded yet
    for default_id in ["EMA20Strategy", "EMA50Strategy"]:
        if default_id not in perf_map:
            perf_map[default_id] = StrategyPerformance(strategy_id=default_id)

    return StrategyPerformanceResponse(
        timestamp=datetime.now(timezone.utc),
        strategies=list(perf_map.values())
    )

