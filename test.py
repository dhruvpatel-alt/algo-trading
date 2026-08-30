import os
import json
import websocket
from dotenv import load_dotenv


load_dotenv()

API_KEY = os.getenv("TWELVE_DATA_API_KEY_1")

if not API_KEY:
    raise ValueError("TWELVE_DATA_API_KEY_1 not found in .env")


WS_URL = (
    "wss://ws.twelvedata.com/v1/quotes/price"
    f"?apikey={API_KEY}"
)


def on_open(ws):
    print("=" * 60)
    print("WebSocket connected")
    print("=" * 60)

    subscribe_message = {
        "action": "subscribe",
        "params": {
            "symbols": "XAU/USD"
        }
    }

    ws.send(json.dumps(subscribe_message))

    print("Subscribed to XAU/USD")
    print("Waiting for live prices...\n")


def on_message(ws, message):
    try:
        data = json.loads(message)

        print(data)

    except json.JSONDecodeError:
        print("Raw message:", message)


def on_error(ws, error):
    print("\nWebSocket ERROR:")
    print(error)


def on_close(ws, close_status_code, close_msg):
    print("\nWebSocket closed")
    print("Status:", close_status_code)
    print("Message:", close_msg)


if __name__ == "__main__":

    print("Connecting to Twelve Data...")
    print("Symbol: XAU/USD")

    ws = websocket.WebSocketApp(
        WS_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )

    ws.run_forever()