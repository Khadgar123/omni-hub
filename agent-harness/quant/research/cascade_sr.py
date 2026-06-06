"""Cross-scale CASCADE + S/R QUALITY (the user's 区间套 + 70500-box-edge question).

Test 1 (cascade / 升级-降级): a 15m bounce "drags" a larger scale into reversal when it
RECLAIMS that scale's prior swing high (a scale-W structure break / CHoCH). For W = 4h/1d/4d,
how big must the move ALREADY be to break that scale, and is P(large reversal) high once it
does? If breaking the bigger scale needs a bigger move already in hand, the upgrade is
CONFIRMATORY (a 4h reversal = a 15m bounce that kept going), not predictable in advance.

Test 2 (S/R quality, not the number): does a support's STRUCTURAL ROLE predict a bigger bounce?
 - htf_confluence: pivot sits at the 1d 20-bar low (HTF support zone)
 - polarity flip: pivot price = a PRIOR pivot-high (resistance) that price later BROKE (now
   retested from above as support) -- exactly the 70500 = ex-box-top case
 - touches: how many prior swing lows held near this price
Label: 大级别反转 = +5% before a -1% new low within 7d. BTC+ETH 15m, 2020-2026 (probability, no cost).
"""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from quant import market_store as ms

R_BIG = 0.05; R_STOP = 0.01; H_DAYS = 7; THETA = 0.012; BPD = 96; EPS = 0.005
W_SCALES = [(16, "4h"), (96, "1d"), (384, "4d")]


def load(sym, tf, start="2020-08-01"):
    b = list(ms.bars(sym, tf, start, "2026-04-30"))
    h = np.array([x["high"] for x in b], float); l = np.array([x["low"] for x in b], float)
    c = np.array([x["close"] for x in b], float); ts = np.array([int(x["bucket_ts"]) for x in b])
    return h, l, c, ts


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


def build(sym):
    h, l, c, ts = load(sym, "15m"); P = zz(c, THETA); N = len(c); H = int(H_DAYS * BPD)
    H1, L1, C1, T1 = load(sym, "1d"); dlow = pd.Series(L1).rolling(20).min().values
    pp_arr = np.array([p[1] for p in P]); pt_arr = np.array([p[2] for p in P])
    rows = []
    for k in range(4, len(P)):
        pidx, pp, pt, dci = P[k]
        if pt != -1 or dci >= N - 1 or pidx < 400: continue
        di = int(np.searchsorted(T1, ts[pidx], side="right") - 1)
        if di < 25: continue
        maxg = 0.0; end = min(dci + H, N - 1)
        for j in range(dci + 1, end + 1):
            if (l[j] - pp) / pp <= -R_STOP: break
            g = (h[j] - pp) / pp
            if g > maxg: maxg = g
        lab = 1 if maxg >= R_BIG else 0
        rec = {}
        for W, name in W_SCALES:
            ph = h[max(0, pidx - W):pidx].max()
            gain_to = (ph - pp) / pp
            rec[name] = (gain_to, 1 if (gain_to <= 0 or maxg >= gain_to) else 0)
        htf_conf = (pp - dlow[di]) / pp if dlow[di] == dlow[di] else np.nan
        near = np.where(np.abs(pp_arr[:k] - pp) / pp <= EPS)[0]
        touches = int(np.sum(pt_arr[near] == -1))
        polar = 0
        for j in near:
            if pt_arr[j] == 1 and np.any(pp_arr[j + 1:k] > pp_arr[j] * (1 + EPS)):
                polar = 1; break
        rows.append(dict(lab=lab, htf_conf=htf_conf, polar=polar, touches=touches, rec=rec))
    return rows


def Pm(rows): return np.mean([r["lab"] for r in rows]) if rows else float("nan")


def main():
    rows = build("BTCUSDT") + build("ETHUSDT")
    base = Pm(rows); n = len(rows)
    print(f"15m bounce candidates n={n}, base P(大级别反转)={base:.1%}\n")

    print("=== Test 1: cascade 升级 — to BREAK a larger scale, how big must the move ALREADY be? ===")
    for W, name in W_SCALES:
        gains = [r["rec"][name][0] for r in rows if r["rec"][name][0] > 0]
        recd = [r for r in rows if r["rec"][name][1] == 1]
        pl = Pm(recd) if recd else float("nan")
        print(f"  scale {name:>3}: median gain-to-break = {np.median(gains):5.1%}   "
              f"frac that break it = {len(recd)/n:4.0%}   P(large | broke {name}) = {pl:4.0%}")
    print("  -> bigger scale needs a bigger move ALREADY in hand => the 升级 is CONFIRMATORY, not advance-predictable.\n")

    print("=== Test 2: does S/R structural QUALITY (not the number) predict a bigger bounce? ===")
    conf = np.array([r["htf_conf"] for r in rows], float); q = np.nanquantile(conf, 1 / 3)
    at = [r for r in rows if np.isfinite(r["htf_conf"]) and r["htf_conf"] <= q]
    aw = [r for r in rows if np.isfinite(r["htf_conf"]) and r["htf_conf"] > q]
    print(f"  at 1d-20 low (HTF support)     : P={Pm(at):.1%} (n{len(at)})  vs away P={Pm(aw):.1%} (n{len(aw)})")
    pol = [r for r in rows if r["polar"] == 1]; npol = [r for r in rows if r["polar"] == 0]
    print(f"  polarity flip (ex-resistance)  : P={Pm(pol):.1%} (n{len(pol)})  vs none P={Pm(npol):.1%} (n{len(npol)})")
    for t in [0, 1, 2, 3]:
        sub = [r for r in rows if r["touches"] == t]
        if sub: print(f"  prior touches = {t}            : P={Pm(sub):.1%} (n{len(sub)})")
    hi = [r for r in rows if r["polar"] == 1 and np.isfinite(r["htf_conf"]) and r["htf_conf"] <= q]
    print(f"  HIGH quality (flip AND at HTF low): P={Pm(hi):.1%} (n{len(hi)})   [base {base:.1%}]")


if __name__ == "__main__":
    main()
