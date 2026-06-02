---
name: quant-framework
description: |
  Analyze the CURRENT live state of a crypto asset (BTC / ETH / any Binance perp) via the
  unified edge-audit framework — regime across timeframes, support/resistance, real order-flow
  (taker-delta/CVD), carry/funding/positioning, ETF & stablecoin flows, and the macro regime —
  collapsed into "who is the counterparty / what is fragile / which triggers to watch".
  Use whenever the user asks: "分析现在的 BTC / ETH 行情"、"btc 现在什么状态 / 该怎么操作"、
  "对手盘是谁"、"edge audit"、"crypto framework read"、"现在能不能抄底 / 追多"、
  "eth 现在的多级别 / 趋势 / 震荡 / 支撑压力". Read-only; NEVER places orders; NEVER predicts price.
  Do NOT trigger for: equities-only questions (use finance-screen), generic coding, or backtests.
license: MIT
---

# Quant Framework — crypto edge-audit read

A **project-level skill** owned by `omni-hub`. It tells the agent how to produce a complete,
honest crypto state read without guessing — by running the registered operation, not by
eyeballing a chart.

## When to use

The user wants the current state / "how to operate" on a crypto asset (BTC, ETH, SOL, …):
multi-timeframe regime + S/R + order-flow + carry + flows + macro, and the **edge audit**
(counterparty / fragility / triggers). The full report contract is in
**`agent-harness/quant/FRAMEWORK.md`** — read it for the 10-section spec.

## What to do

1. Run the canonical operation (stdlib seam → quant venv engine):

   ```bash
   PYTHONPATH=src python3.12 -m omni_hub.cli crypto-read --symbol BTCUSDT    # or ETHUSDT / SOLUSDT …
   ```

   (Direct equivalent if the omni-hub CLI is unavailable: `python -m quant.framework --symbol BTCUSDT`
   inside the quant env; default output is the narrative, `--json` for all layers, `--metrics` for the raw block.)

2. **Lead with the narrative** (the `narrative` field / default output) — a readable 4-sentence
   read, NOT a pile of indicators. Then, only if the user wants depth, expand the layers using the
   10-section spec in `FRAMEWORK.md`, tagging each signal by nature (`预测型`≈none / `确认型` /
   `上下文` / `噪声`).
3. Frame everything as **state + counterparty + triggers**, never a forecast. If macro is missing
   ({} — Yahoo failed), say so; if `ok:false`, surface the error (e.g. quant venv / network).
4. Close with the triggers to watch (scale-break confirm prices + exogenous turns: ETF flip,
   funding turning negative, macro risk-off) and the standing truth: **carry is the only robust
   edge; trend-follow is risk-reduction not alpha.**

## Hard rules

- **NEVER place an order or move funds.** Trade intents go through `omni-hub order-propose`
  (`Proposal(kind=order_intent)`, human-reviewed); the broker CLI executes post-approval.
- **NEVER predict price / call a top or bottom.** If asked, state the wall (price-only direction
  ≈0.59 AUC OOS) and give the read instead.
- **omni-hub never imports `quant`** — only the `crypto-read` shell-out seam.
- **Always append**: `机械指标/流数据的状态分析,非投资建议、非涨跌预测。`

## Expected output shape

A short readable read, e.g.:

> **BTCUSDT 70,431**: 大级别在憋(震荡、低波),而 4h/1h/15m 一路下行、订单流真实主动卖占优——下跌在走。
> 对手盘是拥挤的杠杆多头(funding 93 分位),撞着下行+ETF 持续流出、basis 微负——踩踏风险大。
> 宏观却是 risk-on(VIX 16、信用稳)——这是 BTC 特质性走弱,不是宏观崩。 站一边:收回 74,293 才谈做多,
> 丢 70,651 确认下行,真拐点看 ETF 流转正或 funding 转负。
> *机械指标/流数据的状态分析,非投资建议、非涨跌预测。*
