"""
vt_markets/mt5/symbols.py
==========================
XAU/USD symbol discovery and validation for VT Markets.

VT Markets (and other brokers) may use non-standard symbol names for gold:
    XAUUSD, XAUUSD.a, XAUUSDm, GOLD, GOLDs, etc.

This module:
1. Checks the configured symbol.
2. If not found, searches for XAU/USD-related symbols.
3. Reports all candidates — does NOT silently select an ambiguous one.
4. Returns the confirmed symbol name.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# Keywords used to identify gold/XAU/USD symbols in a broker's symbol list
_GOLD_KEYWORDS = ["xau", "gold", "xauusd"]


class SymbolValidator:
    """
    Discovers and validates the XAU/USD symbol from the connected MT5 broker.

    Parameters
    ----------
    preferred_symbol : str
        The symbol name from config (e.g., "XAUUSD").
    """

    def __init__(self, preferred_symbol: str) -> None:
        self._preferred = preferred_symbol
        self._confirmed_symbol: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self) -> str:
        """
        Validate the preferred symbol.
        Returns the confirmed symbol name.
        Raises ValueError if not found or ambiguous.
        """
        import MetaTrader5 as mt5

        # Step 1: Check the configured symbol directly
        info = mt5.symbol_info(self._preferred)
        if info is not None:
            self._confirmed_symbol = self._preferred
            self._enable_symbol(self._preferred)
            logger.info(
                "Symbol confirmed: %s | Digits=%d | Point=%.5f | "
                "TickSize=%.5f | TickValue=%.5f | ContractSize=%.2f",
                info.name,
                info.digits,
                info.point,
                info.trade_tick_size,
                info.trade_tick_value,
                info.trade_contract_size,
            )
            return self._confirmed_symbol

        # Step 2: Search all broker symbols for gold-related names
        logger.warning(
            "Symbol '%s' not found. Searching broker symbols for XAU/USD equivalents…",
            self._preferred,
        )
        candidates = self._find_gold_candidates()

        if not candidates:
            raise ValueError(
                f"Symbol '{self._preferred}' not found and no XAU/USD-related "
                f"symbols were discovered on this broker. "
                f"Check MT5_SYMBOL in your .env file."
            )

        if len(candidates) == 1:
            chosen = candidates[0]
            logger.info(
                "Single XAU/USD candidate found: '%s'. Using it automatically.",
                chosen,
            )
            self._confirmed_symbol = chosen
            self._enable_symbol(chosen)
            return self._confirmed_symbol

        # Step 3: Multiple candidates — report them and let user decide
        names = ", ".join(candidates)
        raise ValueError(
            f"Symbol '{self._preferred}' not found. "
            f"Multiple XAU/USD candidates exist: [{names}]. "
            f"Set MT5_SYMBOL to one of these in vt_markets/.env and restart."
        )

    @property
    def symbol(self) -> str:
        if self._confirmed_symbol is None:
            raise RuntimeError("call validate() first.")
        return self._confirmed_symbol

    def get_symbol_info(self) -> dict:
        """Return broker constraints for the confirmed symbol."""
        import MetaTrader5 as mt5
        sym = self.symbol
        info = mt5.symbol_info(sym)
        if info is None:
            raise RuntimeError(f"Cannot retrieve info for symbol '{sym}'")
        return {
            "name": info.name,
            "digits": info.digits,
            "point": info.point,
            "volume_min": info.volume_min,
            "volume_max": info.volume_max,
            "volume_step": info.volume_step,
            "trade_tick_size": info.trade_tick_size,
            "trade_tick_value": info.trade_tick_value,
            "trade_contract_size": info.trade_contract_size,
            "spread": info.spread,
            "trade_mode": info.trade_mode,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _find_gold_candidates() -> list[str]:
        """Return broker symbols that contain XAU or gold-related keywords."""
        import MetaTrader5 as mt5
        all_symbols = mt5.symbols_get()
        if all_symbols is None:
            return []
        candidates = []
        for s in all_symbols:
            name_lower = s.name.lower()
            if any(kw in name_lower for kw in _GOLD_KEYWORDS):
                candidates.append(s.name)
        return candidates

    @staticmethod
    def _enable_symbol(symbol: str) -> None:
        """Ensure the symbol is visible / enabled in Market Watch."""
        import MetaTrader5 as mt5
        info = mt5.symbol_info(symbol)
        if info is not None and not info.visible:
            if not mt5.symbol_select(symbol, True):
                logger.warning("Could not enable symbol '%s' in Market Watch.", symbol)
