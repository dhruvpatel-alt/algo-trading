"""
xau_algo/indicators/ema.py
==========================
Pure EMA calculation — no side effects, no global state.

Formula:
    k   = 2 / (period + 1)
    EMA = price * k + EMA_prev * (1 - k)

The first EMA value is seeded with the simple mean of the first `period` closes.
Returns None for indices where there are fewer than `period` data points.
"""

from __future__ import annotations


def calculate_ema(closes: list[float], period: int) -> list[float | None]:
    """
    Calculate EMA for an entire list of close prices.

    Parameters
    ----------
    closes : list[float]
        Chronological list of close prices (index 0 = oldest).
    period : int
        EMA period (e.g., 20 or 50).

    Returns
    -------
    list[float | None]
        EMA values. The first (period - 1) entries are None (warmup).
    """
    if period <= 0:
        raise ValueError(f"EMA period must be positive, got {period}")

    n = len(closes)
    result: list[float | None] = [None] * n

    if n < period:
        return result

    k = 2.0 / (period + 1)

    # Seed: simple mean of first `period` values
    seed = sum(closes[:period]) / period
    result[period - 1] = seed

    for i in range(period, n):
        prev = result[i - 1]
        result[i] = closes[i] * k + prev * (1.0 - k)  # type: ignore[operator]

    return result


def calculate_ema_incremental(
    new_close: float,
    prev_ema: float | None,
    period: int,
    warmup_closes: list[float] | None = None,
) -> float | None:
    """
    Update EMA incrementally with a single new close price.

    Used by live/streaming code where we don't want to recompute the full series
    on every tick.

    Parameters
    ----------
    new_close : float
        The newest close price.
    prev_ema : float | None
        The EMA value from the previous candle. Pass None during warmup.
    period : int
        EMA period.
    warmup_closes : list[float] | None
        Must be provided when prev_ema is None and we have exactly `period`
        closes available (seeds the first EMA).

    Returns
    -------
    float | None
        Updated EMA, or None if still in warmup.
    """
    if prev_ema is None:
        # Still in warmup — try to seed if we have enough data
        if warmup_closes is not None and len(warmup_closes) == period:
            seed = sum(warmup_closes) / period
            k = 2.0 / (period + 1)
            return new_close * k + seed * (1.0 - k)
        return None

    k = 2.0 / (period + 1)
    return new_close * k + prev_ema * (1.0 - k)
