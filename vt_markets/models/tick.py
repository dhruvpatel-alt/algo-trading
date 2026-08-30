"""
vt_markets/models/tick.py
==========================
Live price tick model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Tick:
    """A single live price tick from MT5."""
    timestamp: datetime
    bid: float
    ask: float
    last: float = 0.0
    volume: float = 0.0

    @property
    def mid(self) -> float:
        """Mid-point between bid and ask."""
        return (self.bid + self.ask) / 2.0

    def __str__(self) -> str:
        return (
            f"Tick({self.timestamp.strftime('%H:%M:%S')} "
            f"bid={self.bid} ask={self.ask})"
        )
