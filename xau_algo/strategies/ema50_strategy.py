"""
xau_algo/strategies/ema50_strategy.py
=======================================
EMA50 Strategy — uses ONLY EMA(50).

EMA20 is never referenced or used here.
This strategy is completely independent of EMA20Strategy.
They do NOT share any state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from xau_algo import config
from xau_algo.strategies.base_strategy import BaseEMAStrategy
from xau_algo.trading.paper_broker import PaperBroker
from xau_algo.trading.daily_target import DailyTargetGuard

if TYPE_CHECKING:
    from xau_algo.storage.supabase_repository import SupabaseRepository


class EMA50Strategy(BaseEMAStrategy):
    """
    EMA50 touch-and-breakout strategy.

    BUY setup:
        close > EMA50  AND  low <= EMA50 <= high
        -> wait for ONLY the immediately next candle
        -> if price > setup_high -> BUY (3 positions)

    SELL setup:
        close < EMA50  AND  low <= EMA50 <= high
        -> wait for ONLY the immediately next candle
        -> if price < setup_low -> SELL (3 positions)

    EMA20 is NOT used anywhere in this class.
    """

    ema_period: int = config.EMA50_PERIOD     # 50 by default
    strategy_name: str = "EMA50"

    def __init__(
        self,
        broker: PaperBroker,
        daily_guard: DailyTargetGuard,
        supabase_repo: "SupabaseRepository | None" = None,
    ) -> None:
        super().__init__(broker=broker, daily_guard=daily_guard, supabase_repo=supabase_repo)
