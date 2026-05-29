# quant — single-user market data sub-instance (GATED scaffold)

> **Status: scaffold, intentionally not built out.**  This directory is the
> blueprint + skeleton for a personal quant data layer.  Build the ingestion
> only when real quant work actually starts (a first backtest, or a
> >few-hundred-MB OHLCV pull).  Until then the live
> `scripts/akshare_query.py` print-to-stdout flow in the main repo is
> sufficient, and the data layer is correctly **zero**.

## Why this is a separate sub-instance (not in the main repo)

The main omni-hub repo pins `dependencies = []` — it is a stdlib-only
knowledge harness.  A quant store needs `duckdb` / `polars` / `pyarrow`,
which would be the project's first-ever runtime deps.  So, exactly like
`agent-harness/researchflow`, the quant layer lives **outside** the main
package with its **own** `pyproject.toml` and deps.  Nothing in `src/omni_hub`
imports it.

It also lives **outside** `vault/`.  `vault/` *is* the knowledge harness
(raw → evidence → wiki); high-volume derived OHLCV numerics have no business
in a human-reviewed claim ledger.  Default data root is `~/quant/market`.

## The one engine (and the four we rejected)

For a **single user on a laptop**, the minimal stack is **DuckDB reading
Hive-partitioned Parquet** — in-process (no daemon), reads the same Parquet
files git/backup already track, and is laptop-proven to ~1B rows.

| Engine | Verdict | Why |
|---|---|---|
| **DuckDB + Parquet** | **adopt** | in-process, zero-server, billion-row on a laptop, SQL + zero-copy Parquet scan |
| Polars | redundant | a second in-process query engine; DuckDB already covers it — pick one |
| ClickHouse / QuestDB | reject | server daemons for concurrent multi-client HF ingest — irrelevant to one person |
| TimescaleDB | reject | Postgres extension; same multi-client framing |
| ArcticDB | reject | dataframe *versioning* (Man Group); git + a file vault already give that for one user |
| kdb+ | reject | ~$100k/yr commercial; free tier is non-commercial only; cold-q syntax |

## Data model: trades are truth, K-line is derived

Standard market-microstructure practice (Databento / CoinAPI / TickData):

* **Truth layer** — `trades` / `quotes` / `orderbook` events, append-only,
  carrying `exchange_ts`, `receive_ts`, `sequence`, `fee`, `slippage`,
  `order_state`.  This dual-timestamp + sequence detail is what live
  execution and accurate backtests need.
* **Derived layer** — OHLCV bars (`1m`, `1d`, …) are **rebuilt from trades**
  (`bars_from_trades` in `market_store.py`), never the source of truth.

Scope note: a daily/minute-bar backtest only needs (a) Parquet OHLCV
partitioned by symbol/date and (b) optionally a thin trades table.  The full
fill/slippage/`order_state` event log only earns its keep once you run
**live** execution through `ccxt` / `alpaca-py` (which, per the main repo's
`finance_ops/analyst.py` scope note, belong in
`agent-harness/integrations/finance/`, reached via `Proposal(kind=order_intent)`).

## Hive partition layout

```
~/quant/market/
  trades/  symbol=NVDA/ date=2026-05-29/ part.parquet     # truth (event log)
  bars_1d/ symbol=NVDA/ date=2026-05-29/ part.parquet     # derived
  bars_1m/ symbol=NVDA/ date=2026-05-29/ part.parquet     # derived
```

DuckDB globs these directly:
`SELECT * FROM '~/quant/market/bars_1d/symbol=NVDA/**/*.parquet'`.

## Bonus: eval/experiment data (do NOT do this yet)

The audit's "move `eval_runs.sqlite3` to Parquet" idea is **rejected for now**:
it is 2.9 MB / 394 rows — four orders of magnitude below where SQLite
struggles — and the bloat is the `verdict_json` blob column, not row count.
The cheap fixes if retrieval ever bites: `VACUUM`, trim/compress
`verdict_json`, add an index, prune superseded rows.  Reach for DuckDB only
once this sub-instance already ships it **and** eval rows cross ~1–10 M.
