---
omni_type: domain_schema
domain: international_relations
schema_version: v0.20
stale_after_days: 7
---

# International Relations Domain Schema

## Position

Cross-border events, conflicts, multilateral data.  Highest velocity domain — daily news cycle, weekly stale threshold.

## Authoritative Sources

- `acled`
- `gdelt`
- `world_bank`
- `imf`
- `wikipedia`

## Optional Frontmatter

- `country_iso` — ISO 3166-1 alpha-3 country code(s)
- `event_date` — ISO date of the underlying event
- `conflict_type` — ACLED event_type if relevant

## Stale Threshold

`wiki-lint --rule data_gap` uses **7 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Domain-Specific Lint Hints

- stale_fact severity=high — IR pages decay in days.
- contradiction frequent and EXPECTED — multiple narrative sources are the norm.

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
