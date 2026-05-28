---
name: fashion-wiki
status: active-domain
description: |
  Outfit / season / fit / budget recommendations.

  Triggers — invoke this skill when the user asks any of:
  - "春季商务休闲穿搭"
  - "婚礼伴郎西装预算 3k"
  - "SS26 趋势"
  - "怎么搭配 oversized 衬衫"

  Source corpus: vault/wiki/domains/fashion/.  Authoritative
  cascade: `wikipedia`.  Stale threshold: 90 days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.37
omni_hub:
  kind: domain_wiki
  display_name: "Fashion — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - wikipedia
  tags:
    - wiki
    - domain
    - fashion
  inputs:
    query: "user question text"
    domain: "fashion"
    tier: "minimal | standard | expanded"
  outputs:
    context_pack: "ContextPack with cited wiki + research results"
---

<!-- omni-skill-stub: v0.37 -->

# Fashion — Wiki Domain Skill

This is the **fashion** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["fashion"]`.

> Reactive, taste-driven domain.  Pages capture season trends, brand histories, and outfit references.  No active cascade — built from vault snapshots.

## When to use

Triggers (subset):

- "春季商务休闲穿搭"
  - "婚礼伴郎西装预算 3k"
  - "SS26 趋势"
  - "怎么搭配 oversized 衬衫"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain fashion --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain fashion --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain fashion

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/fashion/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/fashion/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- season pages SHOULD be superseded each cycle; flag stale_fact aggressively.

### Severity overrides

  - `data_gap` → **skip**
  - `stale_fact` → **high**

## Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: fashion
# optional (domain-specific)
# season: ...   # e.g. SS26, FW25
# brand: ...   # brand name
# price_tier: ...   # luxury | premium | mid | budget
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/fashion.jsonl`.  `harness-compile-skill --domain
fashion` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.37 -->` marker line to opt out of future regenerations._
