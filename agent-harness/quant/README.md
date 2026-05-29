# quant — single-user market data sub-instance

> **Status: built out (v0.1).**  Tier-0 store is implemented: Hive-partitioned
> Parquet writer, `bars_from_trades`, a DuckDB glob query API, point-in-time
> correctness (delisting retention + corporate actions + trading calendar), a
> CLI shell-out seam, a nautilus-compatible backtest read-path, and a Binance
> ingestion path.  The record schema is frozen in **[`SCHEMA.md`](SCHEMA.md)**
> — that doc + the CLI are the contract the stdlib-only main repo codes against.
> The design below (engine choice, truth-vs-derived model, scale triggers) is
> unchanged and authoritative.

Quick start (runs in the `quant` venv only — see [Usage](#usage)):

```bash
make install                       # pip install -e ".[dev]" into the quant env
make test                          # quant store tests + finance ingestion tests
python -m quant.market_store --root ~/quant/market ingest-sample
python -m quant.market_store bars --symbol DEMO --freq 1d --start 2026-01-01 --end 2026-01-10 --asof 2026-01-03 --adjust
```

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

## Package layout

```
agent-harness/quant/
  pyproject.toml          # name=omni-hub-quant; deps duckdb/pyarrow/polars; scripts; pytest cfg
  SCHEMA.md               # FROZEN record schema — the cross-session contract
  Makefile                # install / test / sample / smoke (runs in the quant env)
  quant/
    market_store.py       # writer + bars_from_trades + DuckDB query API + PIT + CLI
    backtest.py           # backtest read-path + nautilus_trader record-shape mapping
    sample.py             # the bundled deterministic sample dataset
    __main__.py           # `python -m quant` == `python -m quant.market_store`
  sample/trades.ndjson    # human-facing sample artifact (regen: `make sample`)
  conftest.py + tests/    # quant store tests
# finance ingestion lives in the sibling integrations dir (it MAY import quant):
agent-harness/integrations/finance/binance_market_data.py
tests/test_binance_market_data.py          # ingestion tests (repo-root tests/)
```

## Usage

**Importable API** (inside the quant venv):

```python
from quant import market_store as ms
ms.write_trades(rows, root="~/quant/market")            # truth, append-only
bars = ms.bars_from_trades(ms.trades("NVDA", "2026-01-01", "2026-05-29"), freq="1d")
ms.write_bars(bars, symbol="NVDA", freq="1d")           # derived
ms.bars("NVDA", "1d", "2026-01-01", "2026-05-29", asof="2026-03-01", adjust=True)
ms.last_price("NVDA", asof="2026-03-01")                # point-in-time, no look-ahead
ms.live_symbols("2026-03-01")                            # PIT universe (no survivorship)
```

**CLI shell-out seam** (pure JSON on stdout — see [`SCHEMA.md` §7](SCHEMA.md)):

```bash
python -m quant.market_store bars --symbol NVDA --freq 1d --start 2026-01-01 --end 2026-05-29
python -m quant.market_store last-price --symbol NVDA --asof 2026-03-01
quant-market-store trades --symbol NVDA --start 2026-01-01 --end 2026-05-29   # console script
```

**Ingest from Binance** (public endpoints, no key; writes into `trades/`):

```bash
python agent-harness/integrations/finance/binance_market_data.py \
  ingest --symbol BTCUSDT --start 2026-05-28 --end 2026-05-29 --root ~/quant/market
```

## Backtest read-path / nautilus_trader

`quant.backtest.read_for_backtest(...)` returns point-in-time, split-adjusted
bars already shaped as nautilus `Bar` dicts (ns timestamps, `bar_type`); trade
ticks map to `TradeTick` dicts.  Because nautilus's `ParquetDataCatalog` *is*
Parquet, this aims to be **compatible** with it rather than reinvent a catalog
— `pip install ".[backtest]"` adds `nautilus_trader` when you want the real
catalog/engine.  See [`SCHEMA.md` §6](SCHEMA.md).

## Testing

Everything runs in the **quant venv only** (the main repo stays stdlib-only and
never imports this package):

```bash
make install          # editable install + dev deps into the `quant` env
make test             # quant store tests + finance ingestion tests
make smoke            # materialize the sample and read it back (no network)
# or directly:
cd agent-harness/quant && pytest
```

Tests are network-free: ingestion uses an injectable `request_fn`, and DuckDB
runs in-process.  `import quant` is lazy (PEP 562) so it does not pull in duckdb
until a query actually runs.
