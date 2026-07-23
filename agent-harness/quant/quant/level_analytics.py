"""Empirically validate the S/R level map — the honest, comprehensive version.

Two questions, both answered CAUSALLY (levels computed only from bars BEFORE the event), with
confidence intervals so we don't fool ourselves:

  1. CLUSTERING — do significant reversals sit on a strong level MORE than a random price would?
     ``coverage`` = % of reversals within ``tol`` ATR of a top-k strong level; ``base`` = the same for
     random prices (the level-density base rate). ``mult = coverage/base``; if its CI excludes 1.0 the
     level map genuinely marks turning points. (Naive ±0.5 ATR over the full dense map gives mult≈1 —
     vacuous — so we sparsify to the strongest few and tighten the tolerance.)

  2. EDGE-BY-REGIME — the forward return (ATR units) after touching support / resistance, split by
     trend regime (up / down / range). This is what's tradeable: a level's reaction DIRECTION is
     regime-dependent (with-trend continuation, not reversal).

Pure + injectable data fetch, so the whole thing is unit-testable with no network.
"""
from __future__ import annotations

import math
import statistics as st
from collections.abc import Sequence

from quant import levels, structure
from quant.features import atr as _atr


def find_reversals(bars: Sequence[dict], *, atr_series: Sequence, swing=(3, 3),
                   min_move_atr: float = 2.0, horizon: int = 20) -> list[tuple]:
    """Significant reversals = swing pivots (fractal) that led to a >= ``min_move_atr`` move the
    OTHER way within ``horizon`` bars (filters out noise pivots). Returns ``[(idx, price, kind)]``."""
    high = [float(b["high"]) for b in bars]
    low = [float(b["low"]) for b in bars]
    out = []
    for s in structure.swings(bars, swing[0], swing[1]):
        i = s["idx"]
        if i >= len(bars) - horizon or i >= len(atr_series):
            continue
        a = atr_series[i]
        if not a:
            continue
        p = float(s["price"])
        if s["kind"] == "high" and min(low[i + 1:i + 1 + horizon]) <= p - min_move_atr * a:
            out.append((i, p, "high"))
        elif s["kind"] == "low" and max(high[i + 1:i + 1 + horizon]) >= p + min_move_atr * a:
            out.append((i, p, "low"))
    return out


def regime_at(closes: Sequence[float], i: int, atr_val: float, *, window: int = 30,
              flat: float = 0.06) -> str:
    """Causal trend label from the ATR-normalized slope over ``window`` bars: up / down / range."""
    if i < window or not atr_val:
        return "range"
    slope = (closes[i] - closes[i - window]) / (window * atr_val)
    return "up" if slope > flat else "down" if slope < -flat else "range"


def regime4_at(closes: Sequence[float], i: int, atr_val: float, *, window: int = 30, flat: float = 0.06) -> str:
    """4-way: down / up / range_up (chop after a rise) / range_down (chop after a fall) — splits the
    flat regime by the PRIOR leg's direction, the user's four scenarios."""
    if i < 2 * window or not atr_val:
        return "range_up"
    cur = (closes[i] - closes[i - window]) / (window * atr_val)
    pre = (closes[i - window] - closes[i - 2 * window]) / (window * atr_val)
    if cur > flat:
        return "up"
    if cur < -flat:
        return "down"
    return "range_up" if pre >= 0 else "range_down"


def level_reaction(bars: Sequence[dict], *, atr_series: Sequence, tol_atr: float = 0.25, react_atr: float = 1.0,
                   topk: int = 8, level_window: int = 250, horizon: int = 48, level_stride: int = 3,
                   cooldown: int = 12, method: str = "combined") -> dict:
    """At each FRESH touch of a strong level: first-passage HOLD (price reverses ``react_atr`` the right
    way) vs BREAK (continues ``react_atr`` through), AND the max PENETRATION past the level on holds —
    the wick depth = the disturbance zone = the 误杀 band. The stop buffer that survives noise without
    being fooled ≈ the 90th-pctile hold-penetration (below it you get falsely stopped; the break
    threshold ``react_atr`` is where a real break is confirmed, so any buffer in between is safe).
    Split by regime(4) × side(sup/res). Returns per-cell ``{n, hold_rate, pen_p50, pen_p90}``."""
    closes = [float(b["close"]) for b in bars]
    high = [float(b["high"]) for b in bars]
    low = [float(b["low"]) for b in bars]
    cells: dict = {}
    last = {"sup": -10 ** 9, "res": -10 ** 9}
    lv: list[dict] = []
    for i in range(level_window, len(bars) - horizon):
        a = atr_series[i]
        if not a:
            continue
        if (i - level_window) % level_stride == 0 or not lv:
            lv = _strong_levels(bars[i - level_window:i], atr_val=a, topk=topk, method=method)
        p = closes[i]
        below = [x["price"] for x in lv if x["price"] < p]
        above = [x["price"] for x in lv if x["price"] > p]
        reg = regime4_at(closes, i, a)
        for side in ("sup", "res"):
            lvl = (max(below) if below else None) if side == "sup" else (min(above) if above else None)
            if lvl is None or i - last[side] < cooldown:
                continue
            touched = (low[i] <= lvl + tol_atr * a) if side == "sup" else (high[i] >= lvl - tol_atr * a)
            if not touched:
                continue
            last[side] = i
            pen, res = 0.0, None
            for j in range(i, min(i + horizon, len(bars))):
                if side == "sup":
                    pen = max(pen, (lvl - low[j]) / a)
                    if high[j] >= lvl + react_atr * a:
                        res = "hold"; break
                    if low[j] <= lvl - react_atr * a:
                        res = "break"; break
                else:
                    pen = max(pen, (high[j] - lvl) / a)
                    if low[j] <= lvl - react_atr * a:
                        res = "hold"; break
                    if high[j] >= lvl + react_atr * a:
                        res = "break"; break
            if res is None:
                continue
            for ckey in ((reg, side), ("ALL", "ALL")):           # also keep a pooled cell for method compare
                c = cells.setdefault(ckey, {"hold": 0, "break": 0, "pen": []})
                c[res] += 1
                if res == "hold":
                    c["pen"].append(pen)
    out = {}
    for key, c in cells.items():
        n = c["hold"] + c["break"]
        if n < 12:
            continue
        ph = sorted(c["pen"])
        out[key] = {"n": n, "hold_rate": round(c["hold"] / n, 2),
                    "pen_p50": round(ph[len(ph) // 2], 2) if ph else None,
                    "pen_p90": round(ph[min(len(ph) - 1, int(len(ph) * 0.9))], 2) if ph else None}
    return out


def _levels_by_method(window_bars: Sequence[dict], *, atr_val: float, method: str) -> list[dict]:
    """S/R levels from ONE construction method, so we can compare which is cleanest:
    'swing' (pivot clusters) | 'vp' (volume-profile nodes) | 'round' (psychological) | 'combined' (all)."""
    if method == "swing":
        return [{"price": x["price"], "strength": x.get("strength", 1.0)} for x in levels.swing_levels(window_bars)]
    if method == "vp":
        vp = levels.volume_profile(window_bars)
        out = [{"price": float(vp[k]), "strength": w} for k, w in (("poc", 1.0), ("vah", 0.7), ("val", 0.7))
               if vp.get(k)]
        out += [{"price": float(h), "strength": 0.5} for h in vp.get("hvn", [])]
        return out
    if method == "round":
        ref = float(window_bars[-1]["close"]) if window_bars else 0.0
        return [{"price": r["price"], "strength": r["tier"]} for r in levels.round_levels(ref, atr=atr_val)]
    return levels.scored_levels(window_bars, atr=atr_val)            # 'combined' (default)


def _strong_levels(window_bars: Sequence[dict], *, atr_val: float, topk: int, method: str = "combined") -> list[dict]:
    lv = _levels_by_method(window_bars, atr_val=atr_val, method=method)
    return sorted(lv, key=lambda x: -x.get("strength", 0.0))[:topk]


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple:
    """Wilson score 95% CI for a proportion k/n (better than normal approx for small n / extreme p)."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (centre - half) / denom), min(1.0, (centre + half) / denom))


def _mean_ci(xs: Sequence[float]) -> dict:
    if len(xs) < 8:
        return {"mean": None, "n": len(xs), "ci": (None, None)}
    m = st.fmean(xs)
    se = st.pstdev(xs) / math.sqrt(len(xs))
    return {"mean": round(m, 3), "n": len(xs), "ci": (round(m - 1.96 * se, 3), round(m + 1.96 * se, 3))}


def reversal_clustering(bars: Sequence[dict], *, atr_series: Sequence, tol_atr: float = 0.2,
                        topk: int = 5, level_window: int = 250, min_move_atr: float = 2.0,
                        controls: int = 10, seed: int = 7) -> dict:
    """Do reversals cluster at strong levels beyond chance? ``coverage`` vs random-price ``base``,
    with Wilson CIs and the multiple (coverage/base). mult CI excluding 1.0 = significant."""
    import random

    rng = random.Random(seed)
    closes = [float(b["close"]) for b in bars]
    revs = [(i, p, k) for (i, p, k) in find_reversals(bars, atr_series=atr_series, min_move_atr=min_move_atr)
            if i >= level_window]
    hit, base_hit, base_n = 0, 0, 0
    for i, price, _ in revs:
        a = atr_series[i]
        lv = _strong_levels(bars[i - level_window:i - 2], atr_val=a, topk=topk)
        if any(abs(price - x["price"]) <= tol_atr * a for x in lv):
            hit += 1
        for _ in range(controls):
            rp = closes[i] + (rng.random() * 2 - 1) * 4 * a
            if any(abs(rp - x["price"]) <= tol_atr * a for x in lv):
                base_hit += 1
            base_n += 1
    n = len(revs)
    cov = hit / n if n else 0.0
    base = base_hit / base_n if base_n else 0.0
    return {"n": n, "coverage": round(cov, 3), "base": round(base, 3), "lift": round(cov - base, 3),
            "mult": round(cov / base, 2) if base else None,
            "cov_ci": tuple(round(x, 3) for x in wilson_ci(hit, n)),
            "base_ci": tuple(round(x, 3) for x in wilson_ci(base_hit, base_n))}


def regime_edge(bars: Sequence[dict], *, atr_series: Sequence, tol_atr: float = 0.3, horizon: int = 12,
                topk: int = 8, level_window: int = 250, level_stride: int = 1, raw: bool = False) -> dict:
    """Forward ``horizon``-bar return (ATR units) after touching support / resistance, by regime.
    Positive = price rose. ``level_stride`` recomputes the level map every N bars (speed on long
    history; levels barely move bar-to-bar). ``raw=True`` also returns the pooled return lists."""
    closes = [float(b["close"]) for b in bars]
    buckets = {r: {"sup": [], "res": []} for r in ("up", "down", "range")}
    lv: list[dict] = []
    for i in range(level_window, len(bars) - horizon):
        a = atr_series[i]
        if not a:
            continue
        if (i - level_window) % level_stride == 0 or not lv:
            lv = _strong_levels(bars[i - level_window:i], atr_val=a, topk=topk)
        p = closes[i]
        fwd = (closes[i + horizon] - p) / a
        reg = regime_at(closes, i, a)
        below = [x["price"] for x in lv if x["price"] < p]
        above = [x["price"] for x in lv if x["price"] > p]
        if below and (p - max(below)) <= tol_atr * a:
            buckets[reg]["sup"].append(fwd)
        if above and (min(above) - p) <= tol_atr * a:
            buckets[reg]["res"].append(fwd)
    out = {r: {"sup": _mean_ci(b["sup"]), "res": _mean_ci(b["res"])} for r, b in buckets.items()}
    if raw:
        out["_raw"] = buckets
    return out


def analyze(symbol: str, tf: str, *, fetch, limit: int = 1000, defs=((2.0, (3, 3)),),
            level_stride: int = 1) -> dict:
    """Full analysis for one (symbol, tf): clustering (over several reversal definitions) + regime edge."""
    bars = fetch(symbol, tf, limit=limit)
    a = list(_atr(bars, 14))
    clustering = [{"min_move_atr": mm, "swing": sw,
                   **reversal_clustering(bars, atr_series=a, min_move_atr=mm)}
                  for (mm, sw) in defs]
    return {"symbol": symbol, "tf": tf, "bars": len(bars), "clustering": clustering,
            "regime_edge": regime_edge(bars, atr_series=a, level_stride=level_stride)}


def run(symbols, tfs, *, fetch=None, limit: int = 1000, max_bars=None, level_stride: int = 1) -> list[dict]:
    """Comprehensive sweep over symbols × timeframes. ``fetch(symbol, tf, limit=)`` is injectable; if
    omitted and ``max_bars`` is set, pages full history via ``live.fetch_history``."""
    if fetch is None:
        from quant import live

        if max_bars:
            def fetch(sym, tf, *, limit):  # noqa: E306
                return live.fetch_history(sym, tf, venue="binance", max_bars=max_bars)
        else:
            def fetch(sym, tf, *, limit):  # noqa: E306
                return live.fetch_candles(sym, tf, venue="binance", limit=limit)
    out = []
    for sym in symbols:
        for tf in tfs:
            try:
                out.append(analyze(sym, tf, fetch=fetch, limit=(max_bars or limit), level_stride=level_stride))
            except Exception as e:  # noqa: BLE001
                out.append({"symbol": sym, "tf": tf, "error": str(e)})
    return out


def render(rows: list[dict]) -> str:
    """Human table: clustering multiple (×, with CI) + regime edge (with-trend vs counter-trend)."""
    out = ["== S/R level analytics (causal, 95% CI) =="]
    out.append("  反转聚集: mult=反转落强位 / 随机基准 (CI 不含 1.0 才显著)")
    for r in rows:
        if r.get("error"):
            out.append(f"  {r['symbol']:<8}{r['tf']}: ERR {r['error'][:40]}")
            continue
        c = r["clustering"][0]
        cc, bc = c["cov_ci"], c["base_ci"]
        out.append(f"  {r['symbol']:<8}{r['tf']:<3} n_rev={c['n']:<3} "
                   f"聚集 {100*c['coverage']:.0f}%[{100*cc[0]:.0f}-{100*cc[1]:.0f}] "
                   f"vs随机 {100*c['base']:.0f}%[{100*bc[0]:.0f}-{100*bc[1]:.0f}] "
                   f"×{c['mult']}")
        e = r["regime_edge"]

        def cell(d):
            return f"{d['mean']:+.2f}(n{d['n']})" if d["mean"] is not None else f"n/a(n{d['n']})"
        out.append(f"        edge↑ 撑{cell(e['up']['sup'])} 压{cell(e['up']['res'])} | "
                   f"↓ 撑{cell(e['down']['sup'])} 压{cell(e['down']['res'])} | "
                   f"震 撑{cell(e['range']['sup'])} 压{cell(e['range']['res'])}")
    return "\n".join(out)


def main(argv=None):
    import argparse
    import sys

    p = argparse.ArgumentParser(prog="quant.level_analytics", description=__doc__)
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    p.add_argument("--tfs", default="1h,4h,1d")
    p.add_argument("--limit", type=int, default=1000)
    args = p.parse_args(argv)
    rows = run(args.symbols.split(","), args.tfs.split(","), limit=args.limit)
    sys.stdout.write(render(rows) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
