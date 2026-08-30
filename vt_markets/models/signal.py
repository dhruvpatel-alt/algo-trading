"""
vt_markets/models/signal.py
============================
Trade signal model emitted by strategies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Signal:
    """
    A trade signal produced when a breakout is confirmed.

    Attributes
    ----------
    strategy : str
        "EMA20" or "EMA50"
    direction : str
        "BUY" or "SELL"
    entry_price : float
        Breakout level (setup_high for BUY, setup_low for SELL).
    stop_loss : float
        setup_low for BUY, setup_high for SELL.
    setup_high : float
        High of the setup candle.
    setup_low : float
        Low of the setup candle.
    setup_time : datetime
        Timestamp of the setup candle.
    signal_time : datetime
        Timestamp when the breakout was detected.
    magic : int
        MT5 magic number identifying the strategy.
    """
    strategy: str
    direction: str
    entry_price: float
    stop_loss: float
    setup_high: float
    setup_low: float
    setup_time: datetime
    signal_time: datetime
    magic: int

    def __str__(self) -> str:
        return (
            f"Signal({self.strategy} {self.direction} "
            f"entry={self.entry_price:.5f} SL={self.stop_loss:.5f} "
            f"@ {self.signal_time.strftime('%H:%M:%S')})"
        )
