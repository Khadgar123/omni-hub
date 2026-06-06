# quant/research — empirical studies (the evidence behind "react-not-predict")

Reproducible analyses run against the market store (`~/quant/market`, via `quant.*`).
**Not unit tests** — they do not run in CI. Each documents a *question → method → finding*
that drove a strategy decision. Run inside the quant env:

```bash
python research/<name>.py        # reads ~/quant/market (BTC+ETH 1m–4h); some pull Binance fapi live
```

## The thesis these studies establish

Price-only **direction** prediction caps at ~**0.59 AUC out-of-sample** (the wall). The only
robust edge is **funding carry** (orthogonal, needs no prediction). Trend-following is a
**risk reduction** (≈half the drawdown), **not** alpha — it does **not** beat buy&hold.
Everything else — reversal calls, S/R "quality", range fades, level cascades — is
**confirmatory or negative-skew**, not predictive. → **react, don't predict.**

## Saved studies

| file | question | key finding | conclusion |
|---|---|---|---|
| `reversal_quality.py` | does up-vs-down leg-quality asymmetry predict reversals? | 背驰 OOS-AUC 0.59–0.63 (weak real info); best-factor top-tercile net **negative**; 60%-win baseline still bleeds (geometric-label artifact) | quality decomposition **confirms, doesn't predict**; not tradeable net of 8bps |
| `scale_reversal.py` | small bounce → large reversal? (streaming P) | base 33%(5m)/42%(15m)/58%(1h); no factor shifts it >±5%; "oversold+support" backfires to 35%; +1% bounce = 42% = base | reversal scale undecidable from structure; only a confirmatory ramp |
| `cascade_sr.py` | cross-scale cascade + S/R quality | breaking 4h needs **+2.4% already** / 1d +4.3% / 4d +7% (tautological); S/R quality ±2–3%, polarity-flip ≈0 | "级联升级" is confirmatory; S/R quality ≈ no edge (would need order-flow) |
| `ambush_vs_ride.py` | 埋伏 vs 顺趋势 vs 换方向 (forward-tested P1/P2/P3) | only **4h RIDE is robust+** (Sharpe 0.81, positive every third); 埋伏 decays, 换方向 loses; 5m/30m cost-wall | only confirmed trend-follow (RIDE), at 4h, is robust |
| `range_ops.py` | best operation inside a range? | fade = **negative skew** (loses, blows up on breakout); breakout = positive skew; nothing profitable; 5m wipeout | don't fade ranges; stay neutral + take the breakout (positive skew) |
| `mtf_combined.py` | does multi-level EMA fix the false-range & help? | HTF biases LTF **+25–32pt**; HTF-gated Sharpe 0.39→0.57 but **not robust** (early-bull only); pullback-buy fails | levels are linked (context/risk help), **not** robust profit |
| `optimize_ema.py` | optimize the EMA+vol model + emit a live signal | V2 (vol-filter + confirm-entry) is Pareto: MaxDD −60%→−40%, but **does not beat B&H**; prints the live multi-scale S/R + ladder quality signal | a risk optimization, not alpha; the real-time quality signal lives here |

## Findings established but reproducer not saved (were heredocs; ask to re-add)

- **compression → expansion**: low-vol → next-5d realized vol **×1.48** (vol forecastable); breakout **direction 51.6%** (coin flip). 横盘预告"变盘",不预告方向.
- **快涨/快跌 asymmetry**: 快涨 → mild *continuation* (51% up, **not** 阴跌); 快跌 → more forward vol (leverage effect); context flips it (uptrend +1.6% vs downtrend +0.3%).
- **leg state-machine** {快涨,慢涨,快跌,慢跌}: speed **clusters** (fast→fast +28–47pt all TFs); **顺势腿快、逆势腿慢** (impulse>correction, 1.7–1.9x at 1m, decays to ~1.0x at 1d).
- **阴跌→慢涨 / 暴跌→快涨**: bounce speed tracks the decline's speed (vol-regime persistence); ratio 1.4–1.75x (慢跌后温和回弹) vs 0.6–0.7x (急跌缓弹) — consistent 1m→1d.

## Data + how findings are persisted

- Bars: `~/quant/market` (DuckDB + Parquet, BTC+ETH, 2020-08 → 2026-04). Studies use **1m–4h**;
  the sub-minute bars (`bars_1s/15s/30s`, ~12.7 G) are **unused here**.
- Live reads pull Binance fapi (`fapi.binance.com`).
- To persist a conclusion as a structured, searchable claim, use the **`quant-finding`** skill
  → ClaimLedger (`.omni/claims.jsonl`), not a new database.
