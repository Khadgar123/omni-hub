"""Price STRUCTURE as discrete events on a continuous series — the
non-morphology alternative to candlestick patterns.

The thesis (Mandelbrot fractal markets; 缠论 包含处理/级别): a candle's *shape*
is a downsampling artifact of the chosen timeframe, so named patterns
(hammer / engulfing / ...) are not fundamental. What IS timeframe-robust is
STRUCTURE — the swing pivots of price and the breaks between them. This module
encodes structure as events:

  * ``swings``  — swing highs/lows with a parameterized ``(left, right)`` window.
    The window IS the timeframe knob: a 5-bar Williams fractal, a 50-bar SMC
    swing, and a 缠论 笔 are the same operator at different scales.
  * ``fractals`` — the 3-bar 缠论 分型 (== Williams fractal with left=right=1).
  * ``market_structure`` — BOS (Break Of Structure, continuation) and CHoCH
    (Change of Character, reversal) events from the swing sequence, decided on
    CLOSES (the ICT/SMC convention; a wick alone does not break structure).

Causal discipline (matches ``quant.features``): a swing pivot at bar ``i`` is
only CONFIRMABLE at bar ``i + right`` (you must see ``right`` bars after it), so
``market_structure`` admits a pivot only once that bar exists and never peeks
ahead. Pure stdlib, same bar-dict schema (``open/high/low/close/volume`` +
``bucket_ts``) as the rest of the package.
"""

from __future__ import annotations

from typing import Sequence

from quant.features import closes, highs, lows


def swings(bars: Sequence[dict], left: int = 2, right: int = 2) -> list[dict]:
    """Swing highs/lows: bar ``i`` is a swing high iff its high strictly exceeds
    the ``left`` highs before and ``right`` highs after it (mirror for lows).

    Returns events ``{idx, ts, price, kind}`` (``kind`` in ``{"high","low"}``)
    sorted by ``idx``. Strict comparison avoids double-marking plateaus.
    """
    if left < 1 or right < 1:
        raise ValueError("left and right must be >= 1")
    h, low_ = highs(bars), lows(bars)
    n = len(bars)
    out: list[dict] = []
    for i in range(left, n - right):
        win = range(i - left, i + right + 1)
        ts = int(bars[i].get("bucket_ts", 0))
        if all(h[i] > h[j] for j in win if j != i):
            out.append({"idx": i, "ts": ts, "price": h[i], "kind": "high"})
        elif all(low_[i] < low_[j] for j in win if j != i):
            out.append({"idx": i, "ts": ts, "price": low_[i], "kind": "low"})
    return out


def fractals(bars: Sequence[dict]) -> list[dict]:
    """缠论 分型 / 3-bar Williams fractal == ``swings`` with left=right=1."""
    return swings(bars, 1, 1)


def market_structure(bars: Sequence[dict], *, left: int = 2, right: int = 2) -> list[dict]:
    """BOS / CHoCH events from the swing sequence, decided on closes.

    Walks bars left-to-right; at each bar only swings already CONFIRMED
    (``pivot_idx + right <= i``) are in scope, so the call is causal. When a
    close breaks the most recent confirmed swing high (low), it emits:
      * ``BOS``  if the break continues the prevailing trend (or trend is flat),
      * ``CHoCH`` if it reverses it.
    The broken level is consumed (set aside) until the next swing of that side
    is confirmed, so a single level fires at most once.

    Returns events ``{idx, ts, type, dir, level}`` with ``type`` in
    ``{"BOS","CHoCH"}`` and ``dir`` in ``{"up","down"}``.
    """
    sw = swings(bars, left, right)
    # (confirmation_bar, price) for each side, sorted by confirmation bar
    hi_known = sorted((s["idx"] + right, s["price"]) for s in sw if s["kind"] == "high")
    lo_known = sorted((s["idx"] + right, s["price"]) for s in sw if s["kind"] == "low")
    c = closes(bars)
    events: list[dict] = []
    trend = 0           # +1 up, -1 down, 0 undecided
    last_high: float | None = None
    last_low: float | None = None
    hp = lp = 0
    for i in range(len(bars)):
        while hp < len(hi_known) and hi_known[hp][0] <= i:
            last_high = hi_known[hp][1]
            hp += 1
        while lp < len(lo_known) and lo_known[lp][0] <= i:
            last_low = lo_known[lp][1]
            lp += 1
        ts = int(bars[i].get("bucket_ts", 0))
        if last_high is not None and c[i] > last_high:
            events.append({"idx": i, "ts": ts, "type": "BOS" if trend >= 0 else "CHoCH",
                           "dir": "up", "level": last_high})
            trend = 1
            last_high = None
        elif last_low is not None and c[i] < last_low:
            events.append({"idx": i, "ts": ts, "type": "BOS" if trend <= 0 else "CHoCH",
                           "dir": "down", "level": last_low})
            trend = -1
            last_low = None
    return events
