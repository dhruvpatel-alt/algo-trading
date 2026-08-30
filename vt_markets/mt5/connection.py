"""
vt_markets/mt5/connection.py
==============================
MetaTrader 5 connection manager for VT Markets.

Responsibilities
----------------
1. Initialize MT5 terminal.
2. Login with configured credentials.
3. Verify account mode (DEMO / LIVE).
4. Verify trading permissions.
5. Log account info WITHOUT printing credentials.
6. Provide clean shutdown.

Credentials are NEVER logged.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


class MT5Connection:
    """
    Manages the lifecycle of the MT5 terminal connection.

    Parameters
    ----------
    login : int
        MT5 account number.
    password : str
        MT5 password — NEVER logged.
    server : str
        MT5 broker server name.
    """

    def __init__(self, login: int, password: str, server: str) -> None:
        self._login = login
        self._password = password
        self._server = server
        self._connected = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """
        Initialize MT5, login, and verify the connection.
        Raises RuntimeError on any failure.
        """
        import MetaTrader5 as mt5  # lazy import so tests can mock it

        logger.info("Initialising MetaTrader 5 terminal…")
        if not mt5.initialize():
            error = mt5.last_error()
            raise RuntimeError(
                f"mt5.initialize() failed: code={error[0]} message='{error[1]}'"
            )

        logger.info("Logging into MT5 account (login=***REDACTED***, server=%s)…", self._server)
        ok = mt5.login(
            login=self._login,
            password=self._password,
            server=self._server,
        )
        if not ok:
            error = mt5.last_error()
            mt5.shutdown()
            raise RuntimeError(
                f"mt5.login() failed: code={error[0]} message='{error[1]}'"
            )

        self._connected = True
        self._log_account_info(mt5)

    def disconnect(self) -> None:
        """Shut down the MT5 terminal connection."""
        try:
            import MetaTrader5 as mt5
            mt5.shutdown()
            logger.info("MT5 terminal disconnected.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error during MT5 shutdown: %s", exc)
        finally:
            self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_account_info(self) -> dict:
        """
        Return a dict of safe (non-credential) account information.
        """
        import MetaTrader5 as mt5
        info = mt5.account_info()
        if info is None:
            return {}
        return {
            "login": info.login,
            "name": info.name,
            "server": info.server,
            "broker": info.company,
            "currency": info.currency,
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.margin_free,
            "leverage": info.leverage,
            "trade_mode": self._trade_mode_str(info.trade_mode),
            "trade_allowed": info.trade_allowed,
            "trade_expert": info.trade_expert,
        }

    def assert_demo_or_allowed(self, allow_live: bool = False) -> None:
        """
        Raise RuntimeError if the account is LIVE and allow_live is False.
        This is the live-trading safety gate.
        """
        import MetaTrader5 as mt5
        info = mt5.account_info()
        if info is None:
            raise RuntimeError("Cannot retrieve account info to verify trading mode.")

        is_demo = info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO  # type: ignore[attr-defined]

        if not is_demo and not allow_live:
            raise RuntimeError(
                "SAFETY: This is a LIVE account but ALLOW_LIVE_TRADING=false. "
                "Set TRADING_MODE=LIVE and ALLOW_LIVE_TRADING=true to proceed."
            )

        if not is_demo:
            logger.warning(
                "LIVE TRADING ACTIVE on account %s at %s. "
                "Real money is at risk.",
                info.login,
                info.server,
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _log_account_info(self, mt5) -> None:
        info = mt5.account_info()
        if info is None:
            logger.warning("Could not retrieve account info.")
            return

        logger.info("=" * 60)
        logger.info("MT5 Account Connected")
        logger.info("  Broker       : %s", info.company)
        logger.info("  Account      : %s", info.login)
        logger.info("  Server       : %s", info.server)
        logger.info("  Currency     : %s", info.currency)
        logger.info("  Balance      : %.2f", info.balance)
        logger.info("  Equity       : %.2f", info.equity)
        logger.info("  Margin       : %.2f", info.margin)
        logger.info("  Free Margin  : %.2f", info.margin_free)
        logger.info("  Leverage     : 1:%d", info.leverage)
        logger.info("  Trade Mode   : %s", self._trade_mode_str(info.trade_mode))
        logger.info("  Trade Allowed: %s", info.trade_allowed)
        logger.info("  Expert Allowed: %s", info.trade_expert)
        logger.info("=" * 60)

    @staticmethod
    def _trade_mode_str(mode: int) -> str:
        try:
            import MetaTrader5 as mt5
            modes = {
                mt5.ACCOUNT_TRADE_MODE_DEMO: "DEMO",       # type: ignore[attr-defined]
                mt5.ACCOUNT_TRADE_MODE_CONTEST: "CONTEST",  # type: ignore[attr-defined]
                mt5.ACCOUNT_TRADE_MODE_REAL: "LIVE",        # type: ignore[attr-defined]
            }
            return modes.get(mode, f"UNKNOWN({mode})")
        except Exception:  # noqa: BLE001
            return f"MODE({mode})"
