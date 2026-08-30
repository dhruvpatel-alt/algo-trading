"""
xau_algo/data/twelve_data_client.py
=====================================
Twelve Data REST API client with automatic key rotation.

Key rotation rules
------------------
- When a request returns HTTP 429 (rate limit) or a JSON error related to
  the API key → rotate to the next key.
- Rotation is logged with key INDEX only (e.g., "KEY_2") — the actual key
  value is NEVER printed or logged.
- If all keys are exhausted → raises RuntimeError.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from xau_algo import config

logger = logging.getLogger(__name__)

# Twelve Data error codes that indicate API key issues
_KEY_ERROR_CODES = {400, 401, 403, 429}
_KEY_ERROR_MESSAGES = {
    "apikey",
    "api_key",
    "rate limit",
    "quota",
    "unauthorized",
    "forbidden",
    "too many requests",
}


class TwelveDataClient:
    """
    REST client for Twelve Data API with multi-key rotation.
    """

    def __init__(
        self,
        api_keys: list[str] | None = None,
        base_url: str | None = None,
    ) -> None:
        self._keys = api_keys or config.TWELVE_DATA_API_KEYS
        self._base_url = base_url or config.TWELVE_DATA_BASE_URL
        self._key_index = 0

        if not self._keys:
            raise ValueError("No API keys provided.")

        logger.info("TwelveDataClient initialised with %d API key(s).", len(self._keys))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_time_series(
        self,
        symbol: str | None = None,
        interval: str | None = None,
        outputsize: int | None = None,
        **extra_params: Any,
    ) -> list[dict]:
        """
        Fetch historical OHLCV candles.

        Returns
        -------
        list[dict]
            Chronological list of candle dicts:
            [{datetime, open, high, low, close, volume}, ...]
            Oldest candle first.
        """
        symbol = symbol or config.SYMBOL
        interval = interval or config.TIMEFRAME
        outputsize = outputsize or config.HISTORICAL_OUTPUTSIZE

        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            **extra_params,
        }

        data = self._get("/time_series", params)
        values = data.get("values", [])

        # Twelve Data returns newest-first — reverse for chronological order
        values = list(reversed(values))

        # Cast numeric fields
        candles = []
        for v in values:
            candles.append({
                "datetime": v["datetime"],
                "open":  float(v["open"]),
                "high":  float(v["high"]),
                "low":   float(v["low"]),
                "close": float(v["close"]),
                "volume": float(v.get("volume", 0)),
            })

        logger.info(
            "Fetched %d candles for %s (%s).",
            len(candles),
            symbol,
            interval,
        )
        return candles

    def get_quote(self, symbol: str | None = None) -> dict:
        """Fetch the latest quote for a symbol."""
        return self._get("/quote", {"symbol": symbol or config.SYMBOL})

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @property
    def _current_key(self) -> str:
        return self._keys[self._key_index]

    def _rotate_key(self) -> None:
        """Move to the next available API key."""
        next_index = (self._key_index + 1) % len(self._keys)
        if next_index == self._key_index:
            raise RuntimeError(
                "All Twelve Data API keys exhausted or rate-limited."
            )
        logger.warning(
            "Rotating API key: KEY_%d → KEY_%d",
            self._key_index + 1,
            next_index + 1,
        )
        self._key_index = next_index

    def _get(
        self,
        endpoint: str,
        params: dict,
        max_retries: int = len(config.TWELVE_DATA_API_KEYS) + 1,
    ) -> dict:
        """
        Make a GET request with automatic key rotation on rate-limit errors.
        """
        url = self._base_url + endpoint
        attempt = 0

        while attempt < max_retries:
            try:
                req_params = {**params, "apikey": self._current_key}
                response = requests.get(url, params=req_params, timeout=15)

                # HTTP-level rate limit
                if response.status_code == 429:
                    logger.warning("HTTP 429 on KEY_%d — rotating.", self._key_index + 1)
                    self._rotate_key()
                    time.sleep(1)
                    attempt += 1
                    continue

                response.raise_for_status()
                data: dict = response.json()

                # Check for API-level errors embedded in JSON body
                if self._is_key_error(data):
                    msg = data.get("message", "")
                    logger.warning(
                        "API key error on KEY_%d: '%s' — rotating.",
                        self._key_index + 1,
                        msg,
                    )
                    self._rotate_key()
                    attempt += 1
                    continue

                # Check for other non-ok statuses in JSON
                if data.get("status") == "error":
                    raise RuntimeError(
                        f"Twelve Data API error: {data.get('message', 'unknown')}"
                    )

                return data

            except requests.exceptions.RequestException as exc:
                logger.error("Request failed: %s — retrying in 2s.", exc)
                time.sleep(2)
                attempt += 1

        raise RuntimeError(
            f"Failed to fetch {endpoint} after {max_retries} attempts."
        )

    @staticmethod
    def _is_key_error(data: dict) -> bool:
        """Return True if the response indicates an API key problem."""
        if data.get("status") != "error":
            return False
        message = data.get("message", "").lower()
        return any(kw in message for kw in _KEY_ERROR_MESSAGES)
