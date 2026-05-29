---
omni_type: domain_schema
domain: ai_progress
schema_version: v0.21
stale_after_days: 14
---

# AI Progress Domain Schema

## Position

Frontier AI model / paper / release tracking.  Velocity higher than research overall — weekly-ish refresh.

## Authoritative Sources

- `hf_daily_papers`
- `arxiv`
- `openalex`
- `wikipedia`

## Optional Frontmatter

- `arxiv_id` — e.g. 2510.04618
- `hf_paper_url` — HuggingFace Daily Papers URL
- `model_family` — e.g. Claude / GPT / Gemini / Llama
- `model_version` — specific release version

## Stale Threshold

`wiki-lint --rule data_gap` uses **14 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Domain-Specific Lint Hints

- stale threshold = 14d (AI progress moves faster than classic research).
- missing_concept on model_family slugs SHOULD become entity pages.

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
