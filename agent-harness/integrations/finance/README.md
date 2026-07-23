# finance integration shims

Broker SDKs and exchange account checks live here, outside the stdlib-only
`src/omni_hub` package.

## Binance spot live check

`binance_spot_live.py` is intentionally small and conservative:

- public `ping` / `time`
- signed read-only `account`
- signed `order-test`, which calls Binance's test-order endpoint and does not
  submit a real order

It deliberately does not implement the live order endpoint. Real execution
should consume approved `Proposal(kind="order_intent")` records.

Secrets are read from env first:

```bash
export BINANCE_API_KEY=...
export BINANCE_API_SECRET=...
```

or from the omni-hub local secret backend:

```bash
PYTHONPATH=src python3 agent-harness/integrations/finance/binance_spot_live.py configure
```

`configure` prompts locally, validates the credentials against Binance's signed
read-only account endpoint, and only stores them after validation succeeds.

Run:

```bash
PYTHONPATH=src python3 agent-harness/integrations/finance/binance_spot_live.py ping
PYTHONPATH=src python3 agent-harness/integrations/finance/binance_spot_live.py account
PYTHONPATH=src python3 agent-harness/integrations/finance/binance_spot_live.py order-test \
  --symbol BTCUSDT --side BUY --quantity 0.0001 --type MARKET \
  --i-understand-this-is-a-live-api-test
```

For Binance.US, pass:

```bash
--base-url https://api.binance.us
```

## Binance market-data ingestion

`binance_market_data.py` pulls **public** market data (no API key) into the
quant store (`agent-harness/quant/`, a separate venv with duckdb/pyarrow).
This integration layer MAY import `quant`; the stdlib-only `src/omni_hub` may
not (the seam is a CLI shell-out + `agent-harness/quant/SCHEMA.md`).

- `/api/v3/aggTrades` → the TRUTH `trades` table (append-only, frozen schema).
- `/api/v3/klines` → DERIVED `bars_<freq>` (Binance pre-aggregates; prefer
  ingesting aggTrades when microstructure matters — our own truth is
  `bars_from_trades`).

It reuses `binance_spot_live.request_json` (one HTTP path; `request_fn` is
injectable, so the mappers/fetchers are unit-tested with **no network** in
`tests/test_binance_market_data.py`).

```bash
# fetch only (prints raw JSON, no write)
python3 agent-harness/integrations/finance/binance_market_data.py \
  agg-trades --symbol BTCUSDT --limit 5

# ingest aggTrades over a window -> trades/ parquet (paginates by fromId)
python3 agent-harness/integrations/finance/binance_market_data.py \
  ingest --symbol BTCUSDT --start 2026-05-28 --end 2026-05-29 --root ~/quant/market

# ingest klines -> bars_<freq> parquet
python3 agent-harness/integrations/finance/binance_market_data.py \
  ingest-klines --symbol BTCUSDT --interval 1d --start 2026-01-01 --end 2026-05-29
```
