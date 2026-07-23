# Quant Integration & Data Architecture — v0.1

> **Status (honest, per AGENTS.md HR#7):** the quant package is a **complete standalone
> scaffold** (market store, backtest, regime/levels, broker, dashboard) with its **seam to
> the stdlib core fully designed** (`SCHEMA.md`, `SCHEMA_VERSION=1`). **Closed-loop into
> omni_hub's 5 planes is NOT yet wired** — there is no bridge/skill/operation on the
> `src/omni_hub` side. This doc is the contract source-of-truth for finishing that wiring
> **without re-architecting** and **without breaking other modules**.

## 0. One-paragraph thesis

The project already has a **uniform integration spine** proven by six siblings
(researchflow, paperbite, swe-agent, graphiti, argilla, promptfoo): each is an independent
package under `agent-harness/`, registered in `manifest.json`, pinned as a submodule, and
reached from the stdlib core **only via a frozen CLI/JSON seam** — never imported. **quant is
the one sub-module not yet on this spine.** "The refactor" = put quant on the *same rails*
(plus hygiene + a reproducible data pipeline), not invent anything new. Copying the existing
pattern is precisely what makes it non-destructive.

---

## 1. The integration spine (do NOT redesign)

```
agent-harness/manifest.json          register id/path/upstream/role/decision/decision_log (HR#4)
   → scripts/add_pending_harness_forks.sh <id> → git submodule (pinned SHA)
agent-harness/<sib>/                  standalone pkg: own pyproject / SCHEMA.md / Makefile / tests / venv
   frozen SCHEMA.md + CLI(--json)     the ONLY seam; stdlib core never imports (it can't: duckdb ∉ core)
src/omni_hub/connectors/<x>_bridge.py thin stdlib subprocess wrapper → parse stdout JSON, degrade gracefully
src/omni_hub/cli/<area>.py            register()+COMMANDS, auto-discovered by cli/__init__
src/omni_hub/builtins.py              writes → OperationRunner.run(OperationSpec) → policy + audit (HR#1)
Proposal[T]                           external/agent output → human approval gate (HR#2)
TaskQueue + worker lane               background jobs (python/claude/codex/openhands) (HR#2)
domain_schemas.py + *-wiki/SKILL.md   register as a Skill
agent-harness/integrations/<domain>/  heavyweight SDK / live execution (MAY import quant; runs in quant venv)
```

**Precedent bridges to copy:** `connectors/trafilatura_bridge.py` (subprocess→JSON, `not_installed`
degrade), `retrieval/zhihu_weibo.py` (broker-stub with `status --json`), `research_assets.py`
(researchflow/paperbite read-only index pattern).

---

## 2. Quant package layout & seam

`agent-harness/quant/` is a proper package: `pyproject.toml`, `SCHEMA.md` (seam),
`Makefile` (own pytest in the `quant` conda env), `tests/`, `conftest.py`.

- **The core never imports quant.** `grep quant src/omni_hub/` → empty, by design.
- **Seam = `SCHEMA.md` (frozen Parquet record schemas) + the CLI shell-out.** `SCHEMA_VERSION=1`.
  Source of truth for the column sets is `quant/market_store.py` (`BAR_FIELDS`, `TRADE_FIELDS`).
- **Secrets:** `quant/broker.py` reads `.omni/secrets.json` directly with stdlib only
  (`local:omni-hub/api/binance/{key,secret}` refs), via the same git-common-dir discovery the
  core uses — so it works inside worktrees and never imports omni_hub.

---

## 3. Data architecture — the best-practice store (with measured numbers)

### 3.1 Why Parquet + DuckDB (and why NOT a DB server)

For **time-series OHLCV/tick, analytical, single-machine, Python** workloads, **Hive-partitioned
Parquet queried by embedded DuckDB is the optimum** and the 2024–2026 local-quant standard.
Parquet *is* the storage; DuckDB *is* an embedded analytical database (SQLite-for-analytics) —
**there is no server because that is correct, not an omission.**

| option | when it would win | verdict here |
|---|---|---|
| **Parquet + DuckDB (current)** | — | ✅ **optimal**: columnar, zstd, partition-pruned, zero-ops, free |
| ArcticDB (Man Group) | want native dataframe **versioning / bitemporal** | only real "upgrade" candidate |
| ClickHouse | tick → **billions of rows**, sub-second whole-table scans, concurrent writers | server overhead, overkill now |
| TimescaleDB (PG) | **live streaming + relational joins** | row-ish, slower scans, needs server |
| kdb+/q | institutional HFT tick | $$$ license, niche lang, overkill |
| Postgres/MySQL row-store | never (for this) | ❌ analytical scans hate row stores |

**Decision: keep Parquet + DuckDB.** Don't move to a server DB.

### 3.2 Layout (`~/quant/market`, default `DEFAULT_ROOT`)

```
<root>/trades|quotes|orderbook/symbol=<SYM>/date=<YYYY-MM-DD>/part-*.parquet   # TRUTH (event log)
<root>/bars_<tf>/symbol=<SYM>/date=<YYYY-MM-DD>/part-*.parquet                 # DERIVED (OHLCV)
<root>/_reference/{corporate_actions,listings,calendar}.parquet               # PIT reference
<root>/_ingest_manifest.jsonl                                                 # provenance (NEW, ingest.py)
```

### 3.3 Measured footprint (real, this machine — 2020-08 → 2026-04, BTC+ETH)

| tier | size / yr / symbol (Parquet+zstd) | note |
|---|---|---|
| 1d bars | ~3 MB | free |
| 1m bars | ~55 MB | cheap |
| **1s bars** | **~1.0 GB** (6.0 GB / 5.75 yr measured) | ~32 B/bar; only ~2× compressible (noisy) |
| **full tick (aggTrades)** | **~10–14 GB** (from 3.5 GB CSV/mo) | the expensive tier; raw CSV ~42 GB/yr |

**Sub-minute klines do NOT exist on the exchange API** — `1s/15s/30s` bars can only be
**derived from tick** (`bars-from-trades` / `materialize`). The klines API serves `1m`+ only.

**Sizing a realistic universe (10 symbols × 6 yr):** 1s ≈ 60 GB; full tick ≈ 300–800 GB. Both
fit on one SSD. **Best practice:** store 1s+ bars for everything (cheap); keep raw tick only for
the symbols/windows you actually need microstructure on (selective — it's the costly tier).

### 3.4 Reproducible ingest + compaction (NEW in v0.1)

The 26 GB store was previously an **undocumented one-off** (tick from binance.vision bulk dumps;
bars written by an ad-hoc script not in the repo). v0.1 closes that gap:

- **`quant/ingest.py`** — API → dedup(by `bucket_ts`) → `write_bars` → append `_ingest_manifest.jsonl`.
  - `refresh(sym,freq)` keep current · `backfill(sym,freq)` deep page-back (coarse tfs) ·
    `refresh_all()` sweep (resilient: one bad pair is recorded, never aborts the sweep).
  - Idempotent: overlapping windows never duplicate. CLI: `python -m quant.ingest --refresh-all`.
- **`quant/compact.py`** — merge the many small `part-*.parquet` per day into one zstd file
  (dedup by `bucket_ts`); idempotent. CLI: `python -m quant.compact --symbol BTCUSDT --freq 15s`.
- **Reproducibility** now = the ingest CLI + `DEFAULT_SYMBOLS/FREQS` in-repo + `_ingest_manifest.jsonl`
  runtime log. Schedule `--refresh-all` (launchd or an omni TaskQueue python-lane job).

---

## 4. Wiring into the 5 planes (additive only)

| step | file (NEW unless noted) | risk gate |
|---|---|---|
| register | `agent-harness/manifest.json` → pending_forks (DONE) | HR#4 |
| seam | `agent-harness/quant/SCHEMA.md` (exists) | SCHEMA_VERSION bump on change |
| bridge | `src/omni_hub/connectors/quant_bridge.py` (stdlib subprocess→JSON, degrade) | stdlib-only |
| CLI | extend `src/omni_hub/cli/finance.py` (exists) or new `cli/quant.py` | register()+COMMANDS |
| ops | `builtins.py`: `quant_bars` (READ_ONLY), `quant_backfill` (LOCAL_WRITE) | OperationRunner (HR#1) |
| **order safety** | order intents → **`Proposal(kind="order_intent")`** + `RiskLevel.EXTERNAL_SEND` + `approval_required` | HR#2 — formalizes "never auto-fire" |
| tasks | TaskQueue python-lane: `quant-backfill`, `quant-backtest`, `quant-daily-report` | HR#2 |
| skill | `.agents/skills/quant-*/SKILL.md` (finance domain exists) | HR#8–#10 |
| live exec | `agent-harness/integrations/finance/` (may import quant) | runs in quant venv |

The dashboard's "click-to-fire only, never autonomous" rule **is** the `Proposal` + policy gate
expressed in code — wiring it through the core makes that an audited, enforced contract.

---

## 5. State / config consolidation (non-destructive)

- **Canonical:** core state in `.omni/` (SQLite WAL + JSONL, gitignored); knowledge in `vault/`;
  secrets in `.omni/secrets.json` (discovery is already worktree-aware). **Don't touch.**
- **quant runtime state off `/tmp`:** the live dashboard was launched with `--paper/--book/--intents`
  pointing at `/tmp/*.json` (ephemeral). The code default is `~/quant/` — move runtime state to a
  stable `~/quant/state/` (launch flags + docs only; **no code change**).
- **Experiment scatter:** `~/quant/{blind_*,trainer}`, `/tmp/quant-oss` (14 OSS clones),
  `/tmp/quant-*-smoke` → `~/quant/scratch/` (gitignore) or delete.

---

## 6. Phased plan & checklist

- [ ] **P0 hygiene** — commit untracked modules (+ tests, HR#3); `make test-quant` green; clean tree.
- [ ] **P1 state off /tmp** — `~/quant/state/` defaults + docs.
- [x] **P2 data pipeline** — `ingest.py` + `compact.py` + manifest provenance (v0.1, tested).
- [ ] **P3 seam hardening** — add the missing `SCHEMA.md ↔ BAR_FIELDS/TRADE_FIELDS` column-assert test.
- [ ] **P4 plane wiring** — bridge + CLI + operations + `Proposal(order_intent)` + SKILL.md + lanes.
- [x] **P4a** — `make test-all` (core unittest + quant pytest), manifest entry. (v0.1)
- [ ] **P5 docs/honesty** — architecture doc + manifest decision_log; scaffolding vs closed-loop (HR#7).

---

## 7. Non-destructive guarantees

1. Core stays `dependencies = []` (stdlib); duckdb/pandas live only in the quant venv.
2. Only **additive** changes (new Proposal kind, new CLI area, new operations, new optional lane);
   the 9 core contracts (OperationSpec / RiskLevel / Task / Artifact / Proposal / …) are untouched.
3. Submodule pins a SHA — updating quant can't silently break the core.
4. The bridge degrades (`not_installed`/`timeout`) — quant absent ≠ failure for other modules.
5. Two suites stay independent; `make test-all` only runs them side by side.
6. Orders flow through `Proposal` + policy (`EXTERNAL_SEND`) — never auto-fire, fully audited.

## 8. Known gaps (track honestly)

- `SCHEMA.md` claims a column-set assertion test exists; **it does not yet** (P3).
- No root CI (`.github/workflows`) — gates are local `make test` / `make test-all`.
- `1s/15s/30s` tiers refresh only via tick→materialize, not the klines API (documented in §3.3).
- Deep `1m` backfill should use binance.vision bulk dumps, not API paging.
