---
name: quant-framework
description: |
  Analyze the CURRENT live state of a crypto asset (BTC / ETH / any Binance perp) via the unified
  edge-audit framework — multi-timeframe regime, swing structure (BOS/CHoCH/double-bottom/背驰),
  support/resistance, real order-flow (taker-delta/CVD), carry/funding/positioning, ETF flows, and
  macro — collapsed into "who is the counterparty / what is fragile / which triggers to watch".
  Use whenever the user asks: "分析现在的 BTC / ETH 行情"、"btc 现在什么状态 / 该怎么操作"、
  "对手盘是谁"、"edge audit"、"现在能不能抄底 / 追多"、"eth 现在的多级别 / 趋势 / 支撑压力 / 双底 / 结构".
  Read-only; NEVER places orders; NEVER predicts price. Do NOT trigger for: equities-only questions
  (use finance-screen), generic coding, or backtests.
license: MIT
---

# Quant Framework — crypto edge-audit read (the behavioral contract)

Thin trigger for `omni-hub`'s crypto state read. **Single sources of truth:** the report SPEC
(10 sections) + data inputs live in `agent-harness/quant/FRAMEWORK.md`; the **analysis discipline
below is canonical HERE** (the skill is the contract — both Claude Code and Codex load this file).
Produce the read by running the operation, not by eyeballing a chart.

## Run it

```bash
omni-hub crypto-read --symbol BTCUSDT      # or ETHUSDT / SOLUSDT …
```

Returns `{narrative, read{report, structure, sr, flow_by_tf, …}}`. **Lead with `narrative`** — a
readable 4-sentence read, NOT a metrics dump. For depth read `read.report` (the 10-section detailed
report) or run `python -m quant.framework --symbol BTCUSDT --full` in the quant venv. Frame as
**state + counterparty + triggers**, never a forecast; if `ok:false`, surface the error.

## 分析纪律 — read THIS before any cross-level conclusion (past misses → rules)

The framework computes the verdict; **read it, don't narrate over it.** Each rule is a real miss
logged in `agent-harness/quant/research/error-ledger.md`.

1. **Read §① (per-level regime) + §②b (swing structure: BOS/CHoCH · base/neckline · 背驰) before any
   "X is stronger/weaker" or cross-level claim.** Quote the per-TF labels; don't let the 1d label or
   funding override the 4h price structure. [2026-06-02 ETH double-bottom miss]
2. **"Range at the lows" ≠ "weak."** A post-decline base can be a double-bottom (the *stronger*
   structure) — check §②b before calling it weak.
3. **Never compare raw order-flow `delta` across assets** (BTC vs ETH = different contract scales) —
   compare flow *direction* + per-asset divergence only.
4. **A strength/weakness claim needs per-TF structural evidence** (§①/§②b), not 1d + funding alone.
5. **Distinguish "broke & continued" from "wicked the low & reversed"** — the Donchian `[破]` flag
   can't; the §②b trend (BOS vs CHoCH) + 背驰 does.
6. **Falsify before concluding (catches the *next*, unseen error).** Write one sentence first:
   *"the opposite read is ___, and the evidence for it is ___"* — make your conclusion account for it.

## Hard rules

- **NEVER place an order / move funds.** Trade intents go through `omni-hub order-propose`
  (`Proposal(kind=order_intent)`, human-reviewed); the broker CLI executes post-approval.
- **NEVER predict price / call a top or bottom.** If asked, state the wall (price-only direction
  ≈0.59 AUC OOS) and give the read instead. **carry is the only robust edge; trend-follow is
  risk-reduction, not alpha.**
- **omni-hub never imports `quant`** — only the `crypto-read` shell-out seam.
- **Always append**: `机械指标/流数据的状态分析,非投资建议、非涨跌预测。`
