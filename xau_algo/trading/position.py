"""
xau_algo/trading/position.py
============================
Dataclass representing a single open or closed trading position.

Every signal creates exactly THREE Position objects (one per lot/RR tier).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Position:
    """A single trading position (one lot tier)."""

    # Unique identifier
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Which strategy generated this trade
    strategy: str = ""          # "EMA20" | "EMA50"

    # Direction
    side: str = ""              # "BUY" | "SELL"

    # Sizing
    lot: float = 0.0

    # Prices
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0

    # Risk:Reward tier (1.0, 2.0, or 2.5)
    risk_reward: float = 0.0

    # Timestamps
    setup_time: datetime | None = None
    entry_time: datetime | None = None
    exit_time: datetime | None = None

    # Exit
    exit_price: float | None = None

    # Status
    # OPEN → active position
    # CLOSED_TP → take-profit hit
    # CLOSED_SL → stop-loss hit
    status: str = "OPEN"

    # Realised PnL (populated on close)
    pnl: float = 0.0

    def to_dict(self) -> dict:
        """Serialise to a plain dictionary (for CSV/JSONL storage)."""
        return {
            "id": self.id,
            "strategy": self.strategy,
            "side": self.side,
            "lot": self.lot,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk_reward": self.risk_reward,
            "setup_time": self.setup_time.isoformat() if self.setup_time else "",
            "entry_time": self.entry_time.isoformat() if self.entry_time else "",
            "exit_time": self.exit_time.isoformat() if self.exit_time else "",
            "exit_price": self.exit_price if self.exit_price is not None else "",
            "status": self.status,
            "pnl": round(self.pnl, 4),
        }
