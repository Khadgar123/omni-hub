"""Reversal-zone leg-quality test.

Frame (user's): [trend A] -> [reversal structure] -> [trend B]. At each causal swing
pivot we sit in the "reversal structure" and decompose the EMERGING THRUST quality
(pivot -> its confirm bar, i.e. the first counter-move) vs the DYING TREND-LEG quality
(prior pivot -> this pivot), plus 背驰 (this trend leg vs the one before it). Question:
does the up-vs-down quality ASYMMETRY predict a TRUE reversal (full retrace of the last
trend leg before breaking its extreme) out-of-sample, and does it BEAT buying every
swing? Causal features; honest net-P&L (round-trip cost 8 bps). BTC+ETH, 5m/15m/1h/4h.
"""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from quant import market_store as ms

COST = 8e-4
THETA = {"1m": 0.005, "5m": 0.008, "15m": 0.012, "1h": 0.018, "4h": 0.03}
H_BY  = {"1m": 480, "5m": 288, "15m": 192, "1h": 120, "4h": 120}
START = {"1m": "2024-01-01"}  # 1m: 2.3y slice (3M bars otherwise); others full
FACTORS = ["q_slope", "q_er", "q_vol", "q_body", "q_mom", "beichi", "retr"]


def load(sym, tf):
    b = list(ms.bars(sym, tf, START.get(tf, "2020-08-01"), "2026-04-30"))
    o = np.array([x["open"] for x in b], float); h = np.array([x["high"] for x in b], float)
    l = np.array([x["low"] for x in b], float);  c = np.array([x["close"] for x in b], float)
    v = np.array([x.get("volume", 0.0) for x in b], float)
    ts = np.array([int(x["bucket_ts"]) for x in b])
    yr = pd.to_datetime(ts, unit="us").year.values
    return o, h, l, c, v, yr


def macd_hist(c, f=12, s=26, sig=9):
    ef = pd.Series(c).ewm(span=f).mean(); es = pd.Series(c).ewm(span=s).mean()
    dif = ef - es; dea = dif.ewm(span=sig).mean()
    return (dif - dea).values


def zz(c, theta):
    """Alternating zigzag pivots: (pivot_idx, pivot_price, type[+1 high/-1 low], confirm_idx)."""
    piv = []; trend = 1; ri = 0; rp = c[0]
    for i in range(1, len(c)):
        if trend == 1:
            if c[i] > rp: ri, rp = i, c[i]
            elif (rp - c[i]) / rp >= theta:
                piv.append((ri, rp, 1, i)); trend = -1; ri, rp = i, c[i]
        else:
            if c[i] < rp: ri, rp = i, c[i]
            elif (c[i] - rp) / rp >= theta:
                piv.append((ri, rp, -1, i)); trend = 1; ri, rp = i, c[i]
    return piv


def lq(a, b, o, h, l, c, v, hist):
    """Quality of the leg/segment [a,b]."""
    if b <= a: return None
    ret = abs(c[b] - c[a]) / c[a]; bars = b - a
    moves = np.abs(np.diff(c[a:b + 1]))
    er = abs(c[b] - c[a]) / (moves.sum() + 1e-12)            # Kaufman efficiency (cleanliness)
    rng = h[a:b + 1] - l[a:b + 1]
    body = np.mean(np.abs(o[a:b + 1] - c[a:b + 1]) / (rng + 1e-12))
    return dict(ret=ret, slope=ret / bars, er=er, vbar=v[a:b + 1].mean(),
                area=hist[a:b + 1].sum(), body=body)


def auc(score, lab):
    score = np.asarray(score, float); lab = np.asarray(lab, int)
    m = np.isfinite(score); score, lab = score[m], lab[m]
    n1 = int(lab.sum()); n0 = len(lab) - n1
    if n1 == 0 or n0 == 0: return float("nan")
    order = np.argsort(score, kind="mergesort"); ranks = np.empty(len(score)); ranks[order] = np.arange(1, len(score) + 1)
    return (ranks[lab == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def build(sym, tf):
    theta = THETA[tf]; H = H_BY[tf]
    o, h, l, c, v, yr = load(sym, tf); hist = macd_hist(c); P = zz(c, theta); N = len(c)
    rows = []
    for k in range(3, len(P)):
        pidx, pp, pt, dci = P[k]
        trend = lq(P[k - 1][0], P[k][0], o, h, l, c, v, hist)     # dying trend leg
        prior = lq(P[k - 3][0], P[k - 2][0], o, h, l, c, v, hist) # leg before it (背驰 ref)
        thrust = lq(P[k][0], dci, o, h, l, c, v, hist)            # emerging counter-thrust (causal)
        if not (trend and prior and thrust) or dci >= N - 1: continue
        eps = 1e-9
        f = dict(
            q_slope=thrust["slope"] / (trend["slope"] + eps),
            q_er=thrust["er"] / (trend["er"] + eps),
            q_vol=thrust["vbar"] / (trend["vbar"] + eps),
            q_body=thrust["body"] / (trend["body"] + eps),
            q_mom=abs(thrust["area"]) / (abs(trend["area"]) + eps),
            beichi=abs(prior["area"]) / (abs(trend["area"]) + eps),  # >1 => trend leg weaker than prior (背驰)
            retr=thrust["ret"] / (trend["ret"] + eps),
        )
        d = 1 if pt == -1 else -1                # low pivot => bullish reversal; high => bearish
        entry = c[dci]; target = P[k - 1][1]; stop = pp   # full retrace to prior pivot vs break the extreme
        lab, pnl, end = 0, None, min(dci + H, N - 1)
        for j in range(dci + 1, end + 1):
            if d > 0:
                if l[j] <= stop: lab, pnl = 0, d * (stop - entry) / entry - COST; break
                if h[j] >= target: lab, pnl = 1, d * (target - entry) / entry - COST; break
            else:
                if h[j] >= stop: lab, pnl = 0, d * (stop - entry) / entry - COST; break
                if l[j] <= target: lab, pnl = 1, d * (target - entry) / entry - COST; break
        if pnl is None:                          # timeout: reversal not confirmed in horizon
            lab, pnl = 0, d * (c[end] - entry) / entry - COST
        f["_lab"], f["_pnl"], f["_yr"] = lab, pnl, yr[dci]
        rows.append(f)
    return rows


def net(rows):
    a = np.array([r["_pnl"] for r in rows]); return 100 * a.mean() if len(a) else float("nan")


def main():
    print(f"cost={COST*1e4:.0f}bps | label: reversal = full retrace of last trend leg before breaking its extreme")
    for tf in ["5m", "15m", "1h", "4h"]:
        rows = []
        for sym in ["BTCUSDT", "ETHUSDT"]:
            rows += build(sym, tf)
        n = len(rows); base_rate = np.mean([r["_lab"] for r in rows])
        # chronological IS/OOS split (rows already appended in time order per symbol; sort by year to be safe)
        rows.sort(key=lambda r: r["_yr"])
        cut = int(n * 0.7); IS, OOS = rows[:cut], rows[cut:]
        base_all = net(rows); base_oos = net(OOS)
        print(f"\n===== {tf}  (n={n}, reversal base-rate={base_rate:.0%}, "
              f"theta={THETA[tf]:.1%}) =====")
        print(f"  BASELINE buy-every-swing: net/trade all={base_all:+.3f}%  OOS={base_oos:+.3f}%   "
              f"<- the artifact check (high win-rate can still bleed)")
        print(f"  {'factor':<9} {'AUC_IS':>7} {'AUC_OOS':>8} {'topT_OOS_net':>13} {'vs_base':>9}")
        best = None
        for fac in FACTORS:
            aIS = auc([r[fac] for r in IS], [r["_lab"] for r in IS])
            aOOS = auc([r[fac] for r in OOS], [r["_lab"] for r in OOS])
            sc = np.array([r[fac] for r in OOS], float)
            thr = np.nanquantile(sc, 2 / 3)
            top = [r for r in OOS if np.isfinite(r[fac]) and r[fac] >= thr]
            topnet = net(top); delta = topnet - base_oos
            flag = "  <==" if (aOOS > 0.55 and topnet > 0 and delta > 0) else ""
            print(f"  {fac:<9} {aIS:7.3f} {aOOS:8.3f} {topnet:12.3f}% {delta:+8.3f}%{flag}")
            if best is None or (aOOS > best[1]): best = (fac, aOOS)
        # per-year net of the best factor's top-tercile vs baseline
        fac = best[0]; sc = np.array([r[fac] for r in rows], float); thr = np.nanquantile(sc, 2 / 3)
        line = []
        for y in sorted(set(r["_yr"] for r in rows)):
            yr_rows = [r for r in rows if r["_yr"] == y]
            top = [r for r in yr_rows if np.isfinite(r[fac]) and r[fac] >= thr]
            line.append(f"{y}:{net(top):+.2f}%(n{len(top)})")
        print(f"  best-AUC factor = {fac}; its top-tercile net by year: " + " ".join(line))


if __name__ == "__main__":
    main()
