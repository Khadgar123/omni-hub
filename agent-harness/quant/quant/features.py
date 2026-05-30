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


# --------------------------------------------------------------------------
# candle geometry as CONTINUOUS ratios (Qlib Alpha158 "KBar" family)
#
# The principled replacement for *named* candlestick patterns. A candle's shape
# is a downsampling artifact of the chosen timeframe (Mandelbrot self-affinity;
# 缠论 包含处理 deliberately merges the body away) — so "hammer / engulfing"
# labels are not fundamental. But the GEOMETRY (body size, shadow lengths, close
# location) still carries information; the right encoding is threshold-free
# continuous ratios, not discrete pattern names. A "long upper-shadow rejection"
# is just ``up_shadow ≈ 1``; a "bull/bear body" is the sign+size of ``body``.
# --------------------------------------------------------------------------

def candle_geometry(bars: Sequence[dict]) -> dict[str, Series]:
    """Per-bar continuous shape ratios (defined for every bar, no warmup).

    ``/range`` ratios fall back to 0.0 on a zero-range bar (no shape to read).

      body      (KMID)  = (close-open)/open        signed body == intraday return
      rng       (KLEN)  = (high-low)/open          amplitude
      body_pct  (KMID2) = (close-open)/range        body share of range   [-1,1]
      up_shadow (KUP2)  = (high-max(open,close))/range  upper-wick share   [0,1]
      dn_shadow (KLOW2) = (min(open,close)-low)/range    lower-wick share  [0,1]
      close_loc (KSFT2) = (2*close-high-low)/range       close within bar  [-1,1]
    """
    keys = ("body", "rng", "body_pct", "up_shadow", "dn_shadow", "close_loc")
    out: dict[str, Series] = {k: [None] * len(bars) for k in keys}
    for i, b in enumerate(bars):
        o, h, low, c = float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"])
        rng = h - low
        out["body"][i] = (c - o) / o if o else 0.0
        out["rng"][i] = rng / o if o else 0.0
        if rng > 0:
            out["body_pct"][i] = (c - o) / rng
            out["up_shadow"][i] = (h - max(o, c)) / rng
            out["dn_shadow"][i] = (min(o, c) - low) / rng
            out["close_loc"][i] = (2 * c - h - low) / rng
        else:
            out["body_pct"][i] = out["up_shadow"][i] = 0.0
            out["dn_shadow"][i] = out["close_loc"][i] = 0.0
    return out


# --------------------------------------------------------------------------
# range-position (mean-reversion) + distance-from-mean (statistical "extreme")
# --------------------------------------------------------------------------

def stoch_k(bars: Sequence[dict], n: int = 14) -> Series:
    """Stochastic %K (Qlib RSV): where ``close`` sits in the trailing ``n``-bar
    [low, high] band, in [0,100]. The overbought/oversold + range-position
    primitive. Flat band -> 50 (neutral)."""
    if n <= 0:
        raise ValueError("n must be positive")
    h, low_, c = highs(bars), lows(bars), closes(bars)
    out: Series = [None] * len(bars)
    for i in range(n - 1, len(bars)):
        hi = max(h[i - n + 1 : i + 1])
        lo = min(low_[i - n + 1 : i + 1])
        rng = hi - lo
        out[i] = 100.0 * (c[i] - lo) / rng if rng > 0 else 50.0
    return out


def zscore(values: Sequence[float], n: int = 20) -> Series:
    """Distance from the rolling mean in stdevs: ``(v - mean_n) / std_n``.

    The unit-free, timeframe-comparable measure of "how stretched is price" —
    the rigorous replacement for eyeballing a long candle far from its MA. A
    Bollinger touch is just ``|z| ≈ k``. Flat window -> 0.0."""
    if n <= 1:
        raise ValueError("n must be > 1")
    out: Series = [None] * len(values)
    for i in range(n - 1, len(values)):
        w = values[i - n + 1 : i + 1]
        m = sum(w) / n
        sd = math.sqrt(sum((v - m) ** 2 for v in w) / n)  # population (TA convention)
        out[i] = (values[i] - m) / sd if sd > 0 else 0.0
    return out


# --------------------------------------------------------------------------
# MACD (+ histogram == the 缠论 背驰 / momentum-acceleration substrate)
# --------------------------------------------------------------------------

def _ema_skipna(series: Sequence[float | None], n: int) -> Series:
    """EMA over a series with a leading ``None`` warmup (e.g. another
    indicator's output): seed with the SMA of the first ``n`` valid values and
    re-pad the front so the result stays aligned. Assumes the valid region is a
    contiguous tail (true for our indicator outputs)."""
    out: Series = [None] * len(series)
    idx = [i for i, v in enumerate(series) if v is not None]
    if len(idx) < n:
        return out
    e = ema([series[i] for i in idx], n)  # type: ignore[list-item]
    for off, i in enumerate(idx):
        out[i] = e[off]
    return out


def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, Series]:
    """MACD line / signal / histogram.

    ``macd = EMA_fast - EMA_slow``; ``signal = EMA(macd, signal)``;
    ``hist = macd - signal``. The histogram is the momentum-acceleration proxy;
    comparing the histogram AREA of successive same-direction legs is the
    quantified 缠论 背驰 (divergence) test."""
    ef, es = ema(values, fast), ema(values, slow)
    line: Series = [(a - b) if (a is not None and b is not None) else None
                    for a, b in zip(ef, es)]
    sig = _ema_skipna(line, signal)
    hist: Series = [(l - s) if (l is not None and s is not None) else None
                    for l, s in zip(line, sig)]
    return {"macd": line, "signal": sig, "hist": hist}


# --------------------------------------------------------------------------
# volume / flow + regime (Hurst) primitives
# --------------------------------------------------------------------------

def obv(bars: Sequence[dict]) -> Series:
    """On-balance volume: running signed-volume total (the volume/flow base
    feature). ``out[0]=0``; adds volume on up-closes, subtracts on down-closes."""
    out: Series = [None] * len(bars)
    if not bars:
        return out
    run = 0.0
    out[0] = 0.0
    for i in range(1, len(bars)):
        c, pc = float(bars[i]["close"]), float(bars[i - 1]["close"])
        v = float(bars[i].get("volume", 0.0))
        if c > pc:
            run += v
        elif c < pc:
            run -= v
        out[i] = run
    return out


def hurst_exponent(values: Sequence[float], *, min_lag: int = 2, max_lag: int = 20) -> float | None:
    """Hurst exponent via the lagged-difference (variance-scaling) method.

    ``H < 0.5`` anti-persistent (mean-reverting), ``≈0.5`` random walk,
    ``> 0.5`` persistent (trending). Chan's regime classifier: choose
    mean-reversion vs momentum by which side of 0.5 the series sits. Slope of
    ``log(std of τ-lag diffs)`` vs ``log(τ)``. Returns None if too short / degenerate."""
    n = len(values)
    if max_lag <= min_lag or n < max_lag + 2:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for lag in range(min_lag, max_lag + 1):
        diffs = [values[i] - values[i - lag] for i in range(lag, n)]
        if len(diffs) < 2:
            continue
        m = sum(diffs) / len(diffs)
        var = sum((d - m) ** 2 for d in diffs) / len(diffs)
        if var <= 0:
            continue
        xs.append(math.log(lag))
        ys.append(0.5 * math.log(var))  # log(std) = 0.5*log(var)
    if len(xs) < 2:
        return None
    k = len(xs)
    mx, my = sum(xs) / k, sum(ys) / k
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


# --------------------------------------------------------------------------
# compression / range-regime features ("横盘是趋势的酝酿" — coiled energy)
#
# Volatility compression reliably predicts THAT a move is coming and how big
# (vol mean-reverts / clusters) — the single most robust effect available; it
# does NOT predict direction. Pair these with a range detector (choppiness /
# efficiency_ratio / hurst) before treating a quiet tape as "incubating a trend"
# rather than "mid-trend drift".
# --------------------------------------------------------------------------

def bandwidth_pct(values: Sequence[float], n: int = 20, k: float = 2.0,
                  lookback: int = 126) -> Series:
    """Percentile rank (0..1) of Bollinger BandWidth within its own trailing
    ``lookback``. Low rank == compressed/coiled (bottom-20% = a squeeze).
    None until ≥5 valid widths in the window."""
    width = bollinger(values, n, k)["width"]
    out: Series = [None] * len(values)
    for i in range(len(values)):
        if width[i] is None:
            continue
        win = [w for w in width[max(0, i - lookback + 1): i + 1] if w is not None]
        if len(win) < 5:
            continue
        out[i] = sum(1 for w in win if w <= width[i]) / len(win)
    return out


def squeeze_on(bars: Sequence[dict], n: int = 20, bb_k: float = 2.0,
               kc_mult: float = 1.5) -> list[bool | None]:
    """TTM-style squeeze: True when the Bollinger Bands sit ENTIRELY inside the
    Keltner Channels (volatility compressed → stored energy). KC = EMA(close,n)
    ± kc_mult·ATR(n). None during warmup."""
    cl = closes(bars)
    bb = bollinger(cl, n, bb_k)
    mid = ema(cl, n)
    a = atr(bars, n)
    out: list[bool | None] = [None] * len(bars)
    for i in range(len(bars)):
        if None in (bb["upper"][i], bb["lower"][i], mid[i], a[i]):
            continue
        kc_u, kc_l = mid[i] + kc_mult * a[i], mid[i] - kc_mult * a[i]
        out[i] = bool(bb["lower"][i] > kc_l and bb["upper"][i] < kc_u)
    return out


def atr_ratio(bars: Sequence[dict], n: int = 14, slow: int = 100) -> Series:
    """ATR(n) / SMA(ATR(n), slow): < 1 == current volatility below its own
    baseline (compressed); > 1 == expanded. None until the slow mean exists."""
    a = atr(bars, n)
    valid = [(i, v) for i, v in enumerate(a) if v is not None]
    vals = [v for _, v in valid]
    sm = sma(vals, slow)
    out: Series = [None] * len(bars)
    for off, (i, _) in enumerate(valid):
        if sm[off] and sm[off] > 0:
            out[i] = vals[off] / sm[off]
    return out


def choppiness(bars: Sequence[dict], n: int = 14) -> Series:
    """Choppiness Index in [0,100]: ``100·log10(Σ TR_n / (maxHigh_n − minLow_n)) /
    log10(n)``. ≥ 61.8 == ranging/choppy, ≤ 38.2 == trending. None during warmup."""
    if n < 2:
        raise ValueError("n must be >= 2")
    tr = true_range(bars)
    h, low_ = highs(bars), lows(bars)
    logn = math.log10(n)
    out: Series = [None] * len(bars)
    for i in range(n, len(bars)):          # i>=n => tr slice starts at >=1 (non-None)
        tr_sum = sum(tr[i - n + 1: i + 1])  # type: ignore[arg-type]
        rng = max(h[i - n + 1: i + 1]) - min(low_[i - n + 1: i + 1])
        if rng > 0 and tr_sum > 0:
            out[i] = 100.0 * math.log10(tr_sum / rng) / logn
    return out


def efficiency_ratio(values: Sequence[float], n: int = 10) -> Series:
    """Kaufman Efficiency Ratio in [0,1]: ``|v[i]−v[i−n]| / Σ|Δv|`` over ``n``.
    High (≈1) == clean directional move (trending); low (< ~0.3) == choppy."""
    if n <= 0:
        raise ValueError("n must be positive")
    out: Series = [None] * len(values)
    for i in range(n, len(values)):
        net = abs(values[i] - values[i - n])
        vol = sum(abs(values[k] - values[k - 1]) for k in range(i - n + 1, i + 1))
        out[i] = net / vol if vol > 0 else 0.0
    return out


# --------------------------------------------------------------------------
# position / extremeness ("位置" as a continuous score, not a chart line)
#
# Direction's core factor is POSITION, but chart S/R is fuzzy and fails at
# breakouts. These turn "extreme high/low" into a continuous, asset-agnostic
# extremeness score from price alone (an on-chain cost-basis layer — MVRV/realized
# price — and a derivatives-crowding layer can be blended in once those feeds are
# wired). Extremeness ALONE is necessary-not-sufficient: fade only when it pairs
# with momentum exhaustion in a range regime; in a trend regime price stays extreme.
# --------------------------------------------------------------------------

def mayer_multiple(values: Sequence[float], n: int = 200) -> Series:
    """Price / SMA(n) — the Mayer Multiple (n=200 standard). A continuous
    valuation/extremeness ratio: historically >~2.4 = froth, <~0.8 = oversold."""
    ma = sma(values, n)
    out: Series = [None] * len(values)
    for i in range(len(values)):
        if ma[i]:
            out[i] = values[i] / ma[i]
    return out


def percentile_rank(values: Sequence[float], lookback: int = 252) -> Series:
    """Where the current value sits within its trailing ``lookback`` window, in
    [0,1] — the non-parametric 'how extreme is my position' score. None until
    ≥5 points in the window."""
    out: Series = [None] * len(values)
    for i in range(len(values)):
        win = values[max(0, i - lookback + 1): i + 1]
        if len(win) < 5:
            continue
        out[i] = sum(1 for v in win if v <= values[i]) / len(win)
    return out


def last_valid(series: Sequence[float | None]) -> float | None:
    """Most recent non-None value (the point-in-time reading)."""
    for v in reversed(series):
        if v is not None:
            return v
    return None
