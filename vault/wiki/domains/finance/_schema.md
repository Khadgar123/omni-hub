---
omni_type: domain_schema
domain: finance
schema_version: v0.21
stale_after_days: 30
---

# Finance Domain Schema

## Position

SEC filings, central-bank time-series, scholarly finance.  Data moves quarterly (10-K) or monthly (FRED); short stale threshold.

## Authoritative Sources

- `edgar`
- `fred`
- `openalex`
- `wikipedia`

## Required Frontmatter (in addition to global schema)

- `period` — data period (e.g. 2026-Q1)

## Optional Frontmatter

- `ticker` — stock ticker, e.g. NVDA
- `cik` — SEC central index key
- `fred_series_id` — FRED series identifier
- `currency` — ISO 4217 code

## Stale Threshold

`wiki-lint --rule data_gap` uses **30 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Domain-Specific Lint Hints

- stale_fact severity=high: outdated financial data is dangerous.
- broken_cross_ref on cik/ticker MUST be repaired before next ingest.

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
