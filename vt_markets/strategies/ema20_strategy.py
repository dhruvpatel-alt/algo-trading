"""
vt_markets/strategies/ema20_strategy.py
=========================================
EMA20 Strategy for VT Markets — uses ONLY EMA(20).

EMA50 is NEVER referenced here.
This strategy is completely independent of EMA50Strategy.
"""

from __future__ import annotations

from typing import Callable, TYPE_CHECKING

from vt_markets import config
from vt_markets.strategies.base_strategy import BaseVTStrategy

if TYPE_CHECKING:
    from vt_markets.models.signal import Signal
    from vt_markets.trading.daily_target import VTDailyTargetGuard


class EMA20Strategy(BaseVTStrategy):
    """
    EMA20 touch-and-breakout strategy for VT Markets.

    BUY:  close > EMA20  AND  low <= EMA20 <= high
          → wait ONLY for next candle
          → if price > setup_high → BUY (3 positions via order_manager)

    SELL: close < EMA20  AND  low <= EMA20 <= high
          → wait ONLY for next candle
          → if price < setup_low → SELL (3 positions)

    EMA50 is NOT used. No crossover filter.
    """

    ema_period: int = config.EMA20_PERIOD   # 20
    strategy_name: str = "EMA20"
    magic: int = config.EMA20_MAGIC         # 20020

    def __init__(
        self,
        on_signal: Callable[["Signal"], None],
        daily_guard: "VTDailyTargetGuard",
    ) -> None:
        super().__init__(on_signal=on_signal, daily_guard=daily_guard)
