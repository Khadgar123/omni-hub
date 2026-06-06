"""Multi-level feature aggregation (Q4/Q7) — causally attach lower-timeframe
structure to each higher-timeframe bar, plus the 区间套 (nested-interval) gate.

The several 1m trends nested inside a flat 30m bar are not noise — they are the
order-flow fingerprint of who is winning inside the range (rising CVD while price
is flat = hidden accumulation → up-bias). ``aggregate`` rolls LTF structure
(BOS/CHoCH counts + net direction, swing count, CVD-proxy flow, realized return)
into a feature vector attached to each HTF bar. ``nested_divergence`` is the
区间套 AND gate: act on a HTF divergence only when a same-direction LTF divergence
confirms it at the same time — "大级别定方向, 小级别找拐点".

CAUSALITY (the look-ahead trap that silently inflates MTF backtests): a HTF bar's
LTF-derived features are only KNOWN at that HTF bar's CLOSE. The row is therefore
a valid predictor input only from the NEXT HTF bar onward — read it as "the
just-closed HTF bar's LTF fingerprint" and shift(1) before using it to decide.

Pure stdlib. CVD here is a tick-rule PROXY (OHLCV bars carry no aggressor side);
a true taker-delta CVD from the trades layer replaces it before live promotion.
"""

from __future__ import annotations

import bisect
from typing import Sequence

from quant import structure
from quant.features import closes


def cvd_proxy(bars: Sequence[dict]) -> list[float | None]:
    """Tick-rule CVD proxy: cumulative signed volume by close-to-close direction
    (no true aggressor side in OHLCV bars). ``out[0]=0``. Approximation — flagged."""
    out: list[float | None] = [None] * len(bars)
    if not bars:
        return out
    run = 0.0
    out[0] = 0.0
    for i in range(1, len(bars)):
        c, pc = float(bars[i]["close"]), float(bars[i - 1]["close"])
        v = float(bars[i].get("volume", 0.0))
        run += v if c > pc else (-v if c < pc else 0.0)
        out[i] = run
    return out


def aggregate(htf_bars: Sequence[dict], ltf_bars: Sequence[dict], *,
              left: int = 3, right: int = 3) -> list[dict]:
    """Per-HTF-bar feature vector from the LTF bars nested inside it. Returns a
    list aligned to ``htf_bars``; each row is KNOWN ONLY at that HTF bar's close
    (shift(1) to use as a predictor). Window for HTF bar i = [ts_i, ts_{i+1}).

    Row: ``{ts, n_ltf, n_bos, n_choch, bos_net, n_swings, cvd_delta, ltf_ret}``.
    ``bos_net`` ∈ [-1,1] = net structural direction (up breaks − down breaks);
    ``cvd_delta`` = net tick-rule flow across the window."""
    if not htf_bars:
        return []
    lts = [int(b["bucket_ts"]) for b in ltf_bars]
    lc = closes(ltf_bars)
    cvd = cvd_proxy(ltf_bars)
    ms = structure.market_structure(ltf_bars, left=left, right=right) if ltf_bars else []
    sw = structure.swings(ltf_bars, left, right) if len(ltf_bars) > left + right else []
    bnd = [int(b["bucket_ts"]) for b in htf_bars]
    out: list[dict] = []
    for i, b in enumerate(htf_bars):
        lo = bnd[i]
        hi = bnd[i + 1] if i + 1 < len(htf_bars) else None
        a = bisect.bisect_left(lts, lo)
        z = bisect.bisect_left(lts, hi) if hi is not None else len(lts)
        n = z - a
        win = [e for e in ms if lo <= e["ts"] and (hi is None or e["ts"] < hi)]
        up = sum(1 for e in win if e["dir"] == "up")
        dn = sum(1 for e in win if e["dir"] == "down")
        tot = up + dn
        out.append({
            "ts": int(b["bucket_ts"]), "n_ltf": n,
            "n_bos": sum(1 for e in win if e["type"] == "BOS"),
            "n_choch": sum(1 for e in win if e["type"] == "CHoCH"),
            "bos_net": (up - dn) / tot if tot else 0.0,
            "n_swings": sum(1 for s in sw if lo <= s["ts"] and (hi is None or s["ts"] < hi)),
            "cvd_delta": (cvd[z - 1] - cvd[a]) if n >= 2 else 0.0,
            "ltf_ret": (lc[z - 1] / lc[a] - 1.0) if n >= 2 and lc[a] else 0.0,
        })
    return out


def nested_divergence(htf_bars: Sequence[dict], ltf_bars: Sequence[dict], *,
                      left: int = 3, right: int = 3, ratio: float = 0.9,
                      window_us: int | None = None) -> list[dict]:
    """区间套 gate: a HTF 背驰 confirmed by a SAME-DIRECTION LTF 背驰 at (or just
    before) the same time. ``window_us`` defaults to one HTF bar duration.
    Returns confirmed HTF events with the confirming LTF event attached:
    ``{ts, dir, htf_ratio, ltf_ts, ltf_ratio}``."""
    hd = [d for d in structure.divergence(htf_bars, left=left, right=right, ratio=ratio)
          if d["is_divergence"]]
    ld = [d for d in structure.divergence(ltf_bars, left=left, right=right, ratio=ratio)
          if d["is_divergence"]]
    if window_us is None:
        ts = [int(b["bucket_ts"]) for b in htf_bars]
        window_us = (ts[1] - ts[0]) if len(ts) >= 2 else 0
    out: list[dict] = []
    for h in hd:
        conf = [l for l in ld if l["dir"] == h["dir"]
                and h["ts"] - window_us <= l["ts"] <= h["ts"]]
        if conf:
            best = max(conf, key=lambda l: l["ts"])      # closest at/before the HTF turn
            out.append({"ts": h["ts"], "dir": h["dir"], "htf_ratio": h["metric_ratio"],
                        "ltf_ts": best["ts"], "ltf_ratio": best["metric_ratio"]})
    return out
