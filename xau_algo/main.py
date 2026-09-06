"""
xau_algo/main.py
=================
Entry point for both paper trading (live) and backtesting.

Usage
-----
  # Paper trading (live WebSocket feed)
  python main.py --mode paper

  # Backtest last 7 days of data
  python main.py --mode backtest --days 7

  # Backtest last 30 days
  python main.py --mode backtest --days 30
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

import pytz

# Ensure xau_algo package is importable when running from inside the directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xau_algo import config
from xau_algo.data.candle_builder import CandleBuilder
from xau_algo.data.twelve_data_client import TwelveDataClient
from xau_algo.data.websocket_client import TwelveDataWebSocketClient
from xau_algo.health import HealthState, HealthServer
from xau_algo.strategies.base_strategy import Candle
from xau_algo.strategies.ema20_strategy import EMA20Strategy
from xau_algo.strategies.ema50_strategy import EMA50Strategy
from xau_algo.trading.daily_target import DailyTargetGuard
from xau_algo.trading.paper_broker import PaperBroker
from xau_algo.storage.trade_repository import TradeRepository
from xau_algo.storage.supabase_repository import SupabaseRepository

_IST = pytz.timezone(config.TIMEZONE)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    os.makedirs(config.LOG_DIR, exist_ok=True)

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt=datefmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        ],
    )

    # Suppress noisy third-party loggers
    logging.getLogger("websocket").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paper trading mode
# ---------------------------------------------------------------------------

def run_paper_trading() -> None:
    """
    Live paper trading using Twelve Data WebSocket.

    Architecture:
      WebSocket -> CandleBuilder -> {EMA20Strategy, EMA50Strategy}
                                 -> PaperBroker (SL/TP via on_price)
      All four components also write to SupabaseRepository (async queue).

    Health:
      HealthState (in-memory) <- updated by WebSocket, CandleBuilder, Supabase
      HealthServer (FastAPI)  <- reads HealthState, serves /health endpoint
    """
    logger.info("=" * 60)
    logger.info("APPLICATION_STARTED | XAU/USD Paper Trading System starting...")
    logger.info("Symbol: %s | Timeframe: %s", config.SYMBOL, config.TIMEFRAME)
    logger.info("EMA Periods: EMA%d | EMA%d", config.EMA20_PERIOD, config.EMA50_PERIOD)
    logger.info("Initial Capital: $%.2f", config.INITIAL_CAPITAL)
    logger.info(
        "Daily Target: %.1f%% ($%.2f)",
        config.DAILY_TARGET_PERCENT,
        config.INITIAL_CAPITAL * config.DAILY_TARGET_PERCENT / 100,
    )
    logger.info("Health API: http://0.0.0.0:%d/health", config.HEALTH_API_PORT)
    logger.info("=" * 60)

    # ------------------------------------------------------------------
    # Health state (in-memory — no I/O)
    # ------------------------------------------------------------------
    health_state = HealthState(
        symbol=config.SYMBOL,
        trading_mode="paper",
    )

    # ------------------------------------------------------------------
    # Health HTTP server (FastAPI — background daemon thread)
    # ------------------------------------------------------------------
    health_server = HealthServer(
        health_state=health_state,
        port=config.HEALTH_API_PORT,
    )
    health_server.start()

    # ------------------------------------------------------------------
    # Supabase (optional — gracefully disabled when URL is not set)
    # ------------------------------------------------------------------
    supabase_repo = SupabaseRepository(health_state=health_state)
    supabase_repo.ensure_tables()   # blocks until tables exist (no-op if disabled)

    repo = TradeRepository(supabase_repo=supabase_repo)
    broker = PaperBroker(on_trade_closed=repo.save_trade)
    daily_guard = DailyTargetGuard()

    ema20 = EMA20Strategy(broker=broker, daily_guard=daily_guard, supabase_repo=supabase_repo)
    ema50 = EMA50Strategy(broker=broker, daily_guard=daily_guard, supabase_repo=supabase_repo)

    current_day: list[str] = [None]  # mutable container for closure

    def on_candle_closed(candle: Candle) -> None:
        """Called when a 1-minute candle completes."""
        # Check for session start / daily reset
        ts_ist = candle.timestamp.astimezone(_IST)
        day_str = ts_ist.strftime("%Y-%m-%d")

        if day_str != current_day[0]:
            weekday = ts_ist.weekday()
            h, m = map(int, config.START_TIME.split(":"))
            if weekday in config.TRADING_DAYS and (ts_ist.hour * 60 + ts_ist.minute) >= h * 60 + m:
                current_day[0] = day_str
                broker.reset_daily()
                daily_guard.reset(current_balance=broker.balance)
                logger.info(
                    "=== Trading session started: %s (%s) ===",
                    day_str,
                    ts_ist.strftime("%A"),
                )

        # Feed to both strategies (fully independent)
        ema20.on_candle_closed(candle)
        ema50.on_candle_closed(candle)

        # Update strategy execution timestamp in health state
        health_state.set_last_strategy_execution(datetime.now(tz=timezone.utc))

        # Log account summary every candle
        summary = broker.summary()
        logger.debug("Account: %s", summary)

    def on_price_tick(price: float, timestamp: datetime) -> None:
        """Called on every incoming price tick."""
        # Live SL/TP check
        broker.on_price(price, timestamp)
        broker.mark_to_market(price)

        # Live breakout detection for both strategies
        ema20.on_price_tick(price, timestamp)
        ema50.on_price_tick(price, timestamp)

    candle_builder = CandleBuilder(
        on_candle_closed=on_candle_closed,
        on_price_tick=on_price_tick,
        supabase_repo=supabase_repo,
        health_state=health_state,
    )

    ws_client = TwelveDataWebSocketClient(
        on_tick=candle_builder.on_tick,
        symbol=config.SYMBOL,
        supabase_repo=supabase_repo,
        health_state=health_state,
    )

    # ------------------------------------------------------------------
    # Graceful shutdown — SIGTERM / SIGINT
    # ------------------------------------------------------------------
    _shutdown_called = [False]  # guard against double-invocation

    def _shutdown(signum, frame) -> None:
        if _shutdown_called[0]:
            return
        _shutdown_called[0] = True

        logger.info("APPLICATION_STOPPING | Shutdown signal received (signal %d).", signum)

        # 1. Stop new strategy processing — disconnect WebSocket
        logger.info("STRATEGY_STOPPED | Stopping Twelve Data WebSocket feed...")
        ws_client.stop()

        # 2. Flush pending Supabase writes (drain queue, close pool)
        logger.info("Flushing pending Supabase writes...")
        supabase_repo.close()

        # 3. Stop health server
        health_server.stop()

        # 4. Log final account state
        summary = broker.summary()
        logger.info("Final account state: %s", summary)

        logger.info("APPLICATION_STOPPED | Trading bot exited cleanly.")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # ------------------------------------------------------------------
    # Start live feed
    # ------------------------------------------------------------------
    ws_client.start()
    logger.info("STRATEGY_STARTED | WebSocket live feed started. Press Ctrl+C to stop.")

    # Keep main thread alive — heartbeat every 60 s
    while True:
        time.sleep(60)
        summary = broker.summary()
        logger.info(
            "Heartbeat | Balance=%.2f | Equity=%.2f | Open=%d | Daily P&L=%.4f",
            summary["balance"],
            summary["equity"],
            summary["open_positions"],
            summary["daily_pnl"],
        )
        # Update last strategy execution time on heartbeat
        health_state.set_last_strategy_execution(datetime.now(tz=timezone.utc))


# ---------------------------------------------------------------------------
# Backtest mode
# ---------------------------------------------------------------------------

def run_backtest(days: int) -> None:
    """
    Historical backtest using Twelve Data REST API.
    """
    from xau_algo.backtest.engine import BacktestEngine, load_candles_from_api

    logger.info("=" * 60)
    logger.info("XAU/USD Backtest starting…")
    logger.info("Fetching ~%d days of 1-minute data for %s", days, config.SYMBOL)
    logger.info("=" * 60)

    client = TwelveDataClient()
    candles = load_candles_from_api(days=days, client=client)

    if not candles:
        logger.error("No candles fetched. Check API keys and symbol.")
        sys.exit(1)

    logger.info(
        "Loaded %d candles (%s → %s)",
        len(candles),
        candles[0].timestamp.strftime("%Y-%m-%d %H:%M"),
        candles[-1].timestamp.strftime("%Y-%m-%d %H:%M"),
    )

    engine = BacktestEngine(candles=candles)
    summary = engine.run()

    print("\n" + "=" * 60)
    print("BACKTEST SUMMARY")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:<30} {v}")
    print("=" * 60)
    print(f"\nTrades saved to: {config.TRADES_CSV}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(
        description="XAU/USD Algo Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=["paper", "backtest", "api"],
        default="paper",
        help="Run mode: 'paper' for live paper trading, 'backtest' for historical backtest, 'api' to run Chart API server",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to backtest (only used in backtest mode, default: 7)",
    )

    args = parser.parse_args()

    if args.mode == "paper":
        run_paper_trading()
    elif args.mode == "backtest":
        run_backtest(days=args.days)
    elif args.mode == "api":
        from xau_algo.api.server import run_api_server
        run_api_server(port=8000)


if __name__ == "__main__":
    main()
