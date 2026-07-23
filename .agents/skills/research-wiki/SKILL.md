---
name: research-wiki
status: active-domain
description: |
  Scholarly research — papers / citations / venue context.

  Triggers — invoke this skill when the user asks any of:
  - "调研一下 X"
  - "X 的论文 SOTA"
  - "compare these two papers"
  - "OpenReview 上 X 的评审"
  - "ICLR 2026 X 方向有哪些工作"

  Source corpus: vault/wiki/domains/research/.  Authoritative
  cascade: `openalex`, `semantic_scholar`, `arxiv`, `wikipedia`.  Stale threshold: 730 days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write Policy" below).
license: MIT
schema_version: v0.40
omni_hub:
  layer: domain
  namespace: domain
  kind: domain_wiki
  display_name: "Research — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - openalex
    - semantic_scholar
    - arxiv
    - wikipedia
  tags:
    - wiki
    - domain
    - research
---

<!-- omni-skill-stub: v0.40 -->

# Research — Wiki Domain Skill

This is the **research** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["research"]`.

> First (and reference) implementation of the global truth wiki母模板. Owns scholarly evidence (papers, citations, conferences).  Workflow engine lives upstream in `RipeMangoBox/ResearchFlow`; the read-only evidence vault is `RipeMangoBox/PaperBite`.  omni-hub compiles their output via wiki-ingest; it does NOT copy their notes into main repo.

Every domain skill ships the v0.40 **5-section contract** — Retrieve /
Apply / Guardrails / Eval Metric / Write Policy — so reviewers can audit
each domain to the same checklist.

## 1. Retrieve Knowledge

```bash
# In-wiki query (FTS5 + substring fallback; filters superseded by default)
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Tier-bounded context bundle (minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain research --tier standard

# GraphRAG-style community probe (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

Authoritative cascade: `openalex`, `semantic_scholar`, `arxiv`, `wikipedia`.  When in doubt, default to ``tier=standard``.

## 2. Apply Knowledge

What this skill **does** with the retrieved context (the contract a
caller can rely on):

- Synthesise a cited answer to the user's question, drawing only from
  pages whose ``review_state == approved`` and ``t_valid_to`` either
  null or in the future.
- For factual claims, cite ``claim_id`` from ``.omni/claims.jsonl`` —
  callers can re-resolve via ``claims-show``.
- For methodological / procedural questions, walk the
  ``methods/`` + ``concepts/`` subfolders before falling back to
  ``syntheses/``.
- If the context pack returns empty, surface "no claims yet" rather
  than hallucinating — let the user choose to ingest more evidence
  via the section below.

## 3. Guardrails

- research domain accepts 2-year-old facts — only flag data-gap after 730 days.
- broken_cross_ref severity=high: missing paper citations break academic trust.
- missing_concept findings on method/algorithm slugs SHOULD become new method pages.

Lint severity overrides:

  - `broken_cross_ref` → **high**
  - `missing_concept` → **medium**

## 4. Eval Metric

- Composite score = Judge composite (evidence_coverage / information_density / citation_support / style_fit / uncertainty_calibration) computed by
  ``omni-hub judge-evaluate --domain research --candidate ...``.
- Per-domain rubric weights live in
  ``src/omni_hub/harness/domain_profiles.py::_DOMAIN_RUBRIC_OVERRIDES``.
- PreferenceStore at ``.omni/preference/research.jsonl`` —
  ``harness-compile-skill --domain research`` consumes this weekly
  and proposes SKILL.md body updates as DSPy 5-component artifacts.
- A/B test variants with ``omni-hub ab-test --domain research``.

## 5. Write Policy

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/research/` directly.

```bash
# 1) Cascade retrieves evidence (read-only)
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain research --persist-evidence

# 2) Bridge to a Proposal(kind=wiki_update) — humans review
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain research

# 3) Human review
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land approved Proposal → vault/wiki/domains/research/ + claims.jsonl
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>

# Retire stale claims: bitemporal close, never delete.
PYTHONPATH=src python3 -m omni_hub.cli wiki-supersede --old <id> --new <id>
```

### Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: research
# required (domain-specific)
paper_link: ...   # URL to the canonical paper (OpenReview / arXiv abs / DOI)
venue_year: ...   # Conference + year, e.g. ICLR_2026
# optional (domain-specific)
# doi: ...   # DOI when available
# methods: ...   # list of methods/algorithms the paper introduces
# topics: ...   # list of topical tags from the analysis
# core_operator: ...   # PaperBite-style one-line description of the central operator
# primary_logic: ...   # PaperBite-style one-line description of the mechanism
# orcids: ...   # author ORCIDs — from OpenAlex authorships (disambiguation)
# affiliations: ...   # author institutions + ROR ids — from OpenAlex
# paper_versions: ...   # PaperVersion list: arXiv v1/v2/.. + camera-ready {version,date,url}
# review_thread: ...   # ReviewThread: OpenReview decision + avg_rating + n_reviews (openreview.forum_thread)
# acceptance: ...   # venue decision: accepted / rejected / withdrawn / unknown (OpenReview)
# code_artifact: ...   # Artifact: GitHub stars/license/latest-release/checkpoint (github.repo_audit)
# model_artifact: ...   # Artifact: HF Hub model/dataset id + downloads (hf_hub.model_info)
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.40 -->` marker line to opt out of future regenerations._
