"""Multi-level support/resistance — objective, scored, timeframe-aware.

S/R exists at every timeframe and the levels are NOT equal: the level you TRADE
sets the S/R for entry/stop, but HIGHER-timeframe S/R dominates (a 1d wall
overrides a 5m one) and is the target/barrier. This module computes S/R two
independent ways and fuses them across timeframes with a confluence score, then
exposes per-bar features (distance to nearest level / ATR, reward:risk by levels)
for a structure-driven strategy.

Methods:
  * ``volume_profile`` — POC / Value Area (VAH/VAL, 70%) / HVN / LVN from the
    (low, high, volume) distribution. Volume is spread uniformly across the bins
    a bar spans (TPO-style — better than close-bucketing for crypto's wide bars).
  * ``swing_levels`` — cluster swing pivots into S/R zones via a 1-D agglomerative
    (sorted-gap) merge; strength = recency-decayed touch weight.
  * ``confluence`` — fuse per-timeframe levels; score = Σ tf_weight·strength
    (multi-TF agreement is the dominant strength term).
  * ``nearest_levels`` — per-price features: nearest support/resistance, distances
    (ATR-normalized), reward:risk by levels.

Pure stdlib — agglomerative clustering is a 1-D sorted-merge (no numpy/sklearn).
"""

from __future__ import annotations

import math
from typing import Sequence

from quant import structure


def volume_profile(bars: Sequence[dict], *, n_bins: int = 50, value_area: float = 0.70) -> dict:
    """Volume-by-price profile. Returns ``{poc, vah, val, hvn, lvn, bin_centers,
    profile, bin_width}``. POC = max-volume price; Value Area = the contiguous
    band around POC holding ``value_area`` of total volume; HVN/LVN = local
    volume peaks/valleys (barriers / fast-travel gaps)."""
    if not bars:
        return {"poc": None, "vah": None, "val": None, "hvn": [], "lvn": [],
                "bin_centers": [], "profile": [], "bin_width": 0.0}
    los = [float(b["low"]) for b in bars]
    his = [float(b["high"]) for b in bars]
    lo, hi = min(los), max(his)
    if hi <= lo:                                   # degenerate: a single price
        vol = sum(float(b.get("volume", 0.0)) for b in bars)
        return {"poc": lo, "vah": lo, "val": lo, "hvn": [lo], "lvn": [],
                "bin_centers": [lo], "profile": [vol], "bin_width": 0.0}
    width = (hi - lo) / n_bins
    centers = [lo + (j + 0.5) * width for j in range(n_bins)]
    profile = [0.0] * n_bins
    for b in bars:
        v = float(b.get("volume", 0.0))
        if v <= 0:
            continue
        j0 = min(n_bins - 1, max(0, int((float(b["low"]) - lo) / width)))
        j1 = min(n_bins - 1, max(0, int((float(b["high"]) - lo) / width)))
        share = v / (j1 - j0 + 1)
        for j in range(j0, j1 + 1):
            profile[j] += share
    total = sum(profile)
    poc_idx = max(range(n_bins), key=lambda j: profile[j])
    lo_i = hi_i = poc_idx
    acc = profile[poc_idx]
    target = total * value_area
    while acc < target and (lo_i > 0 or hi_i < n_bins - 1):
        below = profile[lo_i - 1] if lo_i > 0 else -1.0
        above = profile[hi_i + 1] if hi_i < n_bins - 1 else -1.0
        # annex the heavier neighbor; on a tie expand the side closer to the POC
        # so the value area stays centered (not dragged through empty bins).
        if above > below or (above == below and (hi_i - poc_idx) <= (poc_idx - lo_i)):
            hi_i += 1
            acc += profile[hi_i]
        else:
            lo_i -= 1
            acc += profile[lo_i]
    hvn = [centers[j] for j in range(1, n_bins - 1)
           if profile[j] > profile[j - 1] and profile[j] > profile[j + 1]]
    lvn = [centers[j] for j in range(1, n_bins - 1)
           if profile[j] < profile[j - 1] and profile[j] < profile[j + 1] and profile[j] > 0]
    return {"poc": centers[poc_idx], "vah": centers[hi_i], "val": centers[lo_i],
            "hvn": hvn, "lvn": lvn, "bin_centers": centers, "profile": profile,
            "bin_width": width}


def swing_levels(bars: Sequence[dict], *, left: int = 3, right: int = 3,
                 merge_pct: float = 0.005, halflife: int = 200) -> list[dict]:
    """Cluster swing pivots into S/R zones. Sorted-gap agglomerative merge:
    adjacent pivots within ``merge_pct`` (and whose cluster span stays within
    ``2·merge_pct``) join one zone. Strength = Σ recency-decayed touch weight
    (``0.5 ** ((N-1-idx)/halflife)``). Returns zones sorted ascending by price,
    each ``{price, strength, touches, n_high, n_low, first_idx, last_idx}``."""
    sw = structure.swings(bars, left, right)
    if not sw:
        return []
    n = len(bars)

    def w(idx: int) -> float:
        return 0.5 ** ((n - 1 - idx) / halflife)

    pts = sorted(({"price": float(s["price"]), "idx": s["idx"], "kind": s["kind"]}
                  for s in sw), key=lambda p: p["price"])
    clusters: list[list[dict]] = [[pts[0]]]
    for p in pts[1:]:
        cur = clusters[-1]
        if (abs(p["price"] - cur[-1]["price"]) <= merge_pct * cur[-1]["price"]
                and abs(p["price"] - cur[0]["price"]) <= 2 * merge_pct * cur[0]["price"]):
            cur.append(p)
        else:
            clusters.append([p])
    out: list[dict] = []
    for cl in clusters:
        wsum = sum(w(p["idx"]) for p in cl)
        price = sum(p["price"] * w(p["idx"]) for p in cl) / wsum if wsum > 0 else cl[0]["price"]
        out.append({
            "price": price, "strength": wsum, "touches": len(cl),
            "n_high": sum(1 for p in cl if p["kind"] == "high"),
            "n_low": sum(1 for p in cl if p["kind"] == "low"),
            "first_idx": min(p["idx"] for p in cl),
            "last_idx": max(p["idx"] for p in cl),
        })
    out.sort(key=lambda x: x["price"])
    return out


def round_levels(price: float, *, atr: float | None = None, span_atr: float = 8.0,
                 pct_span: float = 0.06) -> list[dict]:
    """Psychological round-number levels near ``price`` (a peer-reviewed barrier:
    BTC closes within 2% of each $10k 15-30× before breaching). Grid auto-tiers to
    price magnitude (≥10k→1000, ≥1k→500, ≥100→50, else 10); a multiple of 10×grid
    (e.g. 70000) gets ``tier=2`` vs 1. Bidirectional — these are magnets, not
    signed. Returns ``[{price, tier}]`` within ±``span_atr·atr`` (or ±``pct_span``)."""
    if price <= 0:
        return []
    major = 1000.0 if price >= 10000 else 500.0 if price >= 1000 else 50.0 if price >= 100 else 10.0
    span = (span_atr * atr) if atr else (pct_span * price)
    lo, hi = price - span, price + span
    out = []
    for k in range(int(math.floor(lo / major)), int(math.ceil(hi / major)) + 1):
        p = k * major
        if lo <= p <= hi and p > 0:
            out.append({"price": p, "tier": 2.0 if (p % (major * 10) == 0) else 1.0})
    return out


def scored_levels(bars: Sequence[dict], *, left: int = 3, right: int = 3,
                  merge_pct: float = 0.005, atr: float | None = None,
                  vp_bins: int = 48, vp_lookback: int | None = None) -> list[dict]:
    """One SYMMETRIC S/R map: swing-pivot clusters are the backbone; a level's
    strength is BOOSTED by confluence with volume-profile nodes (×1.5) and round
    numbers (×(1+0.5·tier)) — fixed economic weights, NOT tunable parameters.
    VP/round nodes with no nearby swing are added as weaker standalone levels so
    an untested wall (e.g. a fresh round number) still appears. Direction is NOT
    stored: a level is bidirectional and the caller decides long/short by
    side-of-approach. Returns levels sorted by price, each
    ``{price, strength, kind, touches}``.

    Swing uses the FULL ``bars`` (its halflife decay fades old pivots, so feeding
    deep history sharpens structure rather than cluttering it); VP uses only the
    last ``vp_lookback`` bars when set, so deep history does NOT smear the volume
    profile into mush."""
    levels = swing_levels(bars, left=left, right=right, merge_pct=merge_pct)
    for L in levels:
        L["kind"] = "swing"
    vp = volume_profile(bars[-vp_lookback:] if vp_lookback else bars, n_bins=vp_bins)
    vp_nodes = [(vp["poc"], 1.0), (vp["vah"], 0.7), (vp["val"], 0.7)]
    vp_nodes += [(h, 0.5) for h in vp.get("hvn", [])]
    price_ref = float(bars[-1]["close"]) if bars else 0.0
    rounds = round_levels(price_ref, atr=atr)

    def near(a: float, b: float) -> bool:
        return b > 0 and abs(a - b) <= merge_pct * b

    for L in levels:                                   # confluence boosts the backbone
        if any(p is not None and near(L["price"], p) for p, _ in vp_nodes):
            L["strength"] *= 1.5
            L["kind"] += "+vp"
        for r in rounds:
            if near(L["price"], r["price"]):
                L["strength"] *= (1.0 + 0.5 * r["tier"])
                L["kind"] += "+round"
                break
    base = (sum(L["strength"] for L in levels) / len(levels)) if levels else 1.0

    def covered(p: float) -> bool:
        return any(near(L["price"], p) for L in levels)

    for p, wfrac in vp_nodes:                          # standalone VP nodes
        if p is not None and not covered(p):
            levels.append({"price": float(p), "strength": base * wfrac * 0.5,
                           "kind": "vp", "touches": 0})
    for r in rounds:                                   # standalone round walls
        if not covered(r["price"]):
            levels.append({"price": r["price"], "strength": base * 0.25 * r["tier"],
                           "kind": "round", "touches": 0})
    levels.sort(key=lambda x: x["price"])
    return levels


def confluence(levels_by_tf: dict, *, tf_weight: dict, merge_pct: float = 0.005) -> list[dict]:
    """Fuse per-timeframe S/R levels. Levels within ``merge_pct`` across any TFs
    merge into one zone whose ``confluence_score`` = Σ ``tf_weight[tf]·strength``.
    Returns zones sorted by descending confluence (the strongest walls first),
    each ``{price, confluence_score, tfs, n_tf}``."""
    tagged = []
    for tf, lvls in levels_by_tf.items():
        wtf = tf_weight.get(tf, 1.0)
        for L in lvls:
            tagged.append({"price": float(L["price"]), "tf": tf,
                           "score": wtf * float(L.get("strength", 1.0))})
    if not tagged:
        return []
    tagged.sort(key=lambda x: x["price"])
    clusters: list[list[dict]] = [[tagged[0]]]
    for t in tagged[1:]:
        if abs(t["price"] - clusters[-1][-1]["price"]) <= merge_pct * clusters[-1][-1]["price"]:
            clusters[-1].append(t)
        else:
            clusters.append([t])
    out = []
    for cl in clusters:
        score = sum(t["score"] for t in cl)
        price = sum(t["price"] * t["score"] for t in cl) / score if score > 0 else cl[0]["price"]
        tfs = sorted({t["tf"] for t in cl})
        out.append({"price": price, "confluence_score": score, "tfs": tfs, "n_tf": len(tfs)})
    out.sort(key=lambda x: -x["confluence_score"])
    return out


def nearest_levels(price: float, levels: Sequence[dict], *, atr: float | None = None,
                   key: str = "price") -> dict:
    """Per-price S/R features: nearest support (level below) and resistance
    (level above), their distances (ATR-normalized if ``atr`` given), and
    ``rr_by_levels`` = (distance to resistance)/(distance to support) — the
    reward:risk a long would have targeting the next level and stopping at the
    one below. Missing side -> None."""
    below = [L for L in levels if L[key] < price]
    above = [L for L in levels if L[key] > price]
    sup = max(below, key=lambda L: L[key])[key] if below else None
    res = min(above, key=lambda L: L[key])[key] if above else None
    s = (price - sup) if sup is not None else None
    r = (res - price) if res is not None else None
    denom = atr if atr else 1.0
    return {
        "support": sup, "resistance": res,
        "dist_to_support": (s / denom) if s is not None else None,
        "dist_to_resistance": (r / denom) if r is not None else None,
        "rr_by_levels": (r / s) if (s and r and s > 0) else None,
    }
