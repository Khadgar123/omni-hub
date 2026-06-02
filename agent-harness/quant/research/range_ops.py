"""震荡行情最好的操作? Test, per TF, inside identified ranges:
  MR_BOTH       双向均值回归: 在区间底(pos<.2)做多、顶(pos>.8)做空,回到中轴止盈,破区间止损
  MR_WITHTREND  顺大势那一边: 下跌大势只空顶 / 上涨大势只抄底
  MR_COUNTER    逆大势那一边: 下跌大势抄底 / 上涨大势空顶
  BREAKOUT      唐奇安突破: 破20根高做多/破低做空 + 移动止损(跟随区间resolution)
  RANGE_THEN_RIDE 震荡里双向MR + 趋势里顺势(为之后趋势定位)
Report CAGR/Sharpe/MaxDD + SKEW (MR 负偏=小赢大亏=危险; 趋势/突破 正偏). 大势= EMA200 slope. perp 多空. 8bps.
"""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from quant import market_store as ms
COST = 8e-4
TFS = {"5m": 288 * 365.25, "30m": 48 * 365.25, "4h": 6 * 365.25}
VARIANTS = ["MR_BOTH", "MR_WITHTREND", "MR_COUNTER", "BREAKOUT", "RANGE_THEN_RIDE"]


def load(s, tf):
    b = list(ms.bars(s, tf, "2020-08-01", "2026-04-30"))
    return (np.array([x["high"] for x in b], float), np.array([x["low"] for x in b], float), np.array([x["close"] for x in b], float))


def feats(h, l, c):
    e = pd.Series(c).ewm(span=50).mean().values; sl = np.zeros(len(c)); sl[10:] = e[10:] - e[:-10]
    pc = np.r_[c[0], c[:-1]]; tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc))); atr = pd.Series(tr).ewm(span=14).mean().values
    spa = sl / (atr + 1e-9); reg = np.where(spa > 0.05, 1, np.where(spa < -0.05, -1, 0))   # up/down/flat
    e2 = pd.Series(c).ewm(span=200).mean().values; big = np.zeros(len(c)); big[20:] = np.sign(e2[20:] - e2[:-20])
    hi = pd.Series(h).rolling(20).max().shift(1).values; lo = pd.Series(l).rolling(20).min().shift(1).values
    pr = (c - lo) / (hi - lo + 1e-12)
    return reg, big, hi, lo, pr, atr


def build(v, c, h, l, reg, big, hi, lo, pr, atr):
    n = len(c); d = np.zeros(n); pos = 0.0; trail = 0.0
    for i in range(n):
        flat = reg[i] == 0
        if v in ("MR_BOTH", "MR_WITHTREND", "MR_COUNTER", "RANGE_THEN_RIDE"):
            if flat:
                if pos == 0:
                    lo_ok = pr[i] < 0.2; hi_ok = pr[i] > 0.8
                    if v == "MR_WITHTREND": lo_ok &= big[i] > 0; hi_ok &= big[i] < 0
                    if v == "MR_COUNTER":   lo_ok &= big[i] < 0; hi_ok &= big[i] > 0
                    if lo_ok: pos = 1.0
                    elif hi_ok: pos = -1.0
                else:
                    if pos > 0 and (pr[i] >= 0.5 or pr[i] < -0.1): pos = 0.0   # 止盈中轴 / 破下沿止损
                    if pos < 0 and (pr[i] <= 0.5 or pr[i] > 1.1): pos = 0.0
            else:
                pos = float(reg[i]) if v == "RANGE_THEN_RIDE" else 0.0          # 趋势里:顺势 / 或离场
        elif v == "BREAKOUT":
            if pos <= 0 and hi[i] == hi[i] and c[i] > hi[i]: pos = 1.0; trail = c[i] - 2.5 * atr[i]
            elif pos >= 0 and lo[i] == lo[i] and c[i] < lo[i]: pos = -1.0; trail = c[i] + 2.5 * atr[i]
            if pos > 0:
                trail = max(trail, c[i] - 2.5 * atr[i]);  pos = 0.0 if c[i] < trail else pos
            elif pos < 0:
                trail = min(trail, c[i] + 2.5 * atr[i]);   pos = 0.0 if c[i] > trail else pos
        d[i] = pos
    return d


def stats(c, d, bpy):
    r = c[1:] / c[:-1] - 1; sr = d[:-1] * r - COST * np.abs(np.diff(d)); eq = np.cumprod(1 + np.r_[0, sr])
    yrs = len(sr) / bpy; cagr = eq[-1] ** (1 / yrs) - 1 if eq[-1] > 0 else -1.0
    sh = sr.mean() / (sr.std() + 1e-12) * np.sqrt(bpy)
    peak = np.maximum.accumulate(eq); mdd = ((eq - peak) / peak).min()
    a = sr[sr != 0]; sk = ((a - a.mean()) ** 3).mean() / (a.std() ** 3 + 1e-12) if len(a) > 10 else 0
    return cagr, sh, mdd, sk


for tf, bpy in TFS.items():
    print(f"\n===== {tf}  (BTC+ETH avg, perp 多空, 8bps) =====")
    res = {v: [] for v in VARIANTS}
    for s in ["BTCUSDT", "ETHUSDT"]:
        h, l, c = load(s, tf); f = feats(h, l, c)
        for v in VARIANTS: res[v].append(stats(c, build(v, c, h, l, *f), bpy))
    print(f"  {'操作':<26} {'CAGR':>7} {'Sharpe':>7} {'MaxDD':>7} {'Skew':>6}")
    for v in VARIANTS:
        cg, sh, mdd, sk = np.mean(res[v], 0)
        print(f"  {v:<26} {cg:+7.0%} {sh:7.2f} {mdd:7.0%} {sk:+6.2f}")
