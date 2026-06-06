# quant market store — frozen record schema (cross-session contract)

> **This file is the seam.** The stdlib-only `src/omni_hub` side codes against
> *this document* — never by importing the `quant` package (it can't: duckdb is
> not a main-repo dep). The contract is: (1) these Parquet record schemas and
> (2) the CLI in [§7](#7-cli-contract-the-shell-out-seam). Changing a column
> type/name/meaning is a breaking change — bump `SCHEMA_VERSION` and note it in
> [§8](#8-versioning).

`SCHEMA_VERSION = 1`

The source of truth for these specs is `quant/market_store.py`
(`TRADE_FIELDS`, `BAR_FIELDS`, … as plain Python data). This doc and that code
must agree; the test suite asserts the column sets match.

---

## 1. Layout & conventions

```
<root>/                                  # default ~/quant/market
  trades/   symbol=<SYM>/date=<YYYY-MM-DD>/part-NNNNN.parquet   # TRUTH (event log)
  quotes/   symbol=<SYM>/date=<YYYY-MM-DD>/part-NNNNN.parquet   # TRUTH
  orderbook/symbol=<SYM>/date=<YYYY-MM-DD>/part-NNNNN.parquet   # TRUTH (L2/L3 deltas)
  bars_1m/  symbol=<SYM>/date=<YYYY-MM-DD>/part-NNNNN.parquet   # DERIVED
  bars_1d/  symbol=<SYM>/date=<YYYY-MM-DD>/part-NNNNN.parquet   # DERIVED
  _reference/
    corporate_actions.parquet
    listings.parquet
    calendar.parquet
```

- **`symbol` and `date` are Hive partition keys — they live in the PATH, not in
  the Parquet payload** (canonical Hive). DuckDB recovers them as columns via
  `hive_partitioning=1`; on read they appear as ordinary columns. `date` is the
  **UTC** calendar date of the row's event/bucket timestamp.
- **Append-only.** A partition may hold many `part-NNNNN.parquet` files; readers
  glob `*.parquet`. Ingestion is **at-least-once** — dedup by `trade_id`
  (`bars_from_trades` does this automatically).
- **Timestamps are `int64` epoch *microseconds*, UTC.** (`MICROS = 1_000_000`.)
  Convert to nanoseconds (×1000) for nautilus; see [§6](#6-nautilus_trader-mapping).
- **Truth vs derived:** `trades`/`quotes`/`orderbook` are the truth layer;
  OHLCV `bars_*` are **rebuilt from trades** (`bars_from_trades`) and are never
  authoritative.
- Bars are aligned to the **UTC epoch** (`1d` = UTC calendar day). Session-aligned
  bars are a calendar-aware extension (see `trading_sessions`), not the default.

---

## 2. `trades` (TRUTH, append-only)

Payload columns (partition keys `symbol`, `date` excluded):

| column | type | meaning |
|---|---|---|
| `exchange_ts` | int64 | event time at the exchange (epoch µs, UTC). Partition `date` derives from this. |
| `receive_ts` | int64 | local receive time (epoch µs, UTC). Defaults to `exchange_ts` if unset. Latency/PIT. |
| `sequence` | int64 | exchange sequence / monotonic id per symbol |
| `price` | float64 | trade price |
| `size` | float64 | base-asset quantity |
| `side` | string | aggressor side: `"buy"` / `"sell"` / `""` (unknown) |
| `trade_id` | string | exchange trade id — the **dedup key** |
| `fee` | float64 | realized fee (own fills only; `0` for market data) |
| `slippage` | float64 | realized slippage (own fills only; `0` for market data) |
| `order_state` | string | own-exec lifecycle (`new`/`filled`/`partial`/`canceled`); `""` for market data |
| `venue` | string | `"binance"` / `"alpaca"` / … |

The same table serves **market-data ticks** (fee/slippage/order_state = default)
and **own-execution fills** (those fields populated) — that dual role is why the
truth layer carries them.

---

## 3. `quotes` (TRUTH) · `orderbook` (TRUTH)

**`quotes`** (top-of-book / BBO): `exchange_ts`, `receive_ts`, `sequence`,
`bid_px`, `bid_sz`, `ask_px`, `ask_sz` (float64), `venue` (string).

**`orderbook`** (L2/L3 **deltas**, not full snapshots): `exchange_ts`,
`receive_ts`, `sequence` (int64), `side` (string `"bid"`/`"ask"`), `price`,
`size` (float64; **`size==0` ⇒ level removed**), `is_snapshot` (bool),
`venue` (string). Store deltas + periodic snapshots, not every full book.

> These two are schema-frozen for forward compatibility; the v1 ingestion path
> (Binance) populates `trades` only.

---

## 4. `bars_<freq>` (DERIVED OHLCV)

`<freq>` ∈ `1m`, `5m`, `1h`, `1d`, … (`freq_to_seconds` parses `s/m/h/d/w`).

| column | type | meaning |
|---|---|---|
| `bucket_ts` | int64 | bar **open** time = start of interval (epoch µs, UTC) |
| `open` / `high` / `low` / `close` | float64 | OHLC |
| `volume` | float64 | Σ trade size in the bucket |
| `vwap` | float64 | volume-weighted avg price (`Σ price·size / Σ size`) |
| `trades` | int64 | trade count in the bucket |

On read, `bars()` may also surface `symbol`/`date` (partition-derived) and, when
`adjust=True`, an `adjusted: true` flag on rows it back-adjusted.

---

## 5. Reference tables (point-in-time correctness)

Small single-file Parquet under `_reference/`. **`symbol` IS a payload column
here** (these are not partitioned).

**`corporate_actions.parquet`** — applied only when `event_date <= asof` (anti
look-ahead):

| column | type | meaning |
|---|---|---|
| `symbol` | string | |
| `event_date` | string | `YYYY-MM-DD` ex-date |
| `type` | string | `split` / `dividend` / `rename` / `delist` |
| `ratio` | float64 | split ratio (`4.0` ⇒ 4:1; `1.0` = no-op) |
| `cash_amount` | float64 | dividend per share |
| `new_symbol` | string | for `rename` |
| `notes` | string | |

Split back-adjustment (`adjust_bars`): for a bar on date `d`, factor = Π split
ratios with `d < event_date <= asof`; prices ÷ factor, volume × factor.

**`listings.parquet`** — symbol master. **Delisted symbols are retained here,
never deleted** (anti-survivorship):

| column | type | meaning |
|---|---|---|
| `symbol` | string | |
| `name` | string | |
| `venue` | string | |
| `list_date` | string | `YYYY-MM-DD` |
| `delist_date` | string | `YYYY-MM-DD`, or `""` if active |
| `status` | string | `active` / `delisted` |
| `asset_class` | string | `equity` / `crypto` / … |

`listings_asof(asof)` annotates each row with `is_live` = `list_date <= asof and
(delist_date == "" or delist_date > asof)`.

**`calendar.parquet`** — trading sessions:

| column | type | meaning |
|---|---|---|
| `venue` | string | |
| `date` | string | `YYYY-MM-DD` |
| `is_open` | bool | |
| `open_ts` / `close_ts` | int64 | session open/close (epoch µs, UTC) |
| `session` | string | `regular` / `half` / `closed` / … |

---

## 6. nautilus_trader mapping

nautilus's `ParquetDataCatalog` *is* Parquet, so we map to its record shapes
rather than reinvent a catalog (`quant/backtest.py`). Timestamps → **nanoseconds**.

`trades` → `TradeTick`: `instrument_id` = `"<SYM>.<VENUE>"`, `price`, `size`,
`aggressor_side` (`buy`→`BUYER`, `sell`→`SELLER`, `""`→`NO_AGGRESSOR`),
`trade_id`, `ts_event` = `exchange_ts*1000`, `ts_init` = `receive_ts*1000`.

`bars_<freq>` → `Bar`: `bar_type` = `"<SYM>.<VENUE>-<step>-<AGG>-LAST-EXTERNAL"`
(e.g. `NVDA.XNAS-1-DAY-LAST-EXTERNAL`), `open/high/low/close/volume`,
`ts_event` = `ts_init` = `bucket_ts*1000`.

---

## 7. CLI contract (the shell-out seam)

omni-hub shells out and parses **stdout** (pure JSON; warnings/errors go to
stderr). Run inside the quant venv:

```bash
python -m quant.market_store [--root R] [--format json|csv] <cmd> ...
# or the installed console script:
quant-market-store <cmd> ...
```

| command | args | stdout |
|---|---|---|
| `bars` | `--symbol --freq --start --end [--asof] [--adjust]` | JSON list of bar rows (§4) |
| `last-price` | `--symbol --asof` | `[{"symbol","asof","last_price"}]` (`last_price` may be `null`) |
| `trades` | `--symbol --start --end` | JSON list of trade rows (§2) |
| `bars-from-trades` | `--symbol --freq --start --end [--persist]` | derived bars; `--persist` also writes `bars_<freq>` |
| `listings` | `--asof [--venue] [--live-only]` | listing rows + `is_live` (§5) |
| `corporate-actions` | `--symbol --asof` | actions with `event_date<=asof` (§5) |
| `calendar` | `--start --end [--venue]` | open sessions (§5) |
| `ingest-sample` | `[--symbol]` | one-row summary; materializes the bundled sample into `--root` |

`--start`/`--end`/`--asof` accept `YYYY-MM-DD` (a bare date means start-of-day
for `start`, end-of-day for `end`/`asof`), an ISO-8601 datetime, or an epoch
number (seconds/millis/micros auto-detected by magnitude).

Integration flow (per `agent-harness/quant/README.md`): omni-hub
`finance_ops` emits `Proposal(kind=order_intent)`; the live-exec side in
`agent-harness/integrations/finance/` reads this store (it *may* import `quant`
— it runs in the quant venv) and/or shells out via the CLI above.

---

## 8. Versioning

`SCHEMA_VERSION = 1` (initial freeze).

Backward-compatible additions (new optional columns with defaults, new tables)
do **not** bump the version. Renaming/removing/retyping a column, or changing a
timestamp unit, **does** — record the change here so the omni-hub side can adapt.

---

## 9. Regime read — `MarketState` (derived analysis; additive CLI seam)

Not a stored record schema (so it is **independent of `SCHEMA_VERSION`**): a
deterministic, point-in-time multi-timeframe regime read *derived* from the
`bars_<freq>` above. Computed in `quant/{features,regime,market_state}.py`
(pure-stdlib indicators + an ADX / EMA-slope / realized-vol committee + a CUSUM
change-point). No LLM, no look-ahead. Numerics stay under `~/quant`; nothing
here writes to the knowledge vault.

CLI (the omni-hub shell-out seam — stdout is one JSON object):

```bash
# stored bars (point-in-time, default):
python -m quant.market_state --symbol BTCUSDT [--asof YYYY-MM-DD] \
    [--htf 1d] [--confirm 4h] [--root R]
# live read (current candles; the fresh read when the store is stale):
python -m quant.market_state --symbol BTCUSDT --live [--venue coinbase|kraken|binance]
```

**Two data paths, identical output shape.** `--live` fetches the most recent
candles from a public venue (`quant.live`) instead of the stored bars. This is
the current read for the **scheduled vol+trend reference indicator**
(`scripts/quant_daily.py`, launchd `com.omni-hub.quant`, daily 09:00), which
cascades **Binance → Coinbase → Kraken → stored** (Binance leads as the
CN/Asia-reachable venue) and writes the feed to
`.omni/quant/regime-indicator.jsonl` (append-only, one line per symbol per run) +
`.omni/quant/regime-latest.json`, stamping each record's `source` and
`stale_days` so a stale reading is never mistaken for a current one. Coinbase has
no native 4h, so a default `4h` confirm maps to `6h` there.

`MarketState` fields:

| field | type | meaning |
|---|---|---|
| `symbol` | string | |
| `as_of` | int | HTF last-bar `bucket_ts` (epoch µs, UTC) |
| `htf_tf` / `confirm_tf` | string | bias / confirm timeframes (default `1d` / `4h`) |
| `composite_bias` | string | `long` / `short` / `flat` — the gate strategies obey |
| `regime_label` | string | HTF label: `strong_down`/`down`/`range`/`up`/`strong_up` |
| `direction` | string | HTF `up` / `down` / `flat` |
| `vol_bucket` | string | `low` / `normal` / `high` |
| `stand_down` | bool | change-point veto (either timeframe) |
| `per_tf` | object | `{tf: label}` |
| `htf` / `confirm` | object | full per-timeframe `RegimeResult` (`adx`, `slope_per_atr`, `insufficient`, …) |
| `schema_version` | string | `ms-v1` |

Fusion contract (strict top-down): the **HTF is the sole bias source**; the
confirm timeframe may only **veto a bias to flat**, never flip it; a change-point
or insufficient data on either timeframe forces `flat`. Thresholds (ADX 25/40,
vol percentile, CUSUM) are conventional **untuned defaults** — hyper-parameters
to fit under purged-CV later, not tuned numbers.
