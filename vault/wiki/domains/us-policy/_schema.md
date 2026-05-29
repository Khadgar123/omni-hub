---
omni_type: domain_schema
domain: us_policy
schema_version: v0.21
stale_after_days: 90
---

# US Policy Domain Schema

## Position

US federal rules, dockets, bills, votes, Supreme Court rulings.  Per-domain cascade hits canonical .gov sources directly; secondary news (GDELT) backs context.  Quarterly update cycle.  Companion to ``cn_policy``; cross-references go through ``international_relations``.

## Authoritative Sources

- `federal_register`
- `regulations_gov`
- `congress_gov`
- `courtlistener`
- `gdelt`
- `wikipedia`

## Optional Frontmatter

- `bill_id` — Congress.gov bill number
- `regulation_id` — Federal Register doc number
- `docket_id` — regulations.gov docket id
- `scotus_case` — Supreme Court docket number when applicable
- `jurisdiction` — US-federal | US-state-XX | etc.

## Stale Threshold

`wiki-lint --rule data_gap` uses **90 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Domain-Specific Lint Hints

- missing_concept on bill_id / regulation_id SHOULD become an event page.
- contradiction severity=high — policy positions across sources require resolution.
- Cross-references to cn_policy / international_relations are encouraged for trade / sanctions / treaty topics.

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
