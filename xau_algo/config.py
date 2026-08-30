"""
xau_algo/config.py
==================
Single source of truth for ALL configurable parameters.
Never scatter these values elsewhere in the codebase.
"""

import os
from dotenv import load_dotenv

# Load .env — check xau_algo/ directory first, then parent (project root)
_DIR = os.path.dirname(__file__)
_ENV_CANDIDATES = [
    os.path.join(_DIR, ".env"),           # xau_algo/.env
    os.path.join(_DIR, "..", ".env"),     # AlgoTrading/.env (parent)
]
for _env_path in _ENV_CANDIDATES:
    if os.path.exists(_env_path):
        load_dotenv(dotenv_path=_env_path)
        break

# ---------------------------------------------------------------------------
# Twelve Data API Keys (loaded from .env — never hardcoded)
# ---------------------------------------------------------------------------
TWELVE_DATA_API_KEYS: list[str] = [
    key
    for key in [
        os.getenv("TWELVE_DATA_API_KEY_1"),
        os.getenv("TWELVE_DATA_API_KEY_2"),
        os.getenv("TWELVE_DATA_API_KEY_3"),
        os.getenv("TWELVE_DATA_API_KEY_4"),
        os.getenv("TWELVE_DATA_API_KEY_5"),
    ]
    if key
]

if not TWELVE_DATA_API_KEYS:
    raise EnvironmentError(
        "No Twelve Data API keys found. "
        "Set at least TWELVE_DATA_API_KEY_1 in your .env file."
    )

# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------
SYMBOL: str = "XAU/USD"
TIMEFRAME: str = "1min"

# ---------------------------------------------------------------------------
# EMA Periods
# ---------------------------------------------------------------------------
EMA20_PERIOD: int = 20
EMA50_PERIOD: int = 50

# ---------------------------------------------------------------------------
# Position sizing (lots per signal)
# ---------------------------------------------------------------------------
LOT_1: float = 0.06   # Risk:Reward 1:1
LOT_2: float = 0.04   # Risk:Reward 1:2
LOT_3: float = 0.02   # Risk:Reward 1:2.5

# ---------------------------------------------------------------------------
# Risk:Reward multiples
# ---------------------------------------------------------------------------
RR_1: float = 1.0
RR_2: float = 2.0
RR_3: float = 2.5

# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------
INITIAL_CAPITAL: float = 10_000.0

# XAU/USD (spot gold): 1 standard lot = 100 troy oz.
# A $1 move in price → $100 PnL per lot.
CONTRACT_SIZE: float = 100.0   # oz per lot

# ---------------------------------------------------------------------------
# Daily target
# ---------------------------------------------------------------------------
DAILY_TARGET_PERCENT: float = 1.0   # 1% of current capital

# ---------------------------------------------------------------------------
# Session timing
# ---------------------------------------------------------------------------
TIMEZONE: str = "Asia/Kolkata"
START_TIME: str = "03:45"           # HH:MM in TIMEZONE

# Days of week to trade (0=Monday … 6=Sunday)
TRADING_DAYS: list[int] = [0, 1, 2, 3, 4]   # Mon–Fri

# ---------------------------------------------------------------------------
# Backtest assumptions (see README §12)
# ---------------------------------------------------------------------------

# Entry is modeled at the breakout level:
#   BUY  entry = setup_candle.high
#   SELL entry = setup_candle.low
ENTRY_AT_BREAKOUT_LEVEL: bool = True

# When both SL and TP are touched inside the same historical OHLC candle,
# assume SL hit first (conservative).
ASSUME_SL_FIRST_ON_CONFLICT: bool = True

# ---------------------------------------------------------------------------
# Twelve Data REST
# ---------------------------------------------------------------------------
TWELVE_DATA_BASE_URL: str = "https://api.twelvedata.com"
TWELVE_DATA_WS_URL: str = "wss://ws.twelvedata.com/v1/quotes/price"

# Max historical candles to fetch per REST call
HISTORICAL_OUTPUTSIZE: int = 5000

# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------
# Project URL: https://[PROJECT-REF].supabase.co
SUPABASE_DB_URL: str = os.getenv("SUPABASE_DB_URL", "")

# Service-role (or anon) key from Supabase dashboard > Settings > API
SUPABASE_DB_KEY: str = os.getenv("SUPABASE_DB_KEY", "")

# Direct PostgreSQL DSN — used ONLY at startup to create tables via psycopg2.
# Find it in: Supabase dashboard > Settings > Database > Connection string > URI
# e.g. postgresql://postgres:[PASSWORD]@db.[REF].supabase.co:5432/postgres
# Leave blank to skip auto-table-creation (run schema.sql manually instead).
SUPABASE_DB_DIRECT_URL: str = os.getenv("SUPABASE_DB_DIRECT_URL", "")
# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR: str = os.path.join(os.path.dirname(__file__), "logs")
LOG_FILE: str = os.path.join(LOG_DIR, "trading.log")
TRADES_CSV: str = os.path.join(LOG_DIR, "trades.csv")

# ---------------------------------------------------------------------------
# Health API
# ---------------------------------------------------------------------------
# Port the HTTP health server listens on (0.0.0.0 — accessible externally)
HEALTH_API_PORT: int = int(os.getenv("HEALTH_API_PORT", "8080"))

# Maximum seconds since the last Twelve Data WebSocket message before the
# system is considered to have stale market data (used by /health endpoint).
MAX_TICK_STALENESS_SECONDS: int = int(os.getenv("MAX_TICK_STALENESS_SECONDS", "120"))
