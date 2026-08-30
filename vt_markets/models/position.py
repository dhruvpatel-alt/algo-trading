"""
vt_markets/models/position.py
==============================
Tracked position model for the VT Markets implementation.

Each MT5 ticket corresponds to one Position object.
Three positions are created per signal (one per lot/RR tier).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class VTPosition:
    """
    Tracks a single MT5 position/order.

    status values:
        PENDING   — order sent, awaiting MT5 confirmation
        OPEN      — confirmed open in MT5
        CLOSED    — closed by SL, TP, or manual action
        FAILED    — order was rejected
    """
    # Internal tracking
    local_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # MT5 identifiers (populated after order_send succeeds)
    ticket: int | None = None       # MT5 position ticket
    order: int | None = None        # MT5 order ticket
    deal: int | None = None         # MT5 deal ticket

    # Strategy
    strategy: str = ""              # "EMA20" | "EMA50"
    magic: int = 0

    # Trade parameters
    side: str = ""                  # "BUY" | "SELL"
    lot: float = 0.0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    risk_reward: float = 0.0

    # Timestamps
    setup_time: datetime | None = None
    entry_time: datetime | None = None
    exit_time: datetime | None = None

    # Exit
    exit_price: float | None = None
    close_reason: str = ""          # "SL" | "TP" | "MANUAL"

    # Status
    status: str = "PENDING"         # "PENDING" | "OPEN" | "CLOSED" | "FAILED"

    # MT5 comment (populated from order result)
    mt5_comment: str = ""

    def to_dict(self) -> dict:
        return {
            "local_id": self.local_id,
            "ticket": self.ticket,
            "order": self.order,
            "deal": self.deal,
            "strategy": self.strategy,
            "magic": self.magic,
            "side": self.side,
            "lot": self.lot,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk_reward": self.risk_reward,
            "setup_time": self.setup_time.isoformat() if self.setup_time else "",
            "entry_time": self.entry_time.isoformat() if self.entry_time else "",
            "exit_time": self.exit_time.isoformat() if self.exit_time else "",
            "exit_price": self.exit_price,
            "close_reason": self.close_reason,
            "status": self.status,
            "mt5_comment": self.mt5_comment,
        }
