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

from quant.features import closes, highs, lows, macd


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


# --------------------------------------------------------------------------
# legs + force metrics + 背驰 (divergence) — quantify "力度" / exhaustion
#
# A leg is a directional move between two alternating swing pivots (a ZigZag
# skeleton). Pullback DEPTH/FORCE (反弹和调整的幅度和力度) and 背驰 (向上上不去
# 就要下) are both read off per-leg force metrics: amplitude, speed, volume, and
# the MACD area/peak. 背驰 is the chan.py kernel — a new price extreme made on a
# WEAKER leg (metric ≤ ratio × the prior same-direction leg's metric).
# --------------------------------------------------------------------------

def _zigzag(sw: list[dict]) -> list[dict]:
    """Collapse raw swings into a strictly alternating high/low sequence,
    keeping the more-extreme pivot when two of the same kind are adjacent."""
    seq: list[dict] = []
    for s in sw:
        if seq and seq[-1]["kind"] == s["kind"]:
            more_extreme = (s["price"] > seq[-1]["price"] if s["kind"] == "high"
                            else s["price"] < seq[-1]["price"])
            if more_extreme:
                seq[-1] = s
        else:
            seq.append(s)
    return seq


def legs(bars: Sequence[dict], *, left: int = 2, right: int = 2) -> list[dict]:
    """Directional legs between alternating swing pivots, each annotated with
    force metrics. Per leg: ``dir``, ``amp``/``amp_pct`` (幅度), ``bars``,
    ``slope`` (速度 = amp/bars), ``ret`` (signed), ``vol`` (volume sum), and the
    same-direction MACD ``macd_area`` / ``macd_peak`` (动力学). The substrate for
    pullback-strength and 背驰 analysis."""
    piv = _zigzag(swings(bars, left, right))
    if len(piv) < 2:
        return []
    cl = closes(bars)
    hist = macd(cl)["hist"]
    vols = [float(b.get("volume", 0.0)) for b in bars]
    out: list[dict] = []
    for a, b in zip(piv, piv[1:]):
        i0, i1 = a["idx"], b["idx"]
        up = b["kind"] == "high"               # low -> high == up leg
        p0, p1 = float(a["price"]), float(b["price"])
        nbars = max(i1 - i0, 1)
        seg = [h for h in hist[i0:i1 + 1] if h is not None]
        area = sum(abs(h) for h in seg if (h > 0) == up)
        peak = max((abs(h) for h in seg if (h > 0) == up), default=0.0)
        out.append({
            "i0": i0, "i1": i1, "ts0": a["ts"], "ts1": b["ts"],
            "dir": "up" if up else "down", "p0": p0, "p1": p1,
            "amp": abs(p1 - p0), "amp_pct": abs(p1 - p0) / p0 if p0 else 0.0,
            "bars": nbars, "slope": abs(p1 - p0) / nbars,
            "ret": (p1 - p0) / p0 if p0 else 0.0,
            "vol": sum(vols[i0:i1 + 1]), "macd_area": area, "macd_peak": peak,
        })
    return out


def _leg_metric(leg: dict, algo: str) -> float:
    """Force metric of a leg (the chan.py ``macd_algo`` choices)."""
    if algo == "amp":
        return leg["amp_pct"]                  # amplitude / price (czsc default proxy)
    if algo == "slope":
        return leg["slope"] / leg["p0"] if leg["p0"] else leg["slope"]
    if algo == "area":
        return leg["macd_area"]                # integrated same-sign histogram
    if algo == "peak":
        return leg["macd_peak"]                # max |histogram|
    raise ValueError(f"unknown macd_algo {algo!r}")


def divergence(bars: Sequence[dict], *, left: int = 2, right: int = 2,
               macd_algo: str = "amp", ratio: float = 0.9) -> list[dict]:
    """背驰: compare each leg to the prior SAME-direction leg (j vs j-2). A new
    price extreme made with a WEAKER force metric = momentum diverging from price
    = exhaustion. The chan.py kernel ``out_metric ≤ divergence_rate · in_metric``
    (default ``ratio=0.9``).

    Returns one dict per comparable pair: ``{idx, ts, dir, metric_ratio,
    new_extreme, is_divergence}``. Use ``metric_ratio`` as a CONTINUOUS feature
    (smaller = stronger divergence), gated by structural context — never a raw
    standalone boolean (naive divergence fails OOS; it needs a prior 中枢 / S/R)."""
    lg = legs(bars, left=left, right=right)
    out: list[dict] = []
    for j in range(2, len(lg)):
        cur, prev = lg[j], lg[j - 2]
        if cur["dir"] != prev["dir"]:
            continue
        up = cur["dir"] == "up"
        new_extreme = cur["p1"] > prev["p1"] if up else cur["p1"] < prev["p1"]
        m_prev, m_cur = _leg_metric(prev, macd_algo), _leg_metric(cur, macd_algo)
        mr = m_cur / m_prev if m_prev > 0 else float("inf")
        out.append({"idx": cur["i1"], "ts": cur["ts1"], "dir": cur["dir"],
                    "metric_ratio": mr, "new_extreme": new_extreme,
                    "is_divergence": bool(new_extreme and mr <= ratio)})
    return out


def exhaustion(bars: Sequence[dict], *, core: int = 4, qual: int = 6, length: int = 12) -> list[dict]:
    """Climax / exhaustion bars (Leledc-style) — the 一致→加速→衰竭 footprint.

    A run of > ``qual`` same-direction bars (close vs close ``core`` bars ago = the
    "consistency/acceleration"), where the bar makes a NEW ``length``-bar extreme,
    but CLOSES AGAINST the move (the rejection). ``kind`` 'top' = buyer exhaustion,
    'bottom' = seller exhaustion. Causal (uses only past+current bar).

    A LEADING-EDGE signal, necessary-not-sufficient: most lower-TF exhaustions stay
    local — confirm up the level hierarchy (区间套 / mtf.nested_divergence) and gate
    by position-extremeness before trading. Run-length alone predicts short-horizon
    reversal but longer-horizon continuation, so never use it as a standalone trigger.
    Returns ``{idx, ts, kind, run}``."""
    h, low_, c = highs(bars), lows(bars), closes(bars)
    o = [float(b["open"]) for b in bars]
    out: list[dict] = []
    bindex = sindex = 0
    for i in range(len(bars)):
        if i >= core:
            if c[i] > c[i - core]:
                bindex += 1
                sindex = 0
            elif c[i] < c[i - core]:
                sindex += 1
                bindex = 0
        if i < length:
            continue
        ts = int(bars[i].get("bucket_ts", 0))
        if bindex > qual and c[i] < o[i] and h[i] >= max(h[i - length:i + 1]):
            out.append({"idx": i, "ts": ts, "kind": "top", "run": bindex})
            bindex = 0
        elif sindex > qual and c[i] > o[i] and low_[i] <= min(low_[i - length:i + 1]):
            out.append({"idx": i, "ts": ts, "kind": "bottom", "run": sindex})
            sindex = 0
    return out
