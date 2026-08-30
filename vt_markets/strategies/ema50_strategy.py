"""
vt_markets/strategies/ema50_strategy.py
=========================================
EMA50 Strategy for VT Markets — uses ONLY EMA(50).

The other strategy (period=20) is never referenced here.
This strategy is completely independent of the period-20 strategy.
They do NOT share any state.
"""

from __future__ import annotations

from typing import Callable, TYPE_CHECKING

from vt_markets import config
from vt_markets.strategies.base_strategy import BaseVTStrategy

if TYPE_CHECKING:
    from vt_markets.models.signal import Signal
    from vt_markets.trading.daily_target import VTDailyTargetGuard


class EMA50Strategy(BaseVTStrategy):
    """
    EMA50 touch-and-breakout strategy for VT Markets.

    BUY:  close > EMA50  AND  low <= EMA50 <= high
          → wait ONLY for next candle
          → if price > setup_high → BUY

    SELL: close < EMA50  AND  low <= EMA50 <= high
          → wait ONLY for next candle
          → if price < setup_low → SELL

    EMA20 is NOT used. No crossover filter.
    """

    ema_period: int = config.EMA50_PERIOD   # 50
    strategy_name: str = "EMA50"
    magic: int = config.EMA50_MAGIC         # 50050

    def __init__(
        self,
        on_signal: Callable[["Signal"], None],
        daily_guard: "VTDailyTargetGuard",
    ) -> None:
        super().__init__(on_signal=on_signal, daily_guard=daily_guard)
