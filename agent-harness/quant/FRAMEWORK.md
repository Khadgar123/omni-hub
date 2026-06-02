# FRAMEWORK.md — crypto edge-audit read (the operating manual for CC / Codex)

> **What this is.** A unified, multi-layer **state read** for a crypto asset (BTC/ETH/any Binance
> perp). It composes the angles that actually carry information into one structured dict + a
> 4-sentence human narrative. It is **react-not-predict**: it reports *state, counterparty, fragility,
> and triggers* — it **does not forecast price** and **never places orders**.
>
> Engine: `quant.framework` (quant venv). omni-hub seam: `omni-hub crypto-read` (stdlib shell-out).

## Run it (any of these)

```bash
# via omni-hub (the module; stdlib, shells out to the quant venv) — the canonical call:
omni-hub crypto-read --symbol BTCUSDT          # or ETHUSDT, SOLUSDT, …
omni-hub crypto-read --symbol ETHUSDT --no-macro

# direct (inside the quant venv) — narrative by default, --metrics / --json for the layers:
python -m quant.framework --symbol BTCUSDT
python -m quant.framework --symbol ETHUSDT --json
```

Ask CC/Codex in natural language ("分析现在的 BTC/ETH", "对手盘是谁", "现在什么状态") → the
`quant-framework` skill triggers this. The daily launchd job (`com.omni-hub.quant`) already emits
the narrative into `.omni/briefs/quant-YYYY-MM-DD.md` (§解读) + `.omni/quant/framework-latest.json`.

## The report — what every read contains (10 sections, bottom-up)

**Principle: tag every signal by NATURE** — `预测型`(几乎没有) / `确认型`(大多数结构) / `上下文` /
`噪声`. **可知** = state / fragility / trigger-levels. **不可知** = direction & timing (the 0.59 wall,
self-similarity, near-martingale). Always end with: *carry is the only robust edge; trend-follow is
risk-reduction not alpha;* + the disclaimer.

1. **逐级别微观结构** (1m·5m·15m·30m·1h·4h·1d, one line each): regime (range/trend ± direction)
   `确认`; volatility bucket + **compression vs expansion** (squeeze → vol×1.48) + CUSUM stand_down
   (`vol扩张=预测;方向=不可知`); ADX & slope/ATR `确认`; pos-in-range `上下文`; **leg character**
   (快涨/慢涨/快跌/慢跌 = impulse-vs-correction) `确认`.
2. **各级别 S/R**: Donchian edges per scale (4h/3d · 1d/20d · 4d/80d) + distance + level strength
   `上下文`; round numbers `弱预测,瞬时`; polarity-flip `上下文,实测仅±3%`; **order-flow
   absorption at each key level** (defended / broke) `确认` ← the only thing that "upgrades" S/R.
3. **跨级别合成**: alignment + HTF→LTF bias (+25–32pt); **where the "false range" is** (LTF range
   inside an HTF trend = pullback) `上下文,概率性`; nested read (大级别定方向, 小级别找拐点);
   **confirmation ladder** (break 4h needs +X% / 1d +Y% / 4d +Z% → notches 42→58→71→78%) `确认,迟到`;
   **中继 vs 反转** + the honest coin-flip framing + the price that confirms/refutes; speed-cluster
   state (fast = 变盘 in progress / slow = range coiling) `确认`.
4. **订单流**: real taker-delta / CVD + flow direction + `real` flag `确认,实时`; CVD divergence
   `确认`; absorption at key S/R `确认`.
5. **衍生品/持仓**: funding (annualized + 30d percentile + trend) → crowd long/short; basis; OI;
   liquidation context `噪声,非精确点位`; **carry collectability** (positive/negative, crowded ⇒
   squeeze fuel, **not a bottom**) `carry=唯一稳健edge`.
6. **慢机构流 / 可日历事件**: ETF flow trend (slow institutional counterparty) `上下文偏领先`;
   stablecoin dry powder `上下文`; **token unlocks / dateable events** (alts) `预测型!可提前研究`.
7. **宏观 / regime**: risk-on/off (NASDAQ/SPX, VIX, VIX-term) `上下文`; liquidity (Fed bal-sheet
   direction, DXY, rates) `frame,指标会失灵`; **credit (HY spread / HYG — fastest tripwire, 1–3mo
   lead)** `上下文偏领先`; curve (10Y-2Y/3M) `上下文,滞后`; **BTC↔NASDAQ beta** (decoupled vs
   coupled — is the weakness idiosyncratic or macro?) `关键上下文`; regime fragility.
8. **链上/周期**: MVRV-Z / SOPR / cycle position `上下文,滞后` (gated, qualitative); fundamentals
   = BTC's liquidity-asset role + narrative; crypto only trusts **fees/real-revenue + unlocks**
   (S2F/NVT/MVRV = mostly overfit narrative) `上下文`.
9. **EDGE AUDIT (收口)**: marginal counterparty (who's on the other side); **mechanical lean
   (NOT a prediction)**; fragility list; **triggers to watch** = ① per-level scale-break confirm
   prices ② exogenous turns (ETF flips, funding flips negative, macro risk-off).
10. **操作映射 (L0 notify+manual)**: what to do now (stand aside / collect carry / wait for a
    trigger — react, not predict); ladder-add confirm prices + asymmetric risk (vol-sizing, stops,
    positive skew); **hard rules: never place orders / no broker SDK / everything via
    Proposal(order_intent) / always append the disclaimer.**

## Hard rules (for any agent driving this)

- **NEVER place an order / transfer funds.** Order intents go through `omni-hub order-propose`
  (`Proposal(kind=order_intent)`, human-reviewed); the broker CLI executes post-approval.
- **No prediction.** Report state + triggers; refuse to call a top/bottom. If asked to predict,
  state the wall (price-only direction caps ~0.59 AUC OOS) and give the read instead.
- **omni-hub never imports `quant`** — always the CLI shell-out seam.
- **Always append**: `机械指标/流数据的状态分析,非投资建议、非涨跌预测。`

## Data inputs

- Live: Binance fapi (price/funding/OI/basis + klines incl. real taker-buy volume); Yahoo (macro,
  best-effort, fails soft).
- **ETF flow** = maintained file `~/quant/etf_flow.json` (`{trend, net_recent_musd, note, as_of}`)
  — no reliable free real-time API; a fetcher or human updates it.
- Store (`~/quant/market`, DuckDB+Parquet) for historical/backtest; live reads don't need it.

## Evidence behind the discipline

See `research/` (reproducible studies) + the ClaimLedger findings. The thesis: price-only direction
is unpredictable (~0.59 wall, self-similar, near-martingale); the **only robust edge is funding
carry**; trend-following (4h confirmed RIDE) is robust but a **risk reduction, not alpha** (doesn't
beat buy&hold); reversal/S-R/level structure is **confirmatory, not predictive**. → react, don't predict.
