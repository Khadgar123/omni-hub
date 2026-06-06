"""小级别反弹 -> 大级别反转? — conditional / streaming probability + scale-invariance.

At each small-TF swing low (a '小级别反弹' candidate), label whether it ESCALATES into a
LARGE reversal (price reaches +R_BIG before breaking R_STOP below the pivot low, within a
multi-day horizon). Then ask the user's exact question: does ANY causal condition
(structure quality, HTF 1d regime, support proximity, oversold, drop depth) SHARPLY move
P(large reversal) above the base rate? Plus (B) a streaming calibration: P(large | the
bounce has already risen g%), and (C) a scale-invariance check across 5m/15m/1h.
This is a PROBABILITY question (not P&L), so no cost is charged. BTC+ETH 2020-2026.
"""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from quant import market_store as ms

R_BIG = 0.05      # +5% from the pivot low = escalated to a LARGE reversal
R_STOP = 0.01     # -1% below the pivot low = failed bounce / new low
HORIZON_DAYS = 7
THETA = {"5m": 0.008, "15m": 0.012, "1h": 0.018}
BPD = {"5m": 288, "15m": 96, "1h": 24}


def load(sym, tf, start="2020-08-01"):
    b = list(ms.bars(sym, tf, start, "2026-04-30"))
    h = np.array([x["high"] for x in b], float); l = np.array([x["low"] for x in b], float)
    c = np.array([x["close"] for x in b], float); v = np.array([x.get("volume", 0.) for x in b], float)
    ts = np.array([int(x["bucket_ts"]) for x in b])
    return h, l, c, v, ts


def macd_h(c):
    ef = pd.Series(c).ewm(span=12).mean(); es = pd.Series(c).ewm(span=26).mean()
    dif = ef - es; dea = dif.ewm(span=9).mean(); return (dif - dea).values


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0]); up = np.where(d > 0, d, 0.); dn = np.where(d < 0, -d, 0.)
    ru = pd.Series(up).ewm(alpha=1 / n).mean().values; rd = pd.Series(dn).ewm(alpha=1 / n).mean().values
    return 100 - 100 / (1 + ru / (rd + 1e-12))


def slope_sign(c, n=50, lb=10):
    e = pd.Series(c).ewm(span=n).mean().values; s = np.zeros(len(c))
    s[lb:] = np.sign(e[lb:] - e[:-lb]); return s


def zz(c, theta):
    piv = []; trend = 1; ri = 0; rp = c[0]
    for i in range(1, len(c)):
        if trend == 1:
            if c[i] > rp: ri, rp = i, c[i]
            elif (rp - c[i]) / rp >= theta: piv.append((ri, rp, 1, i)); trend = -1; ri, rp = i, c[i]
        else:
            if c[i] < rp: ri, rp = i, c[i]
            elif (c[i] - rp) / rp >= theta: piv.append((ri, rp, -1, i)); trend = 1; ri, rp = i, c[i]
    return piv


GRID = [0.005, 0.01, 0.02, 0.03, 0.04]


def build(sym, tf):
    theta = THETA[tf]; H = int(HORIZON_DAYS * BPD[tf])
    h, l, c, v, ts = load(sym, tf); hist = macd_h(c); P = zz(c, theta); N = len(c)
    H1, L1, C1, V1, T1 = load(sym, "1d"); rsi1 = rsi(C1); sl1 = slope_sign(C1)
    dlow = pd.Series(L1).rolling(20).min().values
    rows = []
    for k in range(3, len(P)):
        pidx, pp, pt, dci = P[k]
        if pt != -1 or dci >= N - 1: continue            # bullish bounce candidates only
        di = int(np.searchsorted(T1, ts[pidx], side="right") - 1)
        if di < 25: continue
        ta = hist[P[k - 1][0]:pidx + 1].sum(); pa = hist[P[k - 3][0]:P[k - 2][0] + 1].sum()
        th = hist[pidx:dci + 1].sum()
        f = dict(
            drop_in=(P[k - 1][1] - pp) / P[k - 1][1],          # depth of the down-leg into the pivot
            q_mom=abs(th) / (abs(ta) + 1e-9),                  # thrust momentum vs trend-leg momentum
            beichi=abs(ta) / (abs(pa) + 1e-9),                 # >1 = trend leg weaker than prior (背驰)
            htf_up=1.0 if sl1[di] > 0 else 0.0,                # 1d EMA50 slope up?
            htf_rsi=rsi1[di],                                  # 1d RSI (oversold low)
            at_support=(pp - dlow[di]) / pp if dlow[di] == dlow[di] else np.nan,  # 0 = at 20-day low
        )
        # path from the pivot low: max gain reached BEFORE a -R_STOP new low (within H)
        maxg = 0.0; end = min(dci + H, N - 1)
        for j in range(dci + 1, end + 1):
            if (l[j] - pp) / pp <= -R_STOP: break
            g = (h[j] - pp) / pp
            if g > maxg: maxg = g
        f["_lab"] = 1 if maxg >= R_BIG else 0
        f["_maxg"] = maxg
        rows.append(f)
    return rows


def P(rows): return np.mean([r["_lab"] for r in rows]) if rows else float("nan")


def auc(score, lab):
    s = np.asarray(score, float); y = np.asarray(lab, int); m = np.isfinite(s); s, y = s[m], y[m]
    n1 = int(y.sum()); n0 = len(y) - n1
    if n1 == 0 or n0 == 0: return float("nan")
    o = np.argsort(s, kind="mergesort"); r = np.empty(len(s)); r[o] = np.arange(1, len(s) + 1)
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


FEATS = ["drop_in", "q_mom", "beichi", "htf_up", "htf_rsi", "at_support"]


def main():
    print(f"label: 大级别反转 = from the pivot low, +{R_BIG:.0%} reached BEFORE a -{R_STOP:.0%} new low, "
          f"within {HORIZON_DAYS}d. (probability question; no cost)\n")
    allrows = {}
    for tf in ["5m", "15m", "1h"]:
        rows = build("BTCUSDT", tf) + build("ETHUSDT", tf)
        allrows[tf] = rows

    # ---- Part C: scale-invariance (base rate similar across scales?) ----
    print("=== (C) scale-invariance: base rate of 小反弹->大反转 per scale ===")
    for tf in ["5m", "15m", "1h"]:
        print(f"  {tf:>4}: base P(large reversal)={P(allrows[tf]):.1%}  (n={len(allrows[tf])})")

    # ---- Part A: does ANY causal condition SHARPLY move P? (15m) ----
    rows = allrows["15m"]; base = P(rows)
    print(f"\n=== (A) conditional lift @15m  (base P={base:.1%}, n={len(rows)}) ===")
    print(f"  {'feature':<11} {'AUC':>5} {'P|bottom-3rd':>13} {'P|top-3rd':>11} {'max|lift|':>10}")
    for fac in FEATS:
        sc = np.array([r[fac] for r in rows], float); lab = [r["_lab"] for r in rows]
        a = auc(sc, lab)
        if fac == "htf_up":
            lo = [r["_lab"] for r in rows if r[fac] == 0]; hi = [r["_lab"] for r in rows if r[fac] == 1]
        else:
            qlo, qhi = np.nanquantile(sc, 1 / 3), np.nanquantile(sc, 2 / 3)
            lo = [r["_lab"] for r in rows if np.isfinite(r[fac]) and r[fac] <= qlo]
            hi = [r["_lab"] for r in rows if np.isfinite(r[fac]) and r[fac] >= qhi]
        plo, phi = np.mean(lo), np.mean(hi); lift = max(abs(plo - base), abs(phi - base))
        flag = "  <==" if lift > 0.15 else ""
        print(f"  {fac:<11} {a:5.3f} {plo:12.1%} {phi:10.1%} {lift:9.1%}{flag}")

    # best-case stack: 1d-up + oversold + at-support
    sc_sup = np.array([r["at_support"] for r in rows], float); sc_rsi = np.array([r["htf_rsi"] for r in rows], float)
    sup_thr = np.nanquantile(sc_sup, 1 / 3); rsi_thr = np.nanquantile(sc_rsi, 1 / 3)
    stack = [r for r in rows if r["htf_up"] == 1 and np.isfinite(r["at_support"]) and r["at_support"] <= sup_thr]
    stack2 = [r for r in rows if r["htf_up"] == 1 and r["htf_rsi"] <= rsi_thr and np.isfinite(r["at_support"]) and r["at_support"] <= sup_thr]
    print(f"  STACK 1d-up & at-support           : P={P(stack):.1%} (n={len(stack)})")
    print(f"  STACK 1d-up & oversold & at-support : P={P(stack2):.1%} (n={len(stack2)})  <- best 'bottoming' setup")

    # ---- Part B: streaming calibration — P(large | already risen g%) (15m) ----
    print(f"\n=== (B) streaming: P(大反转 | the bounce has ALREADY risen g% before a -1% low) @15m ===")
    for g in GRID:
        reached = [r for r in rows if r["_maxg"] >= g]
        pg = np.mean([1 if r["_maxg"] >= R_BIG else 0 for r in reached]) if reached else float("nan")
        print(f"  reached +{g:>4.1%}: P(eventually +{R_BIG:.0%})={pg:5.1%}  (n={len(reached)})")
    print(f"  -> if this only crosses 50% once g is already near +{R_BIG:.0%}, the signal is CONFIRMATORY, not predictive.")


if __name__ == "__main__":
    main()
