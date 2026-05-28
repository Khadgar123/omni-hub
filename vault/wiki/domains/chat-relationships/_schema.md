---
omni_type: domain_schema
domain: chat_relationships
schema_version: v0.13
stale_after_days: 180
---

# Chat & Relationships Domain Schema

## Position

Purely reactive — no cascade hits.  Pages capture conversational patterns, social mappings, and shared context.  All ingest is via manual `wiki-propose-research` or `wiki-log --op manual`.

## Authoritative Sources

- (none — purely reactive domain; manual ingest only)

## Optional Frontmatter

- `participants` — list of named participants or roles
- `context_window` — time range the page covers

## Stale Threshold

`wiki-lint --rule data_gap` uses **180 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Domain-Specific Lint Hints

- data_gap is informational only — chat context decays naturally.
- missing_concept findings here often map to entity pages (people / roles).

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
