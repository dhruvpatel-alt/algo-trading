"""
xau_algo/trading/paper_broker.py
=================================
Paper (simulated) broker.

Responsibilities
----------------
- Maintain account state: balance, equity, open/closed positions, P&L
- Open positions via open_position()
- Check SL/TP on every price tick via on_price()
- Close positions and compute P&L via _close_position()

PnL Formula (XAU/USD spot gold)
--------------------------------
  contract_size = 100  (oz per standard lot)
  BUY  PnL = (exit_price - entry_price) * lot * contract_size
  SELL PnL = (entry_price - exit_price) * lot * contract_size

The broker does NOT enforce the daily target.
That is handled by DailyTargetGuard (daily_target.py).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from xau_algo import config
from xau_algo.trading.position import Position

logger = logging.getLogger(__name__)


class PaperBroker:
    """Simulated broker for paper trading."""

    def __init__(
        self,
        initial_capital: float | None = None,
        contract_size: float | None = None,
        on_trade_closed: Callable[[Position], None] | None = None,
    ) -> None:
        """
        Parameters
        ----------
        initial_capital : float
            Starting balance. Defaults to config.INITIAL_CAPITAL.
        contract_size : float
            Oz per standard lot. Defaults to config.CONTRACT_SIZE.
        on_trade_closed : callable, optional
            Callback invoked every time a position is closed.
            Receives the closed Position object.
        """
        self._contract_size = contract_size or config.CONTRACT_SIZE
        self._on_trade_closed = on_trade_closed

        self.balance: float = initial_capital or config.INITIAL_CAPITAL
        self.open_positions: list[Position] = []
        self.closed_positions: list[Position] = []

        # Daily P&L resets each session
        self.daily_pnl: float = 0.0
        self.realized_pnl: float = 0.0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def unrealized_pnl(self) -> float:
        """Sum of unrealised P&L across all open positions (requires last price)."""
        # This is updated via mark_to_market() for reporting; not tracked here.
        return 0.0

    @property
    def equity(self) -> float:
        """Balance + unrealised P&L (approximate — based on last known prices)."""
        return self.balance + self._cached_unrealized

    def mark_to_market(self, current_price: float) -> None:
        """Update unrealised P&L for display purposes (not used in SL/TP logic)."""
        total = 0.0
        for pos in self.open_positions:
            if pos.side == "BUY":
                total += (current_price - pos.entry_price) * pos.lot * self._contract_size
            else:
                total += (pos.entry_price - current_price) * pos.lot * self._contract_size
        self._cached_unrealized = total

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def open_position(self, position: Position) -> None:
        """
        Register a new open position.

        The caller (strategy) is responsible for computing entry, SL, TP.
        """
        self.open_positions.append(position)
        logger.info(
            "%s | %s %s | lot=%.2f | entry=%.5f | SL=%.5f | TP=%.5f | RR=%.1f",
            position.entry_time,
            position.strategy,
            position.side,
            position.lot,
            position.entry_price,
            position.stop_loss,
            position.take_profit,
            position.risk_reward,
        )

    def on_price(self, price: float, timestamp: datetime) -> None:
        """
        Feed a live price tick to the broker.
        Checks all open positions for SL or TP hits.
        """
        self._cached_unrealized = 0.0  # reset; recalculated below
        still_open: list[Position] = []

        for pos in self.open_positions:
            closed = False

            if pos.side == "BUY":
                if price <= pos.stop_loss:
                    self._close_position(pos, pos.stop_loss, "CLOSED_SL", timestamp)
                    closed = True
                elif price >= pos.take_profit:
                    self._close_position(pos, pos.take_profit, "CLOSED_TP", timestamp)
                    closed = True
            else:  # SELL
                if price >= pos.stop_loss:
                    self._close_position(pos, pos.stop_loss, "CLOSED_SL", timestamp)
                    closed = True
                elif price <= pos.take_profit:
                    self._close_position(pos, pos.take_profit, "CLOSED_TP", timestamp)
                    closed = True

            if not closed:
                # Accumulate unrealised
                if pos.side == "BUY":
                    self._cached_unrealized += (price - pos.entry_price) * pos.lot * self._contract_size
                else:
                    self._cached_unrealized += (pos.entry_price - price) * pos.lot * self._contract_size
                still_open.append(pos)

        self.open_positions = still_open

    def check_ohlc(
        self,
        candle_open: float,
        candle_high: float,
        candle_low: float,
        candle_close: float,
        timestamp: datetime,
        assume_sl_first: bool | None = None,
    ) -> None:
        """
        Check SL/TP for historical OHLC candles (backtest mode).

        When both SL and TP fall within the candle's range, the
        `assume_sl_first` flag (defaults to config.ASSUME_SL_FIRST_ON_CONFLICT)
        determines which is applied.
        """
        if assume_sl_first is None:
            assume_sl_first = config.ASSUME_SL_FIRST_ON_CONFLICT

        still_open: list[Position] = []

        for pos in self.open_positions:
            closed = False

            if pos.side == "BUY":
                sl_hit = candle_low <= pos.stop_loss
                tp_hit = candle_high >= pos.take_profit

                if sl_hit and tp_hit:
                    # Conflict — apply configured assumption
                    if assume_sl_first:
                        self._close_position(pos, pos.stop_loss, "CLOSED_SL", timestamp)
                    else:
                        self._close_position(pos, pos.take_profit, "CLOSED_TP", timestamp)
                    closed = True
                elif sl_hit:
                    self._close_position(pos, pos.stop_loss, "CLOSED_SL", timestamp)
                    closed = True
                elif tp_hit:
                    self._close_position(pos, pos.take_profit, "CLOSED_TP", timestamp)
                    closed = True

            else:  # SELL
                sl_hit = candle_high >= pos.stop_loss
                tp_hit = candle_low <= pos.take_profit

                if sl_hit and tp_hit:
                    if assume_sl_first:
                        self._close_position(pos, pos.stop_loss, "CLOSED_SL", timestamp)
                    else:
                        self._close_position(pos, pos.take_profit, "CLOSED_TP", timestamp)
                    closed = True
                elif sl_hit:
                    self._close_position(pos, pos.stop_loss, "CLOSED_SL", timestamp)
                    closed = True
                elif tp_hit:
                    self._close_position(pos, pos.take_profit, "CLOSED_TP", timestamp)
                    closed = True

            if not closed:
                still_open.append(pos)

        self.open_positions = still_open

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _close_position(
        self,
        pos: Position,
        exit_price: float,
        status: str,
        timestamp: datetime,
    ) -> None:
        """Compute PnL, update balance, move position to closed list."""
        if pos.side == "BUY":
            pnl = (exit_price - pos.entry_price) * pos.lot * self._contract_size
        else:
            pnl = (pos.entry_price - exit_price) * pos.lot * self._contract_size

        pos.exit_price = exit_price
        pos.exit_time = timestamp
        pos.status = status
        pos.pnl = round(pnl, 4)

        self.balance += pnl
        self.realized_pnl += pnl
        self.daily_pnl += pnl

        self.closed_positions.append(pos)

        logger.info(
            "%s | CLOSED %s %s | lot=%.2f | exit=%.5f | status=%s | pnl=%.4f | daily_pnl=%.4f",
            timestamp,
            pos.strategy,
            pos.side,
            pos.lot,
            exit_price,
            status,
            pnl,
            self.daily_pnl,
        )

        if self._on_trade_closed:
            self._on_trade_closed(pos)

    def reset_daily(self) -> None:
        """Reset daily P&L counter (called at session start each day)."""
        self.daily_pnl = 0.0
        logger.info("Daily P&L reset. Balance=%.2f", self.balance)

    # ------------------------------------------------------------------
    # Reporting helpers
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """Return a snapshot of the current account state."""
        return {
            "balance": round(self.balance, 4),
            "equity": round(self.equity, 4),
            "unrealized_pnl": round(self._cached_unrealized, 4),
            "realized_pnl": round(self.realized_pnl, 4),
            "daily_pnl": round(self.daily_pnl, 4),
            "open_positions": len(self.open_positions),
            "closed_positions": len(self.closed_positions),
        }

    # Private cache for unrealised P&L
    _cached_unrealized: float = 0.0
