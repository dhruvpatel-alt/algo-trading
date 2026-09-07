"""
xau_algo/data/websocket_client.py
===================================
Twelve Data WebSocket client for live XAU/USD price streaming.

Features
--------
- Subscribes to XAU/USD price events
- Forwards each price to the CandleBuilder via on_tick()
- Rotates API key on subscription errors
- Exponential backoff on unexpected disconnects
- Automatic reconnection with resubscription after every disconnect
- Health state updates on connect / disconnect / reconnect / tick
- Never logs API key values

Twelve Data WebSocket message format (price event):
{
    "event":          "price",
    "symbol":         "XAU/USD",
    "currency_base":  "Gold Spot",
    "currency_quote": "US Dollar",
    "exchange":       "COMMODITY",
    "type":           "Precious Metal",
    "timestamp":      1234567890,
    "price":          3345.12,
    "day_volume":     12345
}
"""

from __future__ import annotations

import json
import logging
import ssl
import threading
import time
from datetime import datetime, timezone
from typing import Callable, TYPE_CHECKING

import websocket

from xau_algo import config

if TYPE_CHECKING:
    from xau_algo.storage.supabase_repository import SupabaseRepository
    from xau_algo.health.state import HealthState

logger = logging.getLogger(__name__)

_KEY_ERROR_EVENTS = {"subscribe-error", "error"}


class TwelveDataWebSocketClient:
    """
    Persistent WebSocket client for Twelve Data live price feed.

    Parameters
    ----------
    on_tick : Callable[[float, datetime], None]
        Called on every received price tick with (price, timestamp).
    symbol : str
        Trading symbol (default: config.SYMBOL).
    supabase_repo : SupabaseRepository | None
        Optional — async tick persistence.
    health_state : HealthState | None
        Optional — updated on connect / disconnect / tick events.
    """

    def __init__(
        self,
        on_tick: Callable[[float, datetime], None],
        symbol: str | None = None,
        supabase_repo: "SupabaseRepository | None" = None,
        health_state: "HealthState | None" = None,
    ) -> None:
        self._on_tick = on_tick
        self._symbol = symbol or config.SYMBOL
        self._keys = config.TWELVE_DATA_API_KEYS
        self._key_index = 0
        self._supabase = supabase_repo
        self._health = health_state

        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._retry_delay = 2   # seconds; doubles on each failure

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Connect and begin streaming in a background thread."""
        self._stop_event.clear()
        self._connect()

    def stop(self) -> None:
        """Gracefully disconnect."""
        self._stop_event.set()
        if self._ws:
            self._ws.close()
        logger.info("TWELVE_DATA_DISCONNECTED | WebSocket client stopped.")

    # ------------------------------------------------------------------
    def _connect(self) -> None:
        url = f"{config.TWELVE_DATA_WS_URL}?apikey={self._current_key}"

        self._ws = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        self._thread = threading.Thread(
            target=self._ws.run_forever,
            kwargs={
                "ping_interval": 15,
                "ping_timeout": 10,
                "sslopt": {"cert_reqs": ssl.CERT_NONE},
            },
            daemon=True,
        )
        self._thread.start()
        logger.info("WebSocket connecting with KEY_%d…", self._key_index + 1)

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        subscribe_msg = {
            "action": "subscribe",
            "params": {"symbols": self._symbol},
        }
        ws.send(json.dumps(subscribe_msg))
        logger.info(
            "TWELVE_DATA_CONNECTED | WebSocket connected. Subscribed to %s.",
            self._symbol,
        )
        self._retry_delay = 2  # reset on successful connect

        if self._health is not None:
            self._health.set_twelve_data_connected(True)

    def _on_message(self, ws: websocket.WebSocketApp, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Non-JSON WebSocket message: %s", raw[:200])
            return

        event = data.get("event", "")

        if event == "price":
            price = data.get("price")
            ts_unix = data.get("timestamp")

            if price is None or ts_unix is None:
                return

            try:
                price = float(price)
                timestamp = datetime.fromtimestamp(int(ts_unix), tz=timezone.utc)
            except (ValueError, TypeError):
                return

            self._on_tick(price, timestamp)

            # Update health state with latest tick timestamp
            if self._health is not None:
                self._health.set_last_tick(timestamp)

            # Async tick persistence (queue push — never blocks)
            if self._supabase is not None:
                self._supabase.save_tick(price, timestamp)

        elif event == "subscribe-status":
            status = data.get("status", "")
            if status == "ok":
                logger.info("Subscription confirmed for %s.", self._symbol)
            else:
                logger.warning("Subscription status: %s", data)

        elif event in _KEY_ERROR_EVENTS:
            message = data.get("message", "")
            logger.warning(
                "WebSocket KEY_%d error: '%s' — rotating key.",
                self._key_index + 1,
                message,
            )
            if self._health is not None:
                self._health.record_error()
            self._rotate_key()
            ws.close()  # will trigger reconnect in on_close

        else:
            logger.debug("Unhandled WS event '%s': %s", event, data)

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        logger.error("TWELVE_DATA_DISCONNECTED | WebSocket error: %s", error)
        if self._health is not None:
            self._health.set_twelve_data_connected(False)
            self._health.record_error()

    def _on_close(
        self,
        ws: websocket.WebSocketApp,
        close_status_code: int | None,
        close_msg: str | None,
    ) -> None:
        if self._stop_event.is_set():
            return   # intentional close — do not reconnect

        logger.warning(
            "TWELVE_DATA_DISCONNECTED | WebSocket closed (status=%s). "
            "TWELVE_DATA_RECONNECTING in %ds…",
            close_status_code,
            self._retry_delay,
        )

        if self._health is not None:
            self._health.set_twelve_data_connected(False)

        time.sleep(self._retry_delay)
        self._retry_delay = min(self._retry_delay * 2, 60)  # cap at 60s

        if not self._stop_event.is_set():
            logger.info(
                "TWELVE_DATA_RECONNECTING | Attempting reconnect with KEY_%d…",
                self._key_index + 1,
            )
            if self._health is not None:
                self._health.record_reconnect()
            self._connect()

    @property
    def _current_key(self) -> str:
        return self._keys[self._key_index]

    def _rotate_key(self) -> None:
        next_index = (self._key_index + 1) % len(self._keys)
        logger.warning(
            "Rotating WebSocket key: KEY_%d → KEY_%d",
            self._key_index + 1,
            next_index + 1,
        )
        self._key_index = next_index
