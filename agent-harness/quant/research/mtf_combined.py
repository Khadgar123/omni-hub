"""Fix the false-range problem: a LTF 'range' inside a still-alive HTF trend is a PULLBACK,
not a true range. Combine multi-level EMA trend. Two parts:

PART 1 — 级别关联: P(LTF direction | HTF direction) for adjacent pairs (5m|30m, 30m|4h, 4h|1d).
         Does the higher level bias/contain the lower? (causal-agnostic, descriptive co-occurrence.)
PART 2 — combined operation on 4h, GATED by the causal 1d trend (prior closed day):
         RIDE_4h        single-TF trend-follow (ignores 1d)            [baseline]
         MTF_RIDE       trend-follow but only WITH the 1d trend (long only if 1d up; short only if 1d down; flat if 1d range)
         MTF_PULLBACK   treat LTF range as HTF pullback: 1d up & 4h dip(pos<.3)->long ; 1d down & 4h rip(pos>.7)->short
         buy&hold
         metrics CAGR/Sharpe/MaxDD/Skew + P1/P2/P3 (forward robustness). perp 多空, 8bps.
"""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from quant import market_store as ms
COST = 8e-4; BPY4H = 6 * 365.25


def load(s, tf):
    b = list(ms.bars(s, tf, "2020-08-01", "2026-04-30"))
    return (np.array([x["high"] for x in b], float), np.array([x["low"] for x in b], float),
            np.array([x["close"] for x in b], float), np.array([int(x["bucket_ts"]) for x in b]))


def direction(h, l, c):
    e = pd.Series(c).ewm(span=50).mean().values; sl = np.zeros(len(c)); sl[10:] = e[10:] - e[:-10]
    pc = np.r_[c[0], c[:-1]]; tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc))); atr = pd.Series(tr).ewm(span=14).mean().values
    spa = sl / (atr + 1e-9); return np.where(spa > 0.05, 1, np.where(spa < -0.05, -1, 0)), atr


# ---------------- PART 1: cross-level linkage ----------------
print("=== PART 1  级别关联: P(下级方向 | 上级方向)  (BTC+ETH 合并) ===")
pairs = [("5m", "30m"), ("30m", "4h"), ("4h", "1d")]
for ltf, htf in pairs:
    cnt = {hd: {-1: 0, 0: 0, 1: 0} for hd in (-1, 0, 1)}
    for s in ["BTCUSDT", "ETHUSDT"]:
        hl, ll, cl, tl = load(s, ltf); dl, _ = direction(hl, ll, cl)
        hh, lh, ch, th = load(s, htf); dh, _ = direction(hh, lh, ch)
        idx = np.searchsorted(th, tl, side="right") - 1; idx = np.clip(idx, 0, len(dh) - 1)
        hd = dh[idx]
        for a, b in zip(hd, dl): cnt[a][b] += 1
    print(f"  [{ltf} 看 {htf}]")
    for hd, nm in [(1, "上级↑"), (-1, "上级↓"), (0, "上级震荡")]:
        tot = sum(cnt[hd].values()) or 1
        print(f"     {nm:<7}: 下级↑ {100*cnt[hd][1]/tot:2.0f}%  震荡 {100*cnt[hd][0]/tot:2.0f}%  下级↓ {100*cnt[hd][-1]/tot:2.0f}%   (n={tot})")


# ---------------- PART 2: combined 4h operation gated by causal 1d trend ----------------
def feats4h(h, l, c):
    d, atr = direction(h, l, c)
    hi = pd.Series(h).rolling(20).max().shift(1).values; lo = pd.Series(l).rolling(20).min().shift(1).values
    pr = (c - lo) / (hi - lo + 1e-12)
    return d, atr, hi, lo, pr


def equity(c, dpos):
    r = c[1:] / c[:-1] - 1; sr = dpos[:-1] * r - COST * np.abs(np.diff(dpos)); eq = np.cumprod(1 + np.r_[0, sr])
    return eq, sr


def stat(c, dpos):
    eq, sr = equity(c, dpos); yrs = len(sr) / BPY4H
    cg = eq[-1] ** (1 / yrs) - 1 if eq[-1] > 0 else -1.0
    sh = sr.mean() / (sr.std() + 1e-12) * np.sqrt(BPY4H)
    peak = np.maximum.accumulate(eq); mdd = ((eq - peak) / peak).min()
    a = sr[sr != 0]; sk = ((a - a.mean()) ** 3).mean() / (a.std() ** 3 + 1e-12) if len(a) > 10 else 0
    p = [np.prod(1 + b) - 1 for b in np.array_split(sr, 3)]
    return cg, sh, mdd, sk, p


def build(v, c, h, l, d4, atr, hi, lo, pr, bias1d):
    n = len(c); dpos = np.zeros(n); pos = 0.0; trail = 0.0
    for i in range(n):
        if v == "RIDE_4h":
            up = d4[i] == 1; dn = d4[i] == -1
            if pos <= 0 and up and hi[i] == hi[i] and c[i] > hi[i]: pos = 1.0; trail = c[i] - 2.5 * atr[i]
            elif pos >= 0 and dn and lo[i] == lo[i] and c[i] < lo[i]: pos = -1.0; trail = c[i] + 2.5 * atr[i]
            if pos > 0: trail = max(trail, c[i] - 2.5 * atr[i]); pos = 0.0 if (c[i] < trail or dn) else pos
            elif pos < 0: trail = min(trail, c[i] + 2.5 * atr[i]); pos = 0.0 if (c[i] > trail or up) else pos
        elif v == "MTF_RIDE":
            b = bias1d[i]
            if b == 1:
                if pos <= 0 and hi[i] == hi[i] and c[i] > hi[i]: pos = 1.0; trail = c[i] - 2.5 * atr[i]
                if pos > 0: trail = max(trail, c[i] - 2.5 * atr[i]); pos = 0.0 if c[i] < trail else pos
                if pos < 0: pos = 0.0
            elif b == -1:
                if pos >= 0 and lo[i] == lo[i] and c[i] < lo[i]: pos = -1.0; trail = c[i] + 2.5 * atr[i]
                if pos < 0: trail = min(trail, c[i] + 2.5 * atr[i]); pos = 0.0 if c[i] > trail else pos
                if pos > 0: pos = 0.0
            else: pos = 0.0
        elif v == "MTF_PULLBACK":
            b = bias1d[i]
            if pos == 0:
                if b == 1 and pr[i] < 0.3: pos = 1.0
                elif b == -1 and pr[i] > 0.7: pos = -1.0
            else:
                if pos > 0 and (pr[i] > 0.6 or b != 1): pos = 0.0
                if pos < 0 and (pr[i] < 0.4 or b != -1): pos = 0.0
        dpos[i] = pos
    return dpos


print("\n=== PART 2  4h 操作,用 causal 1d EMA趋势 组合定向 (BTC+ETH avg) ===")
VAR = ["RIDE_4h", "MTF_RIDE", "MTF_PULLBACK"]
agg = {v: [] for v in VAR}; bh = []
for s in ["BTCUSDT", "ETHUSDT"]:
    h, l, c, t4 = load(s, "4h"); d4, atr, hi, lo, pr = feats4h(h, l, c)
    H1, L1, C1, T1 = load(s, "1d"); d1, _ = direction(H1, L1, C1)
    idx = np.searchsorted(T1, t4, side="right") - 1; idx = np.clip(idx - 1, 0, len(d1) - 1)   # prior CLOSED day (causal)
    bias1d = d1[idx]
    bh.append(stat(c, np.ones(len(c))))
    for v in VAR: agg[v].append(stat(c, build(v, c, h, l, d4, atr, hi, lo, pr, bias1d)))
cg, sh, mdd, sk, p = np.mean([x[:4] for x in bh], 0).tolist() + [np.mean([x[4] for x in bh], 0)]
print(f"  {'操作':<16} {'CAGR':>7} {'Sharpe':>7} {'MaxDD':>7} {'Skew':>6}   P1    P2    P3")
print(f"  {'buy&hold':<16} {cg:+7.0%} {sh:7.2f} {mdd:7.0%} {sk:+6.2f}")
for v in VAR:
    m = [np.mean([x[k] for x in agg[v]], 0) for k in range(4)]; p = np.mean([x[4] for x in agg[v]], 0)
    flag = "  robust+" if all(z > 0 for z in p) else ""
    print(f"  {v:<16} {m[0]:+7.0%} {m[1]:7.2f} {m[2]:7.0%} {m[3]:+6.2f}   {p[0]:+5.0%} {p[1]:+5.0%} {p[2]:+5.0%}{flag}")
