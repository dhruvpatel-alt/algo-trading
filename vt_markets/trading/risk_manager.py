"""
vt_markets/trading/risk_manager.py
=====================================
Risk management for VT Markets — computes SL/TP levels from signals.

Input:  Signal (direction, entry_price, stop_loss)
Output: List of (lot, rr, stop_loss, take_profit) tuples

This module does NOT place orders. It only computes levels.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from vt_markets import config
from vt_markets.models.signal import Signal

logger = logging.getLogger(__name__)


@dataclass
class OrderSpec:
    """Specification for a single order tier."""
    lot: float
    risk_reward: float
    stop_loss: float
    take_profit: float
    strategy: str
    magic: int
    direction: str
    entry_price: float


class RiskManager:
    """
    Computes the three order tiers for each signal.

    Tier definitions (from config):
        0.06 lots  →  1.0 R
        0.04 lots  →  2.0 R
        0.02 lots  →  2.5 R
    """

    TIERS = [
        (config.LOT_1, config.RR_1),
        (config.LOT_2, config.RR_2),
        (config.LOT_3, config.RR_3),
    ]

    def compute_order_specs(self, signal: Signal) -> list[OrderSpec]:
        """
        Build three OrderSpec objects from a Signal.

        BUY:  risk = entry - SL;  TP_n = entry + risk × R_n
        SELL: risk = SL - entry;  TP_n = entry - risk × R_n
        """
        entry = signal.entry_price
        sl = signal.stop_loss

        if signal.direction == "BUY":
            risk = entry - sl
            if risk <= 0:
                logger.warning(
                    "%s BUY signal has non-positive risk (entry=%.5f SL=%.5f) — skipped.",
                    signal.strategy, entry, sl,
                )
                return []
            specs = []
            for lot, rr in self.TIERS:
                tp = entry + risk * rr
                specs.append(OrderSpec(
                    lot=lot,
                    risk_reward=rr,
                    stop_loss=sl,
                    take_profit=tp,
                    strategy=signal.strategy,
                    magic=signal.magic,
                    direction="BUY",
                    entry_price=entry,
                ))

        else:  # SELL
            risk = sl - entry
            if risk <= 0:
                logger.warning(
                    "%s SELL signal has non-positive risk (entry=%.5f SL=%.5f) — skipped.",
                    signal.strategy, entry, sl,
                )
                return []
            specs = []
            for lot, rr in self.TIERS:
                tp = entry - risk * rr
                specs.append(OrderSpec(
                    lot=lot,
                    risk_reward=rr,
                    stop_loss=sl,
                    take_profit=tp,
                    strategy=signal.strategy,
                    magic=signal.magic,
                    direction="SELL",
                    entry_price=entry,
                ))

        logger.debug(
            "%s %s: risk=%.5f | tiers=%s",
            signal.strategy,
            signal.direction,
            risk,
            [(s.lot, s.risk_reward, round(s.take_profit, 5)) for s in specs],
        )
        return specs
