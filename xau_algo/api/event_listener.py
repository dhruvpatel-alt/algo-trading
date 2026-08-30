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

async def listen_to_db_events():
    """
    Listens to PostgreSQL NOTIFY events on the 'chart_events' channel.
    This requires a trigger in PostgreSQL to notify this channel.
    """
    logger.info("Starting PostgreSQL event listener on channel 'chart_events'")
    
    try:
        conn = _get_listen_connection()
        cur = conn.cursor()
        cur.execute("LISTEN chart_events;")
        
        while True:
            # We use select to wait for I/O on the connection
            # Use a small timeout to allow asyncio to yield to other tasks
            await asyncio.sleep(0.1)
            
            if select.select([conn], [], [], 0.5) == ([], [], []):
                continue
                
            conn.poll()
            while conn.notifies:
                notify = conn.notifies.pop(0)
                try:
                    payload = json.loads(notify.payload)
                    # Convert to WSEvent and broadcast
                    event_type = payload.get("event")
                    timestamp = payload.get("timestamp")
                    if not timestamp:
                        timestamp = datetime.now(timezone.utc).isoformat()
                    
                    data = payload.get("data", {})
                    
                    # Convert ISO strings to datetime objects for Pydantic if needed,
                    # but Pydantic handles ISO strings during initialization.
                    ws_event = WSEvent(
                        event=event_type,
                        timestamp=timestamp,
                        data=data
                    )
                    
                    # Fire and forget broadcast task
                    asyncio.create_task(manager.broadcast(ws_event))
                    
                except Exception as e:
                    logger.error(f"Error processing NOTIFY payload: {e}")
                    
    except Exception as e:
        logger.error(f"Event listener failed: {e}")
        # In a production system, implement a reconnection backoff here
        await asyncio.sleep(5)
