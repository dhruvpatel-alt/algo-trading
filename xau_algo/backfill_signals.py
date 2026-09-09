import os
import sys
import uuid
import logging
from datetime import datetime, timezone
import psycopg2
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xau_algo import config
from xau_algo.strategies.base_strategy import Candle
from xau_algo.backtest.engine import BacktestEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill")

from xau_algo.storage.supabase_repository import _DDL_TRIGGERS

def run_backfill():
    direct_url = config.SUPABASE_DB_DIRECT_URL
    if not direct_url:
        print("SUPABASE_DB_DIRECT_URL is not set!")
        return

    conn = psycopg2.connect(direct_url)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Update trigger in PostgreSQL first
    try:
        cur.execute(_DDL_TRIGGERS)
        print("Updated PostgreSQL notify_chart_event trigger successfully.")
    except Exception as trg_err:
        print(f"Trigger update note: {trg_err}")

    # 1. Fetch historical candles from DB
    cur.execute(
        "SELECT timestamp, open, high, low, close FROM candles WHERE symbol = ANY(%s) ORDER BY timestamp ASC",
        (["XAU/USD", "XAUUSD"],)
    )
    rows = cur.fetchall()
    print(f"Loaded {len(rows)} candles from DB for backfill...")

    if not rows:
        print("No candles found in database!")
        return

    candles = [
        Candle(
            timestamp=r["timestamp"] if r["timestamp"].tzinfo else r["timestamp"].replace(tzinfo=timezone.utc),
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
        )
        for r in rows
    ]

    engine = BacktestEngine(candles=candles)

    # Override broker callback to record signals and trades to Supabase DB directly
    def custom_on_trade_closed(position):
        p = position
        t_id = str(uuid.uuid4())
        
        # Save Signal
        cur.execute("""
            INSERT INTO signals (id, strategy, direction, entry_price, stop_loss, setup_time, signal_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (
            t_id,
            p.strategy,
            p.side,
            p.entry_price,
            p.stop_loss,
            p.setup_time,
            p.entry_time
        ))

        # Save Trade
        cur.execute("""
            INSERT INTO trades (id, strategy, side, lot, entry_price, stop_loss, take_profit, setup_time, entry_time, exit_time, exit_price, status, pnl)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (
            t_id,
            p.strategy,
            p.side,
            p.lot,
            p.entry_price,
            p.stop_loss,
            p.take_profit,
            p.setup_time,
            p.entry_time,
            p.exit_time,
            p.exit_price,
            p.status,
            p.pnl
        ))

    engine._broker._on_trade_closed = custom_on_trade_closed

    summary = engine.run()
    print("BACKFILL COMPLETED SUCCESSFULLY!")
    print("Summary:", summary)

if __name__ == "__main__":
    run_backfill()
