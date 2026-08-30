"""
vt_markets/tests/test_market_data.py
======================================
Tests for MT5MarketData and LiveCandleBuilder.
"""

from __future__ import annotations

import numpy as np
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from vt_markets.mt5.market_data import MT5MarketData, LiveCandleBuilder
from vt_markets.models.candle import Candle
from vt_markets.models.tick import Tick


def _make_rates(n: int = 5) -> list[dict]:
    """Create mock MT5 rate records."""
    base_time = int(datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp())
    rates = []
    for i in range(n):
        rates.append({
            "time": base_time + i * 60,
            "open": 3350.0 + i,
            "high": 3355.0 + i,
            "low": 3348.0 + i,
            "close": 3352.0 + i,
            "tick_volume": 100,
        })
    # Return as numpy structured array-like list (MT5 returns numpy array)
    return rates


class TestMT5MarketData:

    def test_get_historical_candles_returns_correct_count(self, mock_mt5):
        rates = _make_rates(5)
        mock_mt5.copy_rates_from_pos.return_value = rates

        md = MT5MarketData(symbol="XAUUSD")
        candles = md.get_historical_candles(count=5)

        assert len(candles) == 5
        mock_mt5.copy_rates_from_pos.assert_called_once_with(
            "XAUUSD", mock_mt5.TIMEFRAME_M1, 0, 5
        )

    def test_get_historical_candles_are_candle_objects(self, mock_mt5):
        mock_mt5.copy_rates_from_pos.return_value = _make_rates(3)
        md = MT5MarketData(symbol="XAUUSD")
        candles = md.get_historical_candles(count=3)
        for c in candles:
            assert isinstance(c, Candle)

    def test_get_historical_candles_are_timezone_aware(self, mock_mt5):
        mock_mt5.copy_rates_from_pos.return_value = _make_rates(3)
        md = MT5MarketData(symbol="XAUUSD")
        candles = md.get_historical_candles(count=3)
        for c in candles:
            assert c.timestamp.tzinfo is not None

    def test_get_historical_candles_ohlc_correct(self, mock_mt5):
        rates = _make_rates(1)
        mock_mt5.copy_rates_from_pos.return_value = rates
        md = MT5MarketData(symbol="XAUUSD")
        candles = md.get_historical_candles(count=1)
        c = candles[0]
        assert c.open == 3350.0
        assert c.high == 3355.0
        assert c.low == 3348.0
        assert c.close == 3352.0

    def test_get_historical_candles_raises_on_mt5_failure(self, mock_mt5):
        mock_mt5.copy_rates_from_pos.return_value = None
        mock_mt5.last_error.return_value = (-2, "No data")
        md = MT5MarketData(symbol="XAUUSD")
        with pytest.raises(RuntimeError, match="copy_rates_from_pos"):
            md.get_historical_candles(count=5)

    def test_get_latest_tick_returns_tick(self, mock_mt5, mock_tick):
        mock_mt5.symbol_info_tick.return_value = mock_tick
        md = MT5MarketData(symbol="XAUUSD")
        tick = md.get_latest_tick()
        assert isinstance(tick, Tick)
        assert tick.bid == 3350.00
        assert tick.ask == 3350.30

    def test_get_latest_tick_raises_on_failure(self, mock_mt5):
        mock_mt5.symbol_info_tick.return_value = None
        mock_mt5.last_error.return_value = (-3, "Tick error")
        md = MT5MarketData(symbol="XAUUSD")
        with pytest.raises(RuntimeError, match="symbol_info_tick"):
            md.get_latest_tick()


class TestLiveCandleBuilder:

    def _make_tick(self, ts_unix: int, bid: float, ask: float) -> Tick:
        return Tick(
            timestamp=datetime.fromtimestamp(ts_unix, tz=timezone.utc),
            bid=bid,
            ask=ask,
        )

    def test_first_tick_starts_candle(self):
        candles = []
        ticks = []
        builder = LiveCandleBuilder(
            on_candle_closed=candles.append,
            on_tick=ticks.append,
        )
        base = int(datetime(2026, 1, 1, 10, 0, 30, tzinfo=timezone.utc).timestamp())
        tick = self._make_tick(base, 3350.0, 3350.3)
        builder.process_tick(tick)
        assert len(ticks) == 1
        assert len(candles) == 0

    def test_minute_change_closes_candle(self):
        candles = []
        ticks_received = []
        builder = LiveCandleBuilder(
            on_candle_closed=candles.append,
            on_tick=ticks_received.append,
        )
        base_min1 = int(datetime(2026, 1, 1, 10, 0, 30, tzinfo=timezone.utc).timestamp())
        base_min2 = int(datetime(2026, 1, 1, 10, 1, 5, tzinfo=timezone.utc).timestamp())

        builder.process_tick(self._make_tick(base_min1, 3350.0, 3350.3))
        builder.process_tick(self._make_tick(base_min1 + 20, 3351.0, 3351.3))
        assert len(candles) == 0

        # Next minute
        builder.process_tick(self._make_tick(base_min2, 3352.0, 3352.3))
        assert len(candles) == 1   # minute 10:00 closed

    def test_closed_candle_has_correct_ohlc(self):
        candles = []
        builder = LiveCandleBuilder(
            on_candle_closed=candles.append,
            on_tick=lambda t: None,
        )
        base = int(datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp())
        next_min = int(datetime(2026, 1, 1, 10, 1, 0, tzinfo=timezone.utc).timestamp())

        # Mid prices: (bid+ask)/2
        builder.process_tick(self._make_tick(base + 5,  3350.0, 3350.0))   # open  = 3350
        builder.process_tick(self._make_tick(base + 20, 3360.0, 3360.0))   # high  = 3360
        builder.process_tick(self._make_tick(base + 40, 3345.0, 3345.0))   # low   = 3345
        builder.process_tick(self._make_tick(base + 58, 3355.0, 3355.0))   # close = 3355
        builder.process_tick(self._make_tick(next_min,  3356.0, 3356.0))   # triggers close

        assert len(candles) == 1
        c = candles[0]
        assert c.open == pytest.approx(3350.0)
        assert c.high == pytest.approx(3360.0)
        assert c.low == pytest.approx(3345.0)
        assert c.close == pytest.approx(3355.0)

    def test_tick_callback_fires_on_every_tick(self):
        ticks = []
        builder = LiveCandleBuilder(
            on_candle_closed=lambda c: None,
            on_tick=ticks.append,
        )
        base = int(datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp())
        for i in range(5):
            builder.process_tick(self._make_tick(base + i, 3350.0, 3350.3))
        assert len(ticks) == 5
