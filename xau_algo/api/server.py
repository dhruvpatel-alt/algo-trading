import asyncio
import logging
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from xau_algo import config
from xau_algo.api.chart_api import router as chart_router
from xau_algo.api.event_listener import listen_to_db_events

logger = logging.getLogger(__name__)

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
