---
omni_type: domain_schema
domain: fashion
schema_version: v0.21
stale_after_days: 90
---

# Fashion Domain Schema

## Position

Reactive, taste-driven domain.  Pages capture season trends, brand histories, and outfit references.  No active cascade — built from vault snapshots.

## Authoritative Sources

- `wikipedia`

## Optional Frontmatter

- `season` — e.g. SS26, FW25
- `brand` — brand name
- `price_tier` — luxury | premium | mid | budget

## Stale Threshold

`wiki-lint --rule data_gap` uses **90 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Domain-Specific Lint Hints

- season pages SHOULD be superseded each cycle; flag stale_fact aggressively.

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
