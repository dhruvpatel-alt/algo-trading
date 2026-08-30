"""
vt_markets/mt5/execution.py
============================
MT5 order execution abstraction for VT Markets.

Strategies MUST use this class instead of calling mt5.order_send() directly.

Responsibilities
----------------
- Validate lot sizes against broker constraints
- Build MT5 order request dicts
- Send orders via mt5.order_send()
- Verify every result — never assume success
- Log order results including rejection reasons
- Never accept orders in LIVE mode unless explicitly allowed

All amounts use BID for SELL orders and ASK for BUY orders.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from vt_markets.models.position import VTPosition
from vt_markets.models.signal import Signal

logger = logging.getLogger(__name__)


class VTMarketsExecutor:
    """
    Clean abstraction over mt5.order_send().

    Parameters
    ----------
    symbol : str
        Confirmed broker symbol (e.g., "XAUUSD").
    allow_live : bool
        Must be True to place real orders. Defaults to False (demo/paper only).
    deviation : int
        Max allowed price deviation in points.
    """

    def __init__(
        self,
        symbol: str,
        allow_live: bool = False,
        deviation: int = 20,
    ) -> None:
        self._symbol = symbol
        self._allow_live = allow_live
        self._deviation = deviation
        self._symbol_info: dict | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_symbol_constraints(self) -> dict:
        """
        Fetch and cache broker constraints for the symbol.
        Must be called before any orders are placed.
        """
        import MetaTrader5 as mt5
        info = mt5.symbol_info(self._symbol)
        if info is None:
            raise RuntimeError(f"Cannot load symbol info for '{self._symbol}'")

        self._symbol_info = {
            "digits": info.digits,
            "point": info.point,
            "volume_min": info.volume_min,
            "volume_max": info.volume_max,
            "volume_step": info.volume_step,
            "trade_tick_size": info.trade_tick_size,
            "trade_tick_value": info.trade_tick_value,
            "trade_contract_size": info.trade_contract_size,
        }
        logger.info(
            "Symbol constraints loaded: %s | vol_min=%.2f vol_max=%.2f "
            "vol_step=%.2f tick_size=%.5f contract_size=%.2f",
            self._symbol,
            self._symbol_info["volume_min"],
            self._symbol_info["volume_max"],
            self._symbol_info["volume_step"],
            self._symbol_info["trade_tick_size"],
            self._symbol_info["trade_contract_size"],
        )
        return self._symbol_info

    def validate_lots(self, lots: list[float]) -> None:
        """
        Verify that each lot size is valid for this broker.
        Raises ValueError with an exact description if any lot is invalid.
        """
        if self._symbol_info is None:
            raise RuntimeError("Call load_symbol_constraints() first.")

        vol_min = self._symbol_info["volume_min"]
        vol_max = self._symbol_info["volume_max"]
        vol_step = self._symbol_info["volume_step"]

        errors = []
        for lot in lots:
            if lot < vol_min:
                errors.append(
                    f"Lot {lot} is below broker minimum {vol_min}"
                )
            elif lot > vol_max:
                errors.append(
                    f"Lot {lot} exceeds broker maximum {vol_max}"
                )
            else:
                # Check it's a valid step.
                # Use a round-trip: quantize to nearest step and compare.
                # This avoids floating point remainder issues (e.g. 0.06 % 0.01).
                steps_from_min = round((lot - vol_min) / vol_step)
                closest = round(vol_min + steps_from_min * vol_step, 10)
                if abs(lot - closest) > 1e-8:
                    errors.append(
                        f"Lot {lot} is not a valid step "
                        f"(min={vol_min}, step={vol_step})"
                    )

        if errors:
            raise ValueError(
                f"Invalid lot sizes for {self._symbol}:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )

        logger.debug("All lots validated: %s", lots)

    def buy(
        self,
        lot: float,
        stop_loss: float,
        take_profit: float,
        magic: int,
        comment: str = "",
        setup_time: datetime | None = None,
    ) -> VTPosition:
        """
        Place a BUY market order at current ASK price.

        Returns
        -------
        VTPosition
            Position with status OPEN (success) or FAILED (rejected).
        """
        import MetaTrader5 as mt5
        tick = mt5.symbol_info_tick(self._symbol)
        if tick is None:
            raise RuntimeError(f"Cannot get tick for '{self._symbol}'")

        price = tick.ask   # BUY at ASK
        entry_time = datetime.fromtimestamp(tick.time, tz=timezone.utc)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,   # type: ignore[attr-defined]
            "symbol": self._symbol,
            "volume": lot,
            "type": mt5.ORDER_TYPE_BUY,         # type: ignore[attr-defined]
            "price": price,
            "sl": round(stop_loss, self._symbol_info["digits"] if self._symbol_info else 5),
            "tp": round(take_profit, self._symbol_info["digits"] if self._symbol_info else 5),
            "deviation": self._deviation,
            "magic": magic,
            "comment": comment[:31],            # MT5 comment max 31 chars
            "type_time": mt5.ORDER_TIME_GTC,    # type: ignore[attr-defined]
            "type_filling": mt5.ORDER_FILLING_IOC,  # type: ignore[attr-defined]
        }

        return self._send_order(
            request=request,
            side="BUY",
            lot=lot,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            magic=magic,
            setup_time=setup_time,
            entry_time=entry_time,
        )

    def sell(
        self,
        lot: float,
        stop_loss: float,
        take_profit: float,
        magic: int,
        comment: str = "",
        setup_time: datetime | None = None,
    ) -> VTPosition:
        """
        Place a SELL market order at current BID price.
        """
        import MetaTrader5 as mt5
        tick = mt5.symbol_info_tick(self._symbol)
        if tick is None:
            raise RuntimeError(f"Cannot get tick for '{self._symbol}'")

        price = tick.bid   # SELL at BID
        entry_time = datetime.fromtimestamp(tick.time, tz=timezone.utc)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,   # type: ignore[attr-defined]
            "symbol": self._symbol,
            "volume": lot,
            "type": mt5.ORDER_TYPE_SELL,        # type: ignore[attr-defined]
            "price": price,
            "sl": round(stop_loss, self._symbol_info["digits"] if self._symbol_info else 5),
            "tp": round(take_profit, self._symbol_info["digits"] if self._symbol_info else 5),
            "deviation": self._deviation,
            "magic": magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,    # type: ignore[attr-defined]
            "type_filling": mt5.ORDER_FILLING_IOC,  # type: ignore[attr-defined]
        }

        return self._send_order(
            request=request,
            side="SELL",
            lot=lot,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            magic=magic,
            setup_time=setup_time,
            entry_time=entry_time,
        )

    def get_open_positions(self, magic: int | None = None) -> list[dict]:
        """
        Retrieve all currently open positions from MT5.

        Parameters
        ----------
        magic : int | None
            Filter by magic number. If None, returns all positions for the symbol.
        """
        import MetaTrader5 as mt5
        positions = mt5.positions_get(symbol=self._symbol)
        if positions is None:
            return []

        result = []
        for p in positions:
            if magic is not None and p.magic != magic:
                continue
            result.append({
                "ticket": p.ticket,
                "symbol": p.symbol,
                "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",  # type: ignore[attr-defined]
                "volume": p.volume,
                "price_open": p.price_open,
                "sl": p.sl,
                "tp": p.tp,
                "magic": p.magic,
                "comment": p.comment,
                "profit": p.profit,
                "time": datetime.fromtimestamp(p.time, tz=timezone.utc),
            })
        return result

    def close_position(self, ticket: int) -> bool:
        """
        Close a position by ticket number at current market price.
        Returns True if successful.
        """
        import MetaTrader5 as mt5
        position = mt5.positions_get(ticket=ticket)
        if not position:
            logger.warning("Position %d not found — may already be closed.", ticket)
            return False

        pos = position[0]
        tick = mt5.symbol_info_tick(self._symbol)
        if tick is None:
            logger.error("Cannot get tick to close position %d.", ticket)
            return False

        # Closing a BUY = SELL at BID; closing a SELL = BUY at ASK
        if pos.type == mt5.ORDER_TYPE_BUY:   # type: ignore[attr-defined]
            order_type = mt5.ORDER_TYPE_SELL  # type: ignore[attr-defined]
            price = tick.bid
        else:
            order_type = mt5.ORDER_TYPE_BUY   # type: ignore[attr-defined]
            price = tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,   # type: ignore[attr-defined]
            "symbol": self._symbol,
            "volume": pos.volume,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": self._deviation,
            "magic": pos.magic,
            "comment": "close",
            "type_time": mt5.ORDER_TIME_GTC,   # type: ignore[attr-defined]
            "type_filling": mt5.ORDER_FILLING_IOC,  # type: ignore[attr-defined]
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:  # type: ignore[attr-defined]
            logger.error(
                "Failed to close position %d: retcode=%s comment='%s'",
                ticket,
                result.retcode if result else "None",
                result.comment if result else "",
            )
            return False

        logger.info("Position %d closed at %.5f.", ticket, price)
        return True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _send_order(
        self,
        request: dict,
        side: str,
        lot: float,
        price: float,
        stop_loss: float,
        take_profit: float,
        magic: int,
        setup_time: datetime | None,
        entry_time: datetime,
    ) -> VTPosition:
        """Send the order and build a VTPosition from the result."""
        import MetaTrader5 as mt5

        self._safety_check()

        logger.info(
            "Sending %s order: symbol=%s lot=%.2f price=%.5f SL=%.5f TP=%.5f magic=%d",
            side,
            self._symbol,
            lot,
            price,
            stop_loss,
            take_profit,
            magic,
        )

        result = mt5.order_send(request)

        pos = VTPosition(
            strategy="",       # filled by caller
            magic=magic,
            side=side,
            lot=lot,
            entry_price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            setup_time=setup_time,
            entry_time=entry_time,
        )

        if result is None:
            logger.error("order_send() returned None for %s order.", side)
            pos.status = "FAILED"
            pos.mt5_comment = "order_send returned None"
            return pos

        # Verify result
        if result.retcode == mt5.TRADE_RETCODE_DONE:   # type: ignore[attr-defined]
            pos.ticket = result.order
            pos.order = result.order
            pos.deal = result.deal
            pos.entry_price = result.price
            pos.status = "OPEN"
            pos.mt5_comment = result.comment

            logger.info(
                "%s CONFIRMED: ticket=%d deal=%d volume=%.2f price=%.5f comment='%s'",
                side,
                result.order,
                result.deal,
                result.volume,
                result.price,
                result.comment,
            )
        else:
            pos.status = "FAILED"
            pos.mt5_comment = f"retcode={result.retcode} comment='{result.comment}'"
            logger.error(
                "%s ORDER REJECTED: retcode=%d comment='%s'",
                side,
                result.retcode,
                result.comment,
            )

        return pos

    def _safety_check(self) -> None:
        """
        Verify that live trading is explicitly allowed before sending orders.
        Raises RuntimeError if not.
        """
        import MetaTrader5 as mt5
        if self._allow_live:
            return   # explicitly allowed

        # In demo mode, orders still go to the broker's demo server — no safety issue.
        # But if the account is LIVE and allow_live=False, abort.
        info = mt5.account_info()
        if info is None:
            raise RuntimeError("Cannot verify account mode before order.")

        is_live = info.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL  # type: ignore[attr-defined]
        if is_live:
            raise RuntimeError(
                "SAFETY BLOCK: LIVE account detected but ALLOW_LIVE_TRADING=false. "
                "Set TRADING_MODE=LIVE and ALLOW_LIVE_TRADING=true to allow real orders."
            )
