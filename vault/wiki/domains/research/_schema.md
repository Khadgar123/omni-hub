---
omni_type: domain_schema
domain: research
schema_version: v0.21
stale_after_days: 730
---

# Research Domain Schema

## Position

First (and reference) implementation of the global truth wiki母模板. Owns scholarly evidence (papers, citations, conferences).  Workflow engine lives upstream in `RipeMangoBox/ResearchFlow`; the read-only evidence vault is `RipeMangoBox/PaperBite`.  omni-hub compiles their output via wiki-ingest; it does NOT copy their notes into main repo.

## Authoritative Sources

- `openalex`
- `semantic_scholar`
- `arxiv`
- `wikipedia`

## Required Frontmatter (in addition to global schema)

- `paper_link` — URL to the canonical paper (OpenReview / arXiv abs / DOI)
- `venue_year` — Conference + year, e.g. ICLR_2026

## Optional Frontmatter

- `doi` — DOI when available
- `methods` — list of methods/algorithms the paper introduces
- `topics` — list of topical tags from the analysis
- `core_operator` — PaperBite-style one-line description of the central operator
- `primary_logic` — PaperBite-style one-line description of the mechanism
- `orcids` — author ORCIDs — from OpenAlex authorships (disambiguation)
- `affiliations` — author institutions + ROR ids — from OpenAlex
- `paper_versions` — PaperVersion list: arXiv v1/v2/.. + camera-ready {version,date,url}
- `review_thread` — ReviewThread: OpenReview decision + avg_rating + n_reviews (openreview.forum_thread)
- `acceptance` — venue decision: accepted / rejected / withdrawn / unknown (OpenReview)
- `code_artifact` — Artifact: GitHub stars/license/latest-release/checkpoint (github.repo_audit)
- `model_artifact` — Artifact: HF Hub model/dataset id + downloads (hf_hub.model_info)

## Stale Threshold

`wiki-lint --rule data_gap` uses **730 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Pinned References

- agent-harness/researchflow (upstream: RipeMangoBox/ResearchFlow)
- agent-harness/paperbite (upstream: RipeMangoBox/PaperBite)

## Domain-Specific Lint Hints

- research domain accepts 2-year-old facts — only flag data-gap after 730 days.
- broken_cross_ref severity=high: missing paper citations break academic trust.
- missing_concept findings on method/algorithm slugs SHOULD become new method pages.

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
