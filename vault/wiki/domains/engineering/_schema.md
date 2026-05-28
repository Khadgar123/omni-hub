---
omni_type: domain_schema
domain: engineering
schema_version: v0.13
stale_after_days: 180
---

# Engineering Domain Schema

## Position

Software engineering, programming languages, framework evolution, system design.  Faster-moving than research — 6-month-old framework docs are likely stale; library APIs drift quarterly.

## Authoritative Sources

- `openalex`
- `arxiv`
- `wikipedia`

## Optional Frontmatter

- `github_repo` — owner/name when the page concerns a specific repo
- `language` — primary programming language
- `framework_version` — framework version at time of writing

## Stale Threshold

`wiki-lint --rule data_gap` uses **180 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Domain-Specific Lint Hints

- engineering pages tagged confidence: low for > 180d SHOULD trigger a re-ingest.
- github_repo links should be checked against current default branch.

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
