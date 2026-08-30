"""
vt_markets/config.py
=====================
All configurable parameters for the VT Markets MT5 implementation.

This module is completely INDEPENDENT of xau_algo/config.py.
Twelve Data is NOT used here.

Environment variables are loaded from vt_markets/.env (or the parent .env
if a vt_markets-specific file is not present).
"""

from __future__ import annotations

import os
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Locate and load .env
# ---------------------------------------------------------------------------
_DIR = os.path.dirname(__file__)
_ENV_CANDIDATES = [
    os.path.join(_DIR, ".env"),           # vt_markets/.env (preferred)
    os.path.join(_DIR, "..", ".env"),     # project root .env
]
for _env_path in _ENV_CANDIDATES:
    if os.path.exists(_env_path):
        load_dotenv(dotenv_path=_env_path, override=False)
        break

# ---------------------------------------------------------------------------
# MT5 Credentials  (never hardcoded — always from environment)
# ---------------------------------------------------------------------------
MT5_LOGIN: int | None = int(os.getenv("MT5_LOGIN", "0")) or None
MT5_PASSWORD: str = os.getenv("MT5_PASSWORD", "")
MT5_SERVER: str = os.getenv("MT5_SERVER", "")

# ---------------------------------------------------------------------------
# Symbol
# ---------------------------------------------------------------------------
MT5_SYMBOL: str = os.getenv("MT5_SYMBOL", "XAUUSD")
TIMEFRAME_MINUTES: int = 1   # 1-minute candles

# ---------------------------------------------------------------------------
# Trading mode safety
# ---------------------------------------------------------------------------
TRADING_MODE: str = os.getenv("TRADING_MODE", "DEMO").upper()          # "DEMO" | "LIVE"
ALLOW_LIVE_TRADING: bool = os.getenv("ALLOW_LIVE_TRADING", "false").lower() == "true"

# ---------------------------------------------------------------------------
# EMA Periods
# ---------------------------------------------------------------------------
EMA20_PERIOD: int = 20
EMA50_PERIOD: int = 50

# ---------------------------------------------------------------------------
# Magic numbers — used to tag MT5 orders per strategy
# ---------------------------------------------------------------------------
EMA20_MAGIC: int = int(os.getenv("EMA20_MAGIC", "20020"))
EMA50_MAGIC: int = int(os.getenv("EMA50_MAGIC", "50050"))

# ---------------------------------------------------------------------------
# Order parameters
# ---------------------------------------------------------------------------
LOT_1: float = 0.06    # 1:1 RR
LOT_2: float = 0.04    # 1:2 RR
LOT_3: float = 0.02    # 1:2.5 RR

RR_1: float = 1.0
RR_2: float = 2.0
RR_3: float = 2.5

# Max allowed price deviation in points (slippage tolerance)
MT5_DEVIATION: int = int(os.getenv("MT5_DEVIATION", "20"))

# ---------------------------------------------------------------------------
# Account / capital
# ---------------------------------------------------------------------------
INITIAL_CAPITAL: float = float(os.getenv("INITIAL_CAPITAL", "10000"))
DAILY_TARGET_PERCENT: float = float(os.getenv("DAILY_TARGET_PERCENT", "1.0"))

# ---------------------------------------------------------------------------
# Session timing
# ---------------------------------------------------------------------------
TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Kolkata")
START_TIME: str = os.getenv("START_TIME", "03:45")   # HH:MM in TIMEZONE

# Days of week to trade (0=Monday … 6=Sunday)
TRADING_DAYS: list[int] = [0, 1, 2, 3, 4]   # Mon–Fri

# ---------------------------------------------------------------------------
# Historical candle fetch
# ---------------------------------------------------------------------------
HISTORICAL_BARS: int = 200   # number of 1-min bars for EMA warmup + recent history

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR: str = os.path.join(_DIR, "logs")
LOG_FILE: str = os.path.join(LOG_DIR, "vt_trading.log")
TRADES_CSV: str = os.path.join(LOG_DIR, "vt_trades.csv")

# ---------------------------------------------------------------------------
# Runtime validation helper
# ---------------------------------------------------------------------------

def is_live_trading_allowed() -> bool:
    """
    Return True ONLY when BOTH conditions are explicitly set:
        TRADING_MODE=LIVE
        ALLOW_LIVE_TRADING=true

    This prevents accidental real-money orders.
    """
    return TRADING_MODE == "LIVE" and ALLOW_LIVE_TRADING


def assert_credentials_present() -> None:
    """Raise EnvironmentError if required MT5 credentials are missing."""
    missing = []
    if not MT5_LOGIN:
        missing.append("MT5_LOGIN")
    if not MT5_PASSWORD:
        missing.append("MT5_PASSWORD")
    if not MT5_SERVER:
        missing.append("MT5_SERVER")
    if missing:
        raise EnvironmentError(
            f"Missing required MT5 credentials: {', '.join(missing)}. "
            f"Set them in vt_markets/.env"
        )
