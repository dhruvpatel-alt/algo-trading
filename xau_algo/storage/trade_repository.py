"""
xau_algo/storage/trade_repository.py
=======================================
Persists closed trades to disk.

Two formats written simultaneously:
  - logs/trades.csv   → easy to open in Excel / pandas
  - logs/trades.jsonl → one JSON object per line (machine-readable)
"""

from __future__ import annotations

import csv
import json
import logging
import os
from typing import TYPE_CHECKING

from xau_algo import config

if TYPE_CHECKING:
    from xau_algo.trading.position import Position
    from xau_algo.storage.supabase_repository import SupabaseRepository

logger = logging.getLogger(__name__)

_FIELDNAMES = [
    "id",
    "strategy",
    "side",
    "lot",
    "entry_price",
    "stop_loss",
    "take_profit",
    "risk_reward",
    "setup_time",
    "entry_time",
    "exit_time",
    "exit_price",
    "status",
    "pnl",
]


class TradeRepository:
    """Append-only store for closed positions."""

    def __init__(
        self,
        csv_path: str | None = None,
        jsonl_path: str | None = None,
        supabase_repo: "SupabaseRepository | None" = None,
    ) -> None:
        self._csv_path = csv_path or config.TRADES_CSV
        self._jsonl_path = jsonl_path or self._csv_path.replace(".csv", ".jsonl")
        self._supabase = supabase_repo

        os.makedirs(os.path.dirname(self._csv_path), exist_ok=True)
        self._ensure_csv_header()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_trade(self, position: "Position") -> None:
        """Append a closed position to CSV, JSONL, and optionally Supabase."""
        record = position.to_dict()

        # CSV
        try:
            with open(self._csv_path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
                writer.writerow(record)
        except OSError as exc:
            logger.error("Failed to write trade to CSV: %s", exc)

        # JSONL
        try:
            with open(self._jsonl_path, "a") as f:
                f.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.error("Failed to write trade to JSONL: %s", exc)

        # Supabase (async, non-blocking)
        if self._supabase is not None:
            self._supabase.save_trade(position)

    def load_trades(self) -> list[dict]:
        """Load all trades from the CSV file."""
        if not os.path.exists(self._csv_path):
            return []
        try:
            with open(self._csv_path, "r", newline="") as f:
                reader = csv.DictReader(f)
                return list(reader)
        except OSError as exc:
            logger.error("Failed to read trades CSV: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_csv_header(self) -> None:
        """Write header row if the file doesn't exist yet."""
        if not os.path.exists(self._csv_path):
            try:
                with open(self._csv_path, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
                    writer.writeheader()
            except OSError as exc:
                logger.error("Failed to create trades CSV: %s", exc)
