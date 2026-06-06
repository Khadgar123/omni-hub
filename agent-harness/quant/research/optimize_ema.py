"""Optimize the shipped EMA+vol regime model (regime.py) with the react-not-predict overlay.

V0  = raw regime exposure (long whenever regime.direction==up & not stand_down)  -- yesterday's model
V1  = + vol filter           (don't enter when realized vol is in its top third)  -- the robust enhancement
V2  = + confirm-ladder entry (enter only on a structure break: close > prior 20-bar high)  -- '让行情自证'
V3  = + asymmetric trail exit (ride winners via a trailing ATR stop; cut losers fast)  -- '非对称下注'

The point is to show TO WHAT LEVEL this optimizes the model in RISK-ADJUSTED terms (Sharpe / maxDD /
the small-loss-big-win asymmetry), NOT to manufacture a prediction edge. Long/flat spot, cost 8bps/side.
Then it emits the real-time multi-scale S/R + ladder QUALITY SIGNAL for the latest bar. BTC+ETH 4h.
"""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from quant import market_store as ms, regime

COST = 8e-4
BPY = 365.25 * 6   # 4h bars per year


def load(sym, tf="4h"):
    return list(ms.bars(sym, tf, "2020-08-01", "2026-04-30"))


def atr_np(h, l, c, n=14):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).ewm(span=n).mean().values


def rvol_np(c, n=20):
    r = np.diff(np.log(c), prepend=np.log(c[0]))
    return pd.Series(r).rolling(n).std().values


def bt(bars, reg, *, vol_filter=False, ladder=False, trail=False):
    c = np.array([b["close"] for b in bars]); h = np.array([b["high"] for b in bars]); l = np.array([b["low"] for b in bars])
    n = len(c); atr = atr_np(h, l, c); rv = rvol_np(c)
    volthr = pd.Series(rv).rolling(90).quantile(0.66).values
    swinghi = pd.Series(h).rolling(20).max().shift(1).values        # prior 20-bar high (causal)
    eq = np.ones(n); pos = 0.0; trailstop = -1.0; sr = np.zeros(n)
    for i in range(n - 1):
        up = reg[i]["direction"] == "up" and not reg[i]["stand_down"]
        dn = reg[i]["direction"] == "down" or reg[i]["stand_down"]
        want = pos
        if pos == 0:
            ok = up
            if vol_filter and rv[i] == rv[i] and volthr[i] == volthr[i] and rv[i] > volthr[i]: ok = False
            if ladder and not (swinghi[i] == swinghi[i] and c[i] > swinghi[i]): ok = False
            if ok:
                want = 1.0
                trailstop = c[i] - 2.0 * atr[i] if (trail and atr[i] == atr[i]) else -1.0
        else:
            if trail and atr[i] == atr[i]:
                trailstop = max(trailstop, c[i] - 2.5 * atr[i])
                if c[i] <= trailstop: want = 0.0
            if dn: want = 0.0
        cost_i = COST * abs(want - pos) if want != pos else 0.0
        pos = want
        r = c[i + 1] / c[i] - 1
        sr[i + 1] = pos * r - cost_i
        eq[i + 1] = eq[i] * (1 + sr[i + 1])
    return eq, sr


def metrics(eq, sr, ts):
    ret = sr[1:]
    yrs = len(ret) / BPY
    cagr = (eq[-1] / eq[0]) ** (1 / yrs) - 1 if eq[-1] > 0 else -1
    sharpe = ret.mean() / (ret.std() + 1e-12) * np.sqrt(BPY)
    peak = np.maximum.accumulate(eq); maxdd = ((eq - peak) / peak).min()
    tim = np.mean(sr != 0)
    return cagr, sharpe, maxdd, eq[-1], tim


def main():
    print("=== Optimization ladder: EMA+vol regime + react-not-predict overlay (BTC+ETH 4h, 8bps) ===")
    print(f"  {'variant':<26} {'CAGR':>7} {'Sharpe':>7} {'MaxDD':>7} {'x':>7} {'%inMkt':>7}")
    variants = [("V0 raw EMA+vol regime", {}),
                ("V1 + vol filter", dict(vol_filter=True)),
                ("V2 + confirm-ladder entry", dict(vol_filter=True, ladder=True)),
                ("V3 + asymmetric trail exit", dict(vol_filter=True, ladder=True, trail=True))]
    agg = {name: [] for name, _ in variants}
    bh = []
    for sym in ["BTCUSDT", "ETHUSDT"]:
        bars = load(sym); reg = regime.classify_series(bars)
        ts = np.array([int(b["bucket_ts"]) for b in bars]); c = np.array([b["close"] for b in bars])
        bh_eq = c / c[0]; peak = np.maximum.accumulate(bh_eq)
        bh.append((sym, (bh_eq[-1]) ** (1 / (len(c) / BPY)) - 1,
                   (np.diff(c) / c[:-1]).mean() / (np.diff(c) / c[:-1]).std() * np.sqrt(BPY),
                   ((bh_eq - peak) / peak).min(), bh_eq[-1]))
        for name, kw in variants:
            eq, sr = bt(bars, reg, **kw); agg[name].append(metrics(eq, sr, ts))
    for name, _ in variants:
        m = np.array(agg[name]); cagr, sh, dd, x, tim = m.mean(0)
        print(f"  {name:<26} {cagr:6.1%} {sh:7.2f} {dd:6.1%} {x:6.1f}x {tim:6.0%}")
    print("  -- reference --")
    for sym, cagr, sh, dd, x in bh:
        print(f"  buy&hold {sym:<17} {cagr:6.1%} {sh:7.2f} {dd:6.1%} {x:6.1f}x")

    # ---------- real-time multi-scale S/R + ladder QUALITY SIGNAL (latest bar) ----------
    print("\n=== Real-time quality signal (latest stored 4h bar; live would use --live) ===")
    for sym in ["BTCUSDT", "ETHUSDT"]:
        bars = load(sym); reg = regime.classify_series(bars)
        c = np.array([b["close"] for b in bars]); h = np.array([b["high"] for b in bars]); l = np.array([b["low"] for b in bars])
        i = len(c) - 1; px = c[i]; r = reg[i]
        # multi-scale S/R = Donchian edges at 4h-bar windows (20≈3d, 120≈20d, 480≈80d)
        scales = [(20, "4h/3d"), (120, "1d/20d"), (480, "4d/80d")]
        sup = {nm: l[max(0, i - W):i].min() for W, nm in scales}
        res = {nm: h[max(0, i - W):i].max() for W, nm in scales}
        broke = [nm for W, nm in scales if px > h[max(0, i - W):i].max() * 0.999]   # reclaimed that scale's high
        rsup = max((nm for W, nm in scales if (px - sup[nm]) / px <= 0.02), key=lambda nm: dict(scales)[nm] if False else 1, default=None)
        # nearest support distance per scale
        print(f"  {sym}: px={px:,.0f}  regime={r['label']}({r['direction']}) standdown={r['stand_down']}")
        for W, nm in scales:
            print(f"     {nm:<8} support={sup[nm]:,.0f} ({(px-sup[nm])/px:+.1%})   "
                  f"resist={res[nm]:,.0f} ({(res[nm]-px)/px:+.1%})")
        print(f"     ladder: reclaimed scales = {broke or 'none'}  -> conviction notch = {len(broke)}/3")


if __name__ == "__main__":
    main()
