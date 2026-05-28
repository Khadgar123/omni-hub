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
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.38
omni_hub:
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
  inputs:
    query: "user question text"
    domain: "research"
    tier: "minimal | standard | expanded"
  outputs:
    context_pack: "ContextPack with cited wiki + research results"
---

<!-- omni-skill-stub: v0.38 -->

# Research — Wiki Domain Skill

This is the **research** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["research"]`.

> First (and reference) implementation of the global truth wiki母模板. Owns scholarly evidence (papers, citations, conferences).  Workflow engine lives upstream in `RipeMangoBox/ResearchFlow`; the read-only evidence vault is `RipeMangoBox/PaperBite`.  omni-hub compiles their output via wiki-ingest; it does NOT copy their notes into main repo.

## When to use

Triggers (subset):

- "调研一下 X"
  - "X 的论文 SOTA"
  - "compare these two papers"
  - "OpenReview 上 X 的评审"
  - "ICLR 2026 X 方向有哪些工作"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain research --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain research --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain research

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/research/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/research/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- research domain accepts 2-year-old facts — only flag data-gap after 730 days.
- broken_cross_ref severity=high: missing paper citations break academic trust.
- missing_concept findings on method/algorithm slugs SHOULD become new method pages.

### Severity overrides

  - `broken_cross_ref` → **high**
  - `missing_concept` → **medium**

## Required frontmatter on new pages

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
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/research.jsonl`.  `harness-compile-skill --domain
research` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.38 -->` marker line to opt out of future regenerations._
