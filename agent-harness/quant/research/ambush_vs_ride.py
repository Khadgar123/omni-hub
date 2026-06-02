"""Forward-test: 精确仓位 + 埋伏 vs 顺趋势 vs 换方向, at 5m / 30m / 4h.

Variants (long/flat unless noted), cost 8bps/side, BTC+ETH averaged:
  RIDE             顺趋势: long only on a CONFIRMED structure break (close>prior-20-high) in an up-regime; trail exit
  AMBUSH_THEN_RIDE 埋伏(0.3仓 when EMA up) -> 顺趋势加码(1.0 on confirm)   [the user's exact sequence]
  AMBUSH_TREND     埋伏-提前顺势: full long as soon as EMA slope up (no confirmation wait)
  AMBUSH_MR        埋伏-抄支撑: long at range-support (pos<0.25), exit at resistance/stop  [counter-trend]
  FLIP             换方向: long/short flip on EMA-slope sign (perp-style, captures both sides)
  RIDE_VT          顺趋势 + 精确仓位: RIDE sized by vol-target (size = clip(median_vol/realized_vol, .2, 2))

Forward/OOS check: P1/P2/P3 = chronological thirds. 'robust+' = positive in ALL three periods (real edge,
not one lucky regime). Reference = buy&hold.
"""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from quant import market_store as ms

COST = 8e-4
TFS = {"5m": 288 * 365.25, "30m": 48 * 365.25, "4h": 6 * 365.25}
VARIANTS = ["RIDE", "AMBUSH_THEN_RIDE", "AMBUSH_TREND", "AMBUSH_MR", "FLIP", "RIDE_VT"]
LABEL = {"RIDE": "RIDE 顺趋势确认", "AMBUSH_THEN_RIDE": "埋伏->顺趋势加码", "AMBUSH_TREND": "埋伏 提前顺势",
         "AMBUSH_MR": "埋伏 抄支撑", "FLIP": "FLIP 换方向", "RIDE_VT": "顺趋势+精确仓位"}


def load(s, tf):
    b = list(ms.bars(s, tf, "2020-08-01", "2026-04-30"))
    return (np.array([x["high"] for x in b], float), np.array([x["low"] for x in b], float),
            np.array([x["close"] for x in b], float))


def feats(h, l, c):
    e = pd.Series(c).ewm(span=50).mean().values
    slope = np.zeros(len(c)); slope[10:] = np.sign(e[10:] - e[:-10])
    hi = pd.Series(h).rolling(20).max().shift(1).values
    lo = pd.Series(l).rolling(20).min().shift(1).values
    pos = (c - lo) / (hi - lo + 1e-12)
    r = np.diff(np.log(c), prepend=0.0); rv = pd.Series(r).rolling(20).std().values
    pc = np.r_[c[0], c[:-1]]; tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = pd.Series(tr).ewm(span=14).mean().values
    return slope, hi, lo, pos, rv, atr


def build_d(v, c, h, l, slope, hi, lo, pos, rv, atr):
    n = len(c); d = np.zeros(n)
    if v == "FLIP":
        d = slope.copy()
    elif v == "AMBUSH_TREND":
        d = (slope > 0).astype(float)
    elif v == "AMBUSH_THEN_RIDE":
        conf = 0
        for i in range(n):
            if slope[i] < 0: conf = 0; d[i] = 0.0
            else:
                if hi[i] == hi[i] and c[i] > hi[i]: conf = 1
                d[i] = 1.0 if conf else 0.3
    elif v == "AMBUSH_MR":
        st = 0
        for i in range(n):
            if st == 0 and pos[i] == pos[i] and pos[i] < 0.25: st = 1
            elif st == 1 and pos[i] == pos[i] and (pos[i] > 0.75 or pos[i] < -0.05): st = 0
            d[i] = st
    elif v in ("RIDE", "RIDE_VT"):
        st = 0; trail = -1.0; tgt = np.nanmedian(rv)
        for i in range(n):
            if st == 0:
                if slope[i] > 0 and hi[i] == hi[i] and c[i] > hi[i]:
                    st = 1; trail = c[i] - 2.5 * atr[i] if atr[i] == atr[i] else -1.0
            else:
                if atr[i] == atr[i]: trail = max(trail, c[i] - 2.5 * atr[i])
                if (trail > 0 and c[i] < trail) or slope[i] < 0: st = 0
            sz = 1.0
            if v == "RIDE_VT" and rv[i] == rv[i] and rv[i] > 0: sz = min(2.0, max(0.2, tgt / rv[i]))
            d[i] = st * sz
    return d


def eqsr(c, d):
    r = c[1:] / c[:-1] - 1
    sr = d[:-1] * r - COST * np.abs(np.diff(d))
    return np.cumprod(1 + np.r_[0, sr]), sr


def metrics(eq, sr, bpy):
    yrs = len(sr) / bpy
    cagr = eq[-1] ** (1 / yrs) - 1 if eq[-1] > 0 else -1.0
    sh = sr.mean() / (sr.std() + 1e-12) * np.sqrt(bpy)
    peak = np.maximum.accumulate(eq); mdd = ((eq - peak) / peak).min()
    return cagr, sh, mdd


def thirds(sr):
    return [np.prod(1 + b) - 1 for b in np.array_split(sr, 3)]


for tf, bpy in TFS.items():
    print(f"\n===== {tf}  (BTC+ETH avg, 8bps; P1/P2/P3 = chronological thirds = forward/OOS) =====")
    bh = []; res = {v: [] for v in VARIANTS}; resp = {v: [] for v in VARIANTS}
    for s in ["BTCUSDT", "ETHUSDT"]:
        h, l, c = load(s, tf); f = feats(h, l, c)
        bh.append(metrics(c / c[0], np.diff(c) / c[:-1], bpy))
        for v in VARIANTS:
            eq, sr = eqsr(c, build_d(v, c, h, l, *f)); res[v].append(metrics(eq, sr, bpy)); resp[v].append(thirds(sr))
    cg, sh, dd = np.mean(bh, 0)
    print(f"  {'buy&hold':<22} CAGR {cg:+6.0%}  Sharpe {sh:5.2f}  MaxDD {dd:6.0%}")
    print(f"  {'variant':<22} {'CAGR':>6} {'Sharpe':>7} {'MaxDD':>7}   {'P1':>5} {'P2':>5} {'P3':>5}")
    for v in VARIANTS:
        cg, sh, dd = np.mean(res[v], 0); p = np.mean(resp[v], 0)
        flag = "  robust+" if all(x > 0 for x in p) else ""
        print(f"  {LABEL[v]:<22} {cg:+6.0%} {sh:7.2f} {dd:7.0%}   {p[0]:+5.0%} {p[1]:+5.0%} {p[2]:+5.0%}{flag}")
