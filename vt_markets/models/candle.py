"""
vt_markets/models/candle.py
============================
1-minute OHLC candle model for the VT Markets implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Candle:
    """A completed 1-minute OHLC candle."""
    timestamp: datetime   # start of the candle minute, timezone-aware
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __str__(self) -> str:
        return (
            f"Candle({self.timestamp.strftime('%Y-%m-%d %H:%M')} "
            f"O={self.open} H={self.high} L={self.low} C={self.close})"
        )
