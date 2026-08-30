"""
vt_markets/main.py
===================
Entry point for the VT Markets MT5 trading system.

Usage
-----
  python -m vt_markets.main

This is completely independent of xau_algo/main.py.
No Twelve Data. No Twelve Data API keys.

Architecture:
  MT5 → MT5MarketData → LiveCandleBuilder
      → (on_tick) → EMA20Strategy.on_tick() & EMA50Strategy.on_tick()
      → (on_candle_closed) → EMA20Strategy.on_candle_closed() & EMA50Strategy.on_candle_closed()
      → (on_signal) → OrderManager.handle_signal()
      → VTMarketsExecutor.buy() / .sell()
      → PositionManager.register_opened()
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from datetime import datetime

import pytz

# Ensure vt_markets is importable when running from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vt_markets import config
from vt_markets.mt5.connection import MT5Connection
from vt_markets.mt5.symbols import SymbolValidator
from vt_markets.mt5.market_data import MT5MarketData, LiveCandleBuilder
from vt_markets.mt5.execution import VTMarketsExecutor
from vt_markets.strategies.ema20_strategy import EMA20Strategy
from vt_markets.strategies.ema50_strategy import EMA50Strategy
from vt_markets.trading.daily_target import VTDailyTargetGuard
from vt_markets.trading.risk_manager import RiskManager
from vt_markets.trading.order_manager import OrderManager
from vt_markets.trading.position_manager import PositionManager
from vt_markets.models.signal import Signal
from vt_markets.models.candle import Candle
from vt_markets.models.tick import Tick

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
    logging.getLogger("MetaTrader5").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    setup_logging()

    # ------------------------------------------------------------------
    # Safety check — refuse to start if credentials are missing
    # ------------------------------------------------------------------
    config.assert_credentials_present()

    logger.info("=" * 60)
    logger.info("VT Markets MT5 Trading System")
    logger.info("Symbol  : %s", config.MT5_SYMBOL)
    logger.info("Mode    : %s | Live allowed: %s",
                config.TRADING_MODE, config.ALLOW_LIVE_TRADING)
    logger.info("EMA20 magic: %d | EMA50 magic: %d",
                config.EMA20_MAGIC, config.EMA50_MAGIC)
    logger.info("Capital : $%.2f | Daily target: %.1f%%",
                config.INITIAL_CAPITAL, config.DAILY_TARGET_PERCENT)
    logger.info("=" * 60)

    # ------------------------------------------------------------------
    # MT5 connection
    # ------------------------------------------------------------------
    conn = MT5Connection(
        login=config.MT5_LOGIN,
        password=config.MT5_PASSWORD,
        server=config.MT5_SERVER,
    )
    conn.connect()

    # Safety gate — verify demo/live mode
    conn.assert_demo_or_allowed(allow_live=config.is_live_trading_allowed())

    # ------------------------------------------------------------------
    # Symbol validation
    # ------------------------------------------------------------------
    symbol_validator = SymbolValidator(config.MT5_SYMBOL)
    symbol = symbol_validator.validate()

    # ------------------------------------------------------------------
    # Executor, risk manager, daily guard
    # ------------------------------------------------------------------
    executor = VTMarketsExecutor(
        symbol=symbol,
        allow_live=config.is_live_trading_allowed(),
        deviation=config.MT5_DEVIATION,
    )
    executor.load_symbol_constraints()
    executor.validate_lots([config.LOT_1, config.LOT_2, config.LOT_3])

    daily_guard = VTDailyTargetGuard()
    risk_manager = RiskManager()

    position_manager = PositionManager(daily_guard=daily_guard, executor=executor)
    recovered = position_manager.recover_from_mt5()
    logger.info("Recovered %d existing position(s) from MT5.", recovered)

    order_manager = OrderManager(
        executor=executor,
        risk_manager=risk_manager,
        on_position_opened=position_manager.register_opened,
    )

    # ------------------------------------------------------------------
    # Signal handler
    # ------------------------------------------------------------------
    def on_signal(sig: Signal) -> None:
        logger.info("Signal: %s", sig)
        order_manager.handle_signal(sig)

    # ------------------------------------------------------------------
    # Strategies (completely independent)
    # ------------------------------------------------------------------
    ema20 = EMA20Strategy(on_signal=on_signal, daily_guard=daily_guard)
    ema50 = EMA50Strategy(on_signal=on_signal, daily_guard=daily_guard)

    # ------------------------------------------------------------------
    # Load historical candles for EMA warmup
    # ------------------------------------------------------------------
    market_data = MT5MarketData(symbol=symbol)
    historical = market_data.get_historical_candles(count=config.HISTORICAL_BARS)
    logger.info("Warming up EMA with %d historical candles…", len(historical))
    for candle in historical[:-1]:   # all but the last (still live)
        ema20.on_candle_closed(candle)
        ema50.on_candle_closed(candle)
    logger.info(
        "Warmup complete. EMA20=%.5f EMA50=%.5f",
        ema20.current_ema or 0,
        ema50.current_ema or 0,
    )

    # ------------------------------------------------------------------
    # Session tracking
    # ------------------------------------------------------------------
    current_day: list[str] = [None]

    def check_session_reset(ts: datetime) -> None:
        ts_ist = ts.astimezone(_IST)
        day_str = ts_ist.strftime("%Y-%m-%d")
        weekday = ts_ist.weekday()
        if day_str != current_day[0] and weekday in config.TRADING_DAYS:
            h, m = map(int, config.START_TIME.split(":"))
            if ts_ist.hour * 60 + ts_ist.minute >= h * 60 + m:
                current_day[0] = day_str
                daily_guard.reset()
                position_manager.sync_with_mt5()
                logger.info(
                    "=== Trading session started: %s (%s) ===",
                    day_str, ts_ist.strftime("%A"),
                )

    # ------------------------------------------------------------------
    # Candle builder callbacks
    # ------------------------------------------------------------------
    def on_candle_closed(candle: Candle) -> None:
        check_session_reset(candle.timestamp)
        ema20.on_candle_closed(candle)
        ema50.on_candle_closed(candle)
        # Sync position state with MT5
        position_manager.sync_with_mt5()

    def on_tick_received(tick: Tick) -> None:
        check_session_reset(tick.timestamp)
        ema20.on_tick(tick)
        ema50.on_tick(tick)

    candle_builder = LiveCandleBuilder(
        on_candle_closed=on_candle_closed,
        on_tick=on_tick_received,
    )

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------
    _running = [True]

    def _shutdown(signum, frame):
        logger.info("Shutdown signal received.")
        _running[0] = False

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # ------------------------------------------------------------------
    # Main polling loop (MT5 doesn't push ticks — we poll)
    # ------------------------------------------------------------------
    logger.info("Starting live tick polling for %s…", symbol)
    logger.info("Press Ctrl+C to stop.")

    POLL_INTERVAL_MS = 500   # poll every 500ms

    while _running[0]:
        try:
            tick = market_data.get_latest_tick()
            candle_builder.process_tick(tick)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error processing tick: %s", exc)

        time.sleep(POLL_INTERVAL_MS / 1000.0)

    conn.disconnect()
    logger.info("VT Markets system shut down cleanly.")


if __name__ == "__main__":
    main()
