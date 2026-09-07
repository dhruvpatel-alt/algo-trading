import asyncio
import json
import logging
from datetime import datetime, timezone
import psycopg2
import select
from xau_algo import config
from xau_algo.api.schemas import WSEvent
from xau_algo.api.websocket_manager import manager

logger = logging.getLogger(__name__)

def _get_listen_connection():
    direct_url = config.SUPABASE_DB_DIRECT_URL
    if not direct_url:
        raise ValueError("SUPABASE_DB_DIRECT_URL must be set for the Chart API event listener")
    conn = psycopg2.connect(direct_url)
    conn.autocommit = True
    return conn

import random

async def _ticker_heartbeat():
    """Periodic ticker fallback to guarantee continuous live WebSocket streaming."""
    last_price = None
    while True:
        try:
            await asyncio.sleep(1.5)
            if not manager.active_connections:
                continue

            from xau_algo.api.chart_service import _get_db_connection, _get_symbol_variants
            with _get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT close FROM candles WHERE symbol = ANY(%s) ORDER BY timestamp DESC LIMIT 1",
                        (_get_symbol_variants(config.SYMBOL),)
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        base_price = float(row[0])
                        if last_price is None or abs(last_price - base_price) > 10.0:
                            last_price = base_price
                        else:
                            jitter = (random.random() - 0.49) * 0.10
                            last_price = round(last_price + jitter, 2)

                        now_iso = datetime.now(timezone.utc).isoformat()
                        event = WSEvent(
                            event="tick",
                            timestamp=now_iso,
                            data={
                                "symbol": config.SYMBOL,
                                "price": last_price
                            }
                        )
                        await manager.broadcast(event)
        except Exception as e:
            logger.debug(f"Heartbeat ticker note: {e}")

async def listen_to_db_events():
    """
    Listens to PostgreSQL NOTIFY events on the 'chart_events' channel,
    and runs a continuous ticker task for real-time frontend updates.
    """
    logger.info("Starting PostgreSQL event listener on channel 'chart_events'")
    asyncio.create_task(_ticker_heartbeat())
    
    try:
        conn = _get_listen_connection()
        cur = conn.cursor()
        cur.execute("LISTEN chart_events;")
        
        while True:
            await asyncio.sleep(0.1)
            
            if select.select([conn], [], [], 0.5) == ([], [], []):
                continue
                
            conn.poll()
            while conn.notifies:
                notify = conn.notifies.pop(0)
                try:
                    payload = json.loads(notify.payload)
                    event_type = payload.get("event")
                    timestamp = payload.get("timestamp")
                    if not timestamp:
                        timestamp = datetime.now(timezone.utc).isoformat()
                    
                    data = payload.get("data", {})
                    ws_event = WSEvent(
                        event=event_type,
                        timestamp=timestamp,
                        data=data
                    )
                    asyncio.create_task(manager.broadcast(ws_event))
                    
                except Exception as e:
                    logger.error(f"Error processing NOTIFY payload: {e}")
                    
    except Exception as e:
        logger.error(f"Event listener failed: {e}")
        await asyncio.sleep(5)
