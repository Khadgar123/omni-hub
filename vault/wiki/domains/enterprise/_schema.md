---
omni_type: domain_schema
domain: enterprise
schema_version: v0.20
stale_after_days: 90
---

# Enterprise Analysis Domain Schema

## Position

公司分析 — 团队、组织架构、投融资、产品线、产品迭代、关键人事变动. Crunchbase / LinkedIn / 财报 PDF / 招股书 are the primary sources (land in v0.22).  Per-company page is a living dossier; supersedes old quarterly snapshots via bitemporal.  Used by Application Plane for enterprise-due-diligence tasks.

## Authoritative Sources

- `crossref`
- `edgar`
- `brave_search`
- `wikidata`
- `wikipedia`

## Required Frontmatter (in addition to global schema)

- `company_id` — Crunchbase UUID or LinkedIn company slug

## Optional Frontmatter

- `ticker` — stock ticker if public
- `hq_country` — ISO 3166-1 alpha-3
- `stage` — seed | series-A..F | public | private-equity | acquired
- `vertical` — saas | fintech | bio | retail | ...
- `headcount_band` — <10 | 10-50 | 50-200 | 200-1000 | 1000+
- `funding_total_usd` — cumulative funding raised (USD)

## Stale Threshold

`wiki-lint --rule data_gap` uses **90 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Domain-Specific Lint Hints

- stale_fact severity=high — outdated company info misleads investment / job decisions.
- broken_cross_ref severity=high — links to LinkedIn / Crunchbase must resolve.
- data_gap severity=high — missing quarterly update on tracked company is a real gap.

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
