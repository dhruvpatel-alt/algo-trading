import json
import logging
import asyncio
from typing import Dict, List, Any, Optional
from fastapi import WebSocket
from xau_algo.api.schemas import WSEvent

logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        # Maps a websocket to its subscription options
        self.active_connections: Dict[WebSocket, Dict[str, Any]] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        # Default empty subscription until client sends subscribe action
        self.active_connections[websocket] = {
            "instrument": None,
            "strategy_id": None,
            "events": []
        }
        logger.info(f"WebSocket connected: {websocket.client}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            del self.active_connections[websocket]
            logger.info(f"WebSocket disconnected: {websocket.client}")

    async def handle_message(self, websocket: WebSocket, message: str):
        try:
            data = json.loads(message)
            if data.get("action") == "subscribe":
                self.active_connections[websocket] = {
                    "instrument": data.get("instrument"),
                    "strategy_id": data.get("strategy_id"),
                    "events": data.get("events", [])
                }
                logger.info(f"Updated subscription for {websocket.client}: {self.active_connections[websocket]}")
            elif data.get("action") == "unsubscribe":
                self.active_connections[websocket] = {
                    "instrument": None,
                    "strategy_id": None,
                    "events": []
                }
        except json.JSONDecodeError:
            logger.error("Invalid JSON received over WebSocket")

    async def broadcast(self, event: WSEvent):
        # We need to broadcast this event to any websocket that is subscribed to it
        event_dict = event.model_dump(mode='json')
        event_str = json.dumps(event_dict)
        
        event_type = event.event
        event_instrument = event.data.get("instrument")
        event_strategy = event.data.get("strategy_id")

        disconnected_clients = []
        for ws, sub in self.active_connections.items():
            if sub["events"] and event_type not in sub["events"]:
                continue
            if sub["instrument"] and event_instrument and sub["instrument"] != event_instrument:
                continue
            if sub["strategy_id"] and event_strategy and str(sub["strategy_id"]) != str(event_strategy):
                continue

            try:
                await ws.send_text(event_str)
            except Exception as e:
                logger.error(f"Error sending message to {ws.client}: {e}")
                disconnected_clients.append(ws)

        for ws in disconnected_clients:
            self.disconnect(ws)

manager = ConnectionManager()
