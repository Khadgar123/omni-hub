# Crypto quant — notify+manual trading pipeline

BTC/ETH, single-user, second-level. **No code path places an order** (autonomy
L0): the system produces *suggested manual actions* a human reviews and executes.
All third-party deps (duckdb/numpy/pandas/...) live in this sub-instance; the
main `src/omni_hub` stays stdlib-only. Run everything with the `quant` conda env.

```
data.binance.vision 1s dump ─► DuckDB/Parquet store (trades=truth, bars derived; SCHEMA.md frozen)
        │
        ├─ resample 1s → 1h / 4h / 1d            (quant/resample.py, DuckDB, UTC-aligned)
        ▼
   features (EMA/RSI/ATR/ADX/Bollinger/ROC/realized-vol; point-in-time)   quant/features.py
        ▼
   regime committee (ADX + EMA-slope/ATR + vol) + CUSUM change-point      quant/regime.py
        ▼
   MarketState — strict top-down MTF (1d bias, 4h veto-to-flat, CP→flat)  quant/market_state.py
        ▼   (regime gate: ENTRIES gated, EXITS always allowed)
   strategies: trend_donchian_v1 + range_bb_revert_v1  →  StrategyIntent  quant/strategy/
        ▼   deterministic sizing (ATR/vol-target ~1% risk, ¼-Kelly cap)   quant/strategy/sizing.py
   ┌────────────────────────────┬───────────────────────────────────────┐
   ▼ research/offline            ▼ live (notify+manual)
   event-loop backtest (PARITY,  TradeAlert → ~/quant/alerts.jsonl →      quant/alert.py
   shift-1, costs, PSR)          channel (Interface Plane) → human trades quant/live.py
   quant/backtest/engine.py
        ▼
   VALIDATION MOAT — CPCV + Deflated Sharpe + PBO + robustness            quant/backtest/validation.py
   sweep + strict gate (viable only if survives deflation+overfit+OOS)    quant/backtest/sweep.py
```

## CLI (all read-only / propose-only; no orders)

```bash
QPY=~/opt/anaconda3/envs/quant/bin/python   # the quant env

# regime read (from the stored 1s, resampled)
$QPY -m quant.market_state --symbol BTCUSDT --root ~/quant/market

# backtest one strategy on real data (parity engine)
$QPY -m quant.backtest.harness --strategy trend_donchian_v1 --symbol BTCUSDT \
     --from 2024-07-01 --to 2026-04-30 --root ~/quant/market

# sweep a grid through the validation gate (PBO + Deflated Sharpe + OOS)
$QPY -m quant.backtest.sweep --strategy trend_donchian_v1 --symbol BTCUSDT \
     --from 2024-07-01 --to 2026-04-30 --root ~/quant/market

# LIVE (no store): real-time regime + suggestions from Coinbase/Kraken
$QPY -m quant.live state  --symbol BTCUSDT --venue coinbase
$QPY -m quant.live alerts --symbol ETHUSDT --venue kraken --emit ~/quant/alerts.jsonl

# always-on watcher (launchd): poll, flag regime changes, emit TradeAlerts
$QPY -m quant.live watch --symbols BTCUSDT,ETHUSDT --venue coinbase \
     --interval 300 --emit ~/quant/alerts.jsonl

# bulk backfill 1s history (CHECKSUM-verified, 50G-capped, newest-first)
$QPY agent-harness/integrations/finance/binance_vision.py \
     --symbols BTCUSDT,ETHUSDT --interval 1s --from 2020-01 --to 2026-04 \
     --root ~/quant/market --max-gb 50 --newest-first
```

## The validation moat (the differentiator)

None of the surveyed OSS engines (freqtrade/nautilus/jesse/vectorbt/backtrader/
hummingbot/OctoBot) ship statistical-overfitting defense. We do, reimplemented
clean from López de Prado / Bailey: **purged+embargoed CV, Combinatorial Purged
CV, Deflated Sharpe (deflated by # configs tried), Probability of Backtest
Overfitting (CSCV), event-concentration, IS→OOS degradation.** A strategy is
`viable` only if it survives all of them with a positive OOS Sharpe.

Honest current status: the two untuned default strategies, swept across params
on real BTC/ETH 1s (2024–2026), are **all REJECTED** (best OOS Sharpe < 0, DSR
≈ 0.08). The infrastructure is complete; finding a real edge is strategy/signal
work — and the gate will keep us honest about it.

## Safety invariants (non-negotiable)

- **No order path.** `live`/`alert` only emit suggestions; a human executes.
- **No LLM in the hot path.** Regime/strategy/sizing are deterministic.
- **Parity.** Backtest drives the same `gated_evaluate` + `sizing` as live.
- **No look-ahead.** Engine fills a bar-i signal at bar-i+1 open (shift-1).
- **A backtest without a passing DSR/PBO gate is not trusted.**

## Data sources

History: `data.binance.vision` 1s static dumps (free, stable; NOT the strict
signed API). Live: Coinbase + Kraken public REST (fast, US-friendly; Coinbase
confirm tf = 6h since it lacks 4h). Funding/basis carry is a *signal* only
(compressed below T-bills since the ETF era).

## License guardrails

Borrow only MIT/Apache/BSD (jesse, hummingbot, TradingAgents, skfolio); GPL/
no-license repos (freqtrade, nautilus, NostalgiaForInfinity, regime-classifier,
vectorbt's Commons Clause) are read-for-ideas / reimplement-clean only. See
`docs/crypto-quant-reference-survey-2026-05-30.md`.
