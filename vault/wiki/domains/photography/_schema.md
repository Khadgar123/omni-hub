---
omni_type: domain_schema
domain: photography
schema_version: v0.21
stale_after_days: 365
---

# Photography Domain Schema

## Position

Reactive domain — content comes from user-forwarded links, not active ingest.  Wiki pages here are mostly portfolio notes, technique references, and gear comparisons.

## Authoritative Sources

- `unsplash`
- `pexels`
- `wikipedia`

## Required Frontmatter (in addition to global schema)

- `attribution` — photographer credit + license (CC0, CC-BY, etc.)

## Optional Frontmatter

- `camera_body` — e.g. Sony α7 IV
- `lens` — lens used
- `style_tags` — list of style descriptors

## Stale Threshold

`wiki-lint --rule data_gap` uses **365 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Domain-Specific Lint Hints

- missing attribution = automatic broken_cross_ref severity high.
- low data-gap pressure — photography knowledge ages slowly.

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
