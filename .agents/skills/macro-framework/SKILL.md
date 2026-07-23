---
name: macro-framework
description: |
  Analyze the CURRENT global macro tape — a cross-asset daily dashboard over US/China/Japan/Korea
  equities, US Treasury yields + curve (2s10s) + China 10Y, the dollar (DXY) and CNY, gold, oil,
  copper, and BTC — each with regime (trend/vol/ADX) + market structure (BOS/CHoCH/背驰), plus a
  panel (rate curve, credit spread proxy HYG/IEF, VIX/MOVE vol, commodities) and a cross-asset
  strength + correlation matrix, collapsed into a readable risk-on/off + growth/inflation read.
  Use whenever the user asks: "全球宏观 / 大类资产现在怎么样"、"美股/A股/日股/韩股/债市/汇率/黄金/油 现状"、
  "风险偏好 / risk-on-off"、"跨资产 / 相关性"、"为什么 A股 弱 / 美股强"、"global macro / cross-asset".
  Read-only; DAILY granularity; NEVER places orders; NEVER predicts price. Do NOT trigger for: a
  single crypto asset (use quant-framework / crypto-read) or single-stock screening (finance-screen).
license: MIT
---

# Macro Framework — global cross-asset daily read

Thin trigger for `omni-hub`'s global macro dashboard (the TradFi sibling of `quant-framework`/crypto).
Produce the read by running the operation, not by eyeballing charts. Engine: `quant.macro` (quant
venv; free data via yfinance + akshare). **Daily granularity only** (no free intraday history for
TradFi).

## Run it

```bash
omni-hub macro-read           # whole-world dashboard;  --period 5y for a longer lookback
```

Returns `{narrative, read{assets, panel, cross}}`. **Lead with `narrative`** — a readable risk-on/off
+ growth/inflation read, NOT a table dump. For depth read `read.assets` (per-asset regime + structure
+ S/R), `read.panel` (curve / credit / vol / commodities), `read.cross` (correlation + leaders/laggards),
or run `python -m quant.macro` directly in the quant venv.

## Discipline (read the computed verdict, don't narrate over it)

1. **It's a STATE read, not a forecast.** Report regime + structure + cross-asset relationships +
   the macro quadrant (growth soft/firm × inflation up/down); never call tops/bottoms.
2. **Frame cross-asset, not single-asset**: who's leading/lagging, risk-on vs risk-off, where the
   divergences are (e.g. equities up while BTC/commodities down = the tell).
3. **Honest gaps**: macro econ series (CPI/PMI/jobs via akshare) lag ~months; JGB/Bund + fresh FRED
   are TODO; intraday history isn't free for TradFi — so this is a **daily** read.
4. **Always append**: `机械统计/公开数据状态分析,非投资建议、非涨跌预测。`

## Hard rules

- **NEVER place an order / move funds** (order intents → `omni-hub order-propose`).
- **omni-hub never imports `quant`** — only the `macro-read` shell-out seam.
- carry/structure edges are confirmatory; direction is unforecastable (the same 0.59-wall discipline
  as crypto applies to daily TradFi).
