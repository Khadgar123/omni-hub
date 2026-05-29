"""Point-in-time technical indicators (pure stdlib; no third-party deps).

These operate on plain lists of floats / bar dicts so the math is unit-testable
without duckdb/pyarrow/numpy — the same discipline as
``market_store.bars_from_trades``.  Two invariants:

  * **Causal / point-in-time:** ``out[i]`` uses only inputs ``[0..i]``; warmup
    positions are ``None``.  A regime/strategy reading ``out[-1]`` therefore
    never peeks at the future.
  * **Aligned:** every function returns a list the same length as its input, so
    results zip 1:1 with the bars.

Bar dicts carry at least ``open/high/low/close/volume`` (the
``market_store`` bar schema) and are assumed sorted ascending by ``bucket_ts``.
"""

from __future__ import annotations

import math
from typing import Sequence

Num = float
Series = list[Num | None]


# --------------------------------------------------------------------------
# column extractors
# --------------------------------------------------------------------------

def closes(bars: Sequence[dict]) -> list[float]:
    return [float(b["close"]) for b in bars]


def highs(bars: Sequence[dict]) -> list[float]:
    return [float(b["high"]) for b in bars]


def lows(bars: Sequence[dict]) -> list[float]:
    return [float(b["low"]) for b in bars]


# --------------------------------------------------------------------------
# moving averages
# --------------------------------------------------------------------------

def sma(values: Sequence[float], n: int) -> Series:
    if n <= 0:
        raise ValueError("n must be positive")
    out: Series = [None] * len(values)
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= n:
            run -= values[i - n]
        if i >= n - 1:
            out[i] = run / n
    return out


def ema(values: Sequence[float], n: int) -> Series:
    """EMA seeded with the SMA of the first ``n`` values (Wilder/TA convention)."""
    if n <= 0:
        raise ValueError("n must be positive")
    out: Series = [None] * len(values)
    if len(values) < n:
        return out
    k = 2.0 / (n + 1.0)
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def slope(values: Sequence[float | None], n: int) -> Series:
    """Per-bar change of ``values`` over ``n`` bars: ``(v[i] - v[i-n]) / n``.

    ``None`` inputs (indicator warmup) propagate to ``None`` outputs.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    out: Series = [None] * len(values)
    for i in range(n, len(values)):
        a, b = values[i], values[i - n]
        if a is not None and b is not None:
            out[i] = (a - b) / n
    return out


# --------------------------------------------------------------------------
# Wilder-smoothed indicators (RSI / ATR / ADX)
# --------------------------------------------------------------------------

def rsi(values: Sequence[float], n: int = 14) -> Series:
    """Wilder's RSI.  All-up window -> ~100, all-down -> ~0, no move -> 50."""
    out: Series = [None] * len(values)
    if len(values) <= n:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, n + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_gain = gains / n
    avg_loss = losses / n
    out[n] = _rsi_from(avg_gain, avg_loss)
    for i in range(n + 1, len(values)):
        d = values[i] - values[i - 1]
        avg_gain = (avg_gain * (n - 1) + max(d, 0.0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-d, 0.0)) / n
        out[i] = _rsi_from(avg_gain, avg_loss)
    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def true_range(bars: Sequence[dict]) -> Series:
    """True range per bar; ``None`` for the first bar (needs a prior close)."""
    out: Series = [None] * len(bars)
    for i in range(1, len(bars)):
        h = float(bars[i]["high"])
        low = float(bars[i]["low"])
        pc = float(bars[i - 1]["close"])
        out[i] = max(h - low, abs(h - pc), abs(low - pc))
    return out


def atr(bars: Sequence[dict], n: int = 14) -> Series:
    """Wilder's ATR (smoothed true range)."""
    tr = true_range(bars)
    out: Series = [None] * len(bars)
    # need n TR values, i.e. bars[1..n]
    if len(bars) <= n:
        return out
    seed = sum(tr[1 : n + 1]) / n  # type: ignore[arg-type]
    out[n] = seed
    prev = seed
    for i in range(n + 1, len(bars)):
        prev = (prev * (n - 1) + tr[i]) / n  # type: ignore[operator]
        out[i] = prev
    return out


def adx(bars: Sequence[dict], n: int = 14) -> dict[str, Series]:
    """Wilder's ADX / +DI / -DI.

    Returns ``{"adx", "plus_di", "minus_di"}`` aligned lists.  ADX rises in a
    sustained directional move and stays low in a choppy range — the strength
    gate of the regime committee.
    """
    size = len(bars)
    plus_di: Series = [None] * size
    minus_di: Series = [None] * size
    adx_out: Series = [None] * size
    if size <= 2 * n:
        return {"adx": adx_out, "plus_di": plus_di, "minus_di": minus_di}

    tr = [0.0] * size
    plus_dm = [0.0] * size
    minus_dm = [0.0] * size
    for i in range(1, size):
        h, low = float(bars[i]["high"]), float(bars[i]["low"])
        ph, pl, pc = float(bars[i - 1]["high"]), float(bars[i - 1]["low"]), float(bars[i - 1]["close"])
        up_move = h - ph
        down_move = pl - low
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr[i] = max(h - low, abs(h - pc), abs(low - pc))

    # Wilder smoothing (sum form): seed = sum of first n, then prev - prev/n + cur
    str_ = sum(tr[1 : n + 1])
    sp = sum(plus_dm[1 : n + 1])
    sm = sum(minus_dm[1 : n + 1])
    dx_list: list[float] = []
    for i in range(n + 1, size):
        str_ = str_ - str_ / n + tr[i]
        sp = sp - sp / n + plus_dm[i]
        sm = sm - sm / n + minus_dm[i]
        pdi = 100.0 * sp / str_ if str_ else 0.0
        mdi = 100.0 * sm / str_ if str_ else 0.0
        plus_di[i] = pdi
        minus_di[i] = mdi
        denom = pdi + mdi
        dx = 100.0 * abs(pdi - mdi) / denom if denom else 0.0
        dx_list.append(dx)
        # once we have n DX values, seed ADX with their mean, then Wilder-smooth
        if len(dx_list) == n:
            adx_out[i] = sum(dx_list) / n
        elif len(dx_list) > n:
            adx_out[i] = (adx_out[i - 1] * (n - 1) + dx) / n  # type: ignore[operator]
    return {"adx": adx_out, "plus_di": plus_di, "minus_di": minus_di}


# --------------------------------------------------------------------------
# Bollinger / ROC / realized volatility
# --------------------------------------------------------------------------

def bollinger(values: Sequence[float], n: int = 20, k: float = 2.0) -> dict[str, Series]:
    """Bollinger bands; ``width = (upper - lower) / mid`` (squeeze detector)."""
    mid = sma(values, n)
    upper: Series = [None] * len(values)
    lower: Series = [None] * len(values)
    width: Series = [None] * len(values)
    for i in range(n - 1, len(values)):
        window = values[i - n + 1 : i + 1]
        m = mid[i]
        var = sum((v - m) ** 2 for v in window) / n  # population stdev (TA convention)
        sd = math.sqrt(var)
        upper[i] = m + k * sd
        lower[i] = m - k * sd
        width[i] = (upper[i] - lower[i]) / m if m else 0.0
    return {"mid": mid, "upper": upper, "lower": lower, "width": width}


def roc(values: Sequence[float], n: int) -> Series:
    """Rate of change (momentum): ``(v[i] / v[i-n] - 1)``."""
    if n <= 0:
        raise ValueError("n must be positive")
    out: Series = [None] * len(values)
    for i in range(n, len(values)):
        base = values[i - n]
        out[i] = (values[i] / base - 1.0) if base else None
    return out


def log_returns(values: Sequence[float]) -> Series:
    out: Series = [None] * len(values)
    for i in range(1, len(values)):
        a, b = values[i], values[i - 1]
        if a > 0 and b > 0:
            out[i] = math.log(a / b)
    return out


def realized_vol(values: Sequence[float], n: int = 20) -> Series:
    """Rolling stdev of log returns over ``n`` bars (raw, not annualized).

    Regime buckets this by percentile, so the unit is irrelevant — only the
    relative level matters.
    """
    lr = log_returns(values)
    out: Series = [None] * len(values)
    for i in range(n, len(values)):
        window = [x for x in lr[i - n + 1 : i + 1] if x is not None]
        if len(window) < 2:
            continue
        mean = sum(window) / len(window)
        var = sum((x - mean) ** 2 for x in window) / (len(window) - 1)
        out[i] = math.sqrt(var)
    return out


def last_valid(series: Sequence[float | None]) -> float | None:
    """Most recent non-None value (the point-in-time reading)."""
    for v in reversed(series):
        if v is not None:
            return v
    return None
