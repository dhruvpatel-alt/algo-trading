import asyncio
import logging
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from xau_algo import config
from xau_algo.api.chart_api import router as chart_router
from xau_algo.api.event_listener import listen_to_db_events

logger = logging.getLogger(__name__)

from datetime import datetime, timezone

_SERVER_START_TIME = datetime.now(timezone.utc)

def get_live_health_snapshot():
    now = datetime.now(timezone.utc)
    uptime = int((now - _SERVER_START_TIME).total_seconds())
    
    db_status = "disconnected"
    market_data = "no_data"
    td_status = "disconnected"
    last_tick_seconds_ago = None
    
    try:
        from xau_algo.api.chart_service import _get_db_connection
        with _get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT max(timestamp) FROM candles WHERE symbol = %s", (config.SYMBOL,))
                row = cur.fetchone()
                db_status = "connected"
                
                if row and row[0]:
                    last_ts = row[0]
                    if last_ts.tzinfo is None:
                        last_ts = last_ts.replace(tzinfo=timezone.utc)
                    last_tick_seconds_ago = max(0, int((now - last_ts).total_seconds()))
                    max_stale = getattr(config, 'MAX_TICK_STALENESS_SECONDS', 120)
                    if last_tick_seconds_ago <= max_stale:
                        market_data = "fresh"
                        td_status = "connected"
                    else:
                        market_data = "stale"
                        td_status = "stale"
                else:
                    td_status = "connected"
                    market_data = "fresh"
    except Exception as e:
        logger.warning(f"Health DB probe error: {e}")
        db_status = "disconnected"

    is_healthy = (db_status == "connected")
    
    return {
        "status": "healthy" if is_healthy else "unhealthy",
        "application": "running",
        "twelve_data": td_status,
        "database": db_status,
        "market_data": market_data,
        "symbol": config.SYMBOL,
        "last_tick_seconds_ago": last_tick_seconds_ago,
        "uptime_seconds": uptime
    }

def create_app() -> FastAPI:
    app = FastAPI(
        title="XAU/USD Chart API",
        description="REST and WebSocket API for the Trading Chart Frontend",
        version="1.0.0",
    )

    # Add CORS middleware to allow frontend connections
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(chart_router, prefix="/api")

    @app.get("/health/live", tags=["health"])
    def liveness():
        return {"status": "alive", "application": "running"}

    @app.get("/health/ready", tags=["health"])
    def readiness():
        snapshot = get_live_health_snapshot()
        return {"ready": snapshot["status"] == "healthy", "twelve_data": snapshot["twelve_data"], "database": snapshot["database"]}

    @app.get("/health", tags=["health"])
    @app.get("/api/health", tags=["health"])
    def health():
        return get_live_health_snapshot()

    @app.on_event("startup")
    async def startup_event():
        logger.info("Starting Chart API Server...")
        # Start the DB event listener as a background task
        asyncio.create_task(listen_to_db_events())

    return app

def run_api_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    logger.info("=" * 60)
    logger.info("APPLICATION_STARTED | Chart API System starting...")
    logger.info(f"API listening on http://{host}:{port}/api/v1/charts/...")
    logger.info("=" * 60)
    
    # We use string import to let uvicorn handle the event loop correctly
    uvicorn.run("xau_algo.api.server:create_app", host=host, port=port, log_level="info", factory=True)
