"""
vt_markets/trading/position_manager.py
========================================
Position manager — tracks all open positions and handles restart recovery.

Responsibilities
----------------
1. Maintain the in-memory list of open VTPositions.
2. On startup: recover existing MT5 positions using magic numbers.
3. Prevent duplicate positions after restart.
4. Update daily P&L when positions close.
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timezone

from vt_markets import config
from vt_markets.models.position import VTPosition
from vt_markets.trading.daily_target import VTDailyTargetGuard

logger = logging.getLogger(__name__)

_KNOWN_MAGICS = {config.EMA20_MAGIC, config.EMA50_MAGIC}


class PositionManager:
    """
    Tracks and persists all open/closed positions.

    Parameters
    ----------
    daily_guard : VTDailyTargetGuard
        Used to record P&L when positions close.
    executor : VTMarketsExecutor | None
        If provided, used during startup recovery to read MT5 positions.
    """

    def __init__(
        self,
        daily_guard: VTDailyTargetGuard,
        executor=None,
    ) -> None:
        self._daily_guard = daily_guard
        self._executor = executor
        self._open: dict[int, VTPosition] = {}   # ticket → position
        self._closed: list[VTPosition] = []

        os.makedirs(config.LOG_DIR, exist_ok=True)
        self._csv_path = config.TRADES_CSV
        self._ensure_csv()

    # ------------------------------------------------------------------
    # Startup recovery
    # ------------------------------------------------------------------

    def recover_from_mt5(self) -> int:
        """
        Read currently open MT5 positions and rebuild internal state.
        Returns the count of recovered positions.

        This prevents opening duplicate positions after a restart.
        """
        if self._executor is None:
            logger.warning("No executor — cannot recover MT5 positions.")
            return 0

        recovered = 0
        for magic in _KNOWN_MAGICS:
            positions = self._executor.get_open_positions(magic=magic)
            for p in positions:
                ticket = p["ticket"]
                if ticket in self._open:
                    continue  # already tracked
                strategy = "EMA20" if magic == config.EMA20_MAGIC else "EMA50"
                vt_pos = VTPosition(
                    ticket=ticket,
                    strategy=strategy,
                    magic=magic,
                    side=p["type"],
                    lot=p["volume"],
                    entry_price=p["price_open"],
                    stop_loss=p["sl"],
                    take_profit=p["tp"],
                    entry_time=p["time"],
                    status="OPEN",
                    mt5_comment=p["comment"],
                )
                self._open[ticket] = vt_pos
                recovered += 1
                logger.info(
                    "Recovered position: %s ticket=%d %s %.2f lot @ %.5f",
                    strategy, ticket, p["type"], p["volume"], p["price_open"],
                )

        logger.info("Position recovery complete. Recovered %d positions.", recovered)
        return recovered

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_opened(self, position: VTPosition) -> None:
        """Register a newly opened position."""
        if position.ticket is not None:
            self._open[position.ticket] = position

    def register_closed(self, ticket: int, exit_price: float, reason: str) -> None:
        """Mark a position as closed and record P&L."""
        pos = self._open.pop(ticket, None)
        if pos is None:
            logger.warning("Ticket %d not in open positions — already closed?", ticket)
            return

        pos.exit_time = datetime.now(tz=timezone.utc)
        pos.exit_price = exit_price
        pos.close_reason = reason
        pos.status = "CLOSED"

        # Compute P&L (approximate — actual P&L comes from MT5)
        self._closed.append(pos)
        self._save_trade(pos)
        logger.info(
            "Position closed: ticket=%d %s %s @ %.5f reason=%s",
            ticket, pos.strategy, pos.side, exit_price, reason,
        )

    @property
    def open_tickets(self) -> list[int]:
        return list(self._open.keys())

    @property
    def open_count(self) -> int:
        return len(self._open)

    @property
    def closed_count(self) -> int:
        return len(self._closed)

    def sync_with_mt5(self) -> None:
        """
        Compare internal open positions with MT5.
        Positions that disappeared from MT5 (SL/TP hit) are marked closed.
        """
        if self._executor is None:
            return

        mt5_tickets: set[int] = set()
        for magic in _KNOWN_MAGICS:
            for p in self._executor.get_open_positions(magic=magic):
                mt5_tickets.add(p["ticket"])

        # Positions in our internal state but NOT in MT5 → they were closed
        disappeared = [t for t in self._open if t not in mt5_tickets]
        for ticket in disappeared:
            pos = self._open[ticket]
            logger.info(
                "Ticket %d (%s %s) no longer in MT5 — marking CLOSED.",
                ticket, pos.strategy, pos.side,
            )
            self.register_closed(ticket, exit_price=0.0, reason="MT5_CLOSED")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_csv(self) -> None:
        if not os.path.exists(self._csv_path):
            with open(self._csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(VTPosition().to_dict().keys()))
                writer.writeheader()

    def _save_trade(self, pos: VTPosition) -> None:
        try:
            with open(self._csv_path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(pos.to_dict().keys()))
                writer.writerow(pos.to_dict())
        except OSError as exc:
            logger.error("Failed to save trade: %s", exc)
