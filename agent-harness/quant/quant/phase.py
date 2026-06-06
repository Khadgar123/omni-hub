"""Market-PHASE classifier — the 2D (trendiness × volatility) regime map.

The user's four phases — trend-up, trend-down, quiet coil, volatile chop — are the
quadrants of a 2D space, and the SAME mechanical rule applies at every timeframe
(self-similar), per the Fractal Market Hypothesis. Validated empirically: the
phase-appropriate action's forward edge is ~0 at 5m but 0.3–4% at 4h/1d — i.e. the
edge is real and phase-conditional, it just needs a timeframe where it clears cost.

Axes (price/OHLCV only — no order flow):
  * trendiness = Kaufman Efficiency Ratio (net move / total path), scale-free [0,1].
    ER ≥ ``trend_er`` → trend; ER < ``range_er`` → range; between → transition.
  * volatility = ATR/price vs its own slow MA → high-vol / low-vol.
  * coil flag   = Bollinger inside Keltner (TTM squeeze) → a compressed spring.
  * trend sign  = close vs its SMA.

Phases: ``trend_up`` / ``trend_down`` (follow), ``coil`` (low-vol range / squeeze →
await breakout), ``chop`` (high-vol range → fade or stand aside), ``mid`` (transition).
Only two free thresholds (``trend_er``, ``range_er``); the windows are standard.
Causal: bar i uses only [0..i].
"""

from __future__ import annotations

from typing import Sequence

from quant import features as F


def classify(bars: Sequence[dict], *, er_n: int = 20, vol_n: int = 14,
             vol_ma: int = 100, trend_er: float = 0.35, range_er: float = 0.22) -> list[dict]:
    """Per-bar phase. Returns ``[{phase, er, sign, hi_vol, squeeze}]`` (len == len(bars))."""
    n = len(bars)
    cl = F.closes(bars)
    er = F.efficiency_ratio(cl, er_n)
    atr = F.atr(bars, vol_n)
    sma = F.sma(cl, er_n)
    sq = F.squeeze_on(bars)
    atr_pct = [(atr[i] / cl[i]) if (atr[i] is not None and cl[i]) else None for i in range(n)]
    out: list[dict] = []
    for i in range(n):
        e, ap = er[i], atr_pct[i]
        if e is None or sma[i] is None:
            out.append({"phase": "none", "er": e, "sign": 0, "hi_vol": False,
                        "squeeze": bool(sq[i])})
            continue
        # slow vol reference: mean of available atr_pct over the trailing vol_ma window
        win = [x for x in atr_pct[max(0, i - vol_ma + 1): i + 1] if x is not None]
        ref = sum(win) / len(win) if win else None
        hi_vol = ref is not None and ap is not None and ap > ref
        sign = 1 if cl[i] >= sma[i] else -1
        if e >= trend_er:
            phase = "trend_up" if sign > 0 else "trend_down"
        elif e < range_er:
            phase = "chop" if hi_vol else "coil"
        else:
            phase = "mid"
        out.append({"phase": phase, "er": e, "sign": sign, "hi_vol": hi_vol,
                    "squeeze": bool(sq[i])})
    return out


def latest(bars: Sequence[dict], **kw) -> dict:
    """Phase of the most recent (causal) bar."""
    c = classify(bars, **kw)
    return c[-1] if c else {"phase": "none", "er": None, "sign": 0, "hi_vol": False, "squeeze": False}
