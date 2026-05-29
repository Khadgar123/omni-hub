---
omni_type: domain_schema
domain: fitness_wellness
schema_version: v0.20
stale_after_days: 365
---

# Fitness & Wellness Domain Schema

## Position

健身、营养、康复、睡眠、心理健康。 RCT-backed claims preferred; Bilibili / Instagram 健身博主 claims need supporting study links or are marked confidence: low.  Connectors land in v0.20 (PubMed + Bilibili).  High guard against pseudo-science: lint hints require RCT / meta-analysis citation for any 'do X to achieve Y' claim.

## Authoritative Sources

- `pubmed`
- `europe_pmc`
- `bilibili`
- `wikipedia`

## Optional Frontmatter

- `modality` — strength | hypertrophy | cardio | mobility | nutrition | sleep | mental
- `evidence_grade` — RCT | meta-analysis | observational | expert-opinion | n=1
- `rct_link` — DOI of supporting RCT when claim is causal

## Stale Threshold

`wiki-lint --rule data_gap` uses **365 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Domain-Specific Lint Hints

- evidence_grade frontmatter REQUIRED for any 'do X to achieve Y' claim.
- missing_concept severity=high on supplement / drug names — must link to safety profile.
- contradiction severity=high — fitness folklore frequently contradicts trials.

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
