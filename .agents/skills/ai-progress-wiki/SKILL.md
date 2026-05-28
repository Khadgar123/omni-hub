---
name: ai-progress-wiki
status: active-domain
description: |
  Frontier AI — models, papers, releases.

  Triggers — invoke this skill when the user asks any of:
  - "Claude 4.7 有什么新特性"
  - "DSPy 3 怎么用"
  - "GPT-5 / Gemini 3 / Llama 5 对比"
  - "Anthropic Skills 怎么写"

  Source corpus: vault/wiki/domains/ai-progress/.  Authoritative
  cascade: `hf_daily_papers`, `arxiv`, `openalex`, `wikipedia`.  Stale threshold: 14 days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.37
omni_hub:
  kind: domain_wiki
  display_name: "AI Progress — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - hf_daily_papers
    - arxiv
    - openalex
    - wikipedia
  tags:
    - wiki
    - domain
    - ai_progress
  inputs:
    query: "user question text"
    domain: "ai_progress"
    tier: "minimal | standard | expanded"
  outputs:
    context_pack: "ContextPack with cited wiki + research results"
---

<!-- omni-skill-stub: v0.37 -->

# AI Progress — Wiki Domain Skill

This is the **ai_progress** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["ai_progress"]`.

> Frontier AI model / paper / release tracking.  Velocity higher than research overall — weekly-ish refresh.

## When to use

Triggers (subset):

- "Claude 4.7 有什么新特性"
  - "DSPy 3 怎么用"
  - "GPT-5 / Gemini 3 / Llama 5 对比"
  - "Anthropic Skills 怎么写"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain ai_progress --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain ai_progress --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain ai_progress

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/ai-progress/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/ai-progress/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- stale threshold = 14d (AI progress moves faster than classic research).
- missing_concept on model_family slugs SHOULD become entity pages.

### Severity overrides

  - `data_gap` → **medium**
  - `missing_concept` → **high**

## Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: ai_progress
# optional (domain-specific)
# arxiv_id: ...   # e.g. 2510.04618
# hf_paper_url: ...   # HuggingFace Daily Papers URL
# model_family: ...   # e.g. Claude / GPT / Gemini / Llama
# model_version: ...   # specific release version
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/ai_progress.jsonl`.  `harness-compile-skill --domain
ai_progress` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.37 -->` marker line to opt out of future regenerations._
