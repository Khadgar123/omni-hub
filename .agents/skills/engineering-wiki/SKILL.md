---
name: engineering-wiki
status: active-domain
description: |
  Software engineering — stack traces, frameworks, refactors.

  Triggers — invoke this skill when the user asks any of:
  - "这个 stack trace 是什么意思"
  - "X 框架的 idiomatic 写法"
  - "refactor this module"
  - "为什么 test 挂了"

  Source corpus: vault/wiki/domains/engineering/.  Authoritative
  cascade: `openalex`, `arxiv`, `wikipedia`.  Stale threshold: 180 days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.38
omni_hub:
  kind: domain_wiki
  display_name: "Engineering — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - openalex
    - arxiv
    - wikipedia
  tags:
    - wiki
    - domain
    - engineering
  inputs:
    query: "user question text"
    domain: "engineering"
    tier: "minimal | standard | expanded"
  outputs:
    context_pack: "ContextPack with cited wiki + research results"
---

<!-- omni-skill-stub: v0.38 -->

# Engineering — Wiki Domain Skill

This is the **engineering** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["engineering"]`.

> Software engineering, programming languages, framework evolution, system design.  Faster-moving than research — 6-month-old framework docs are likely stale; library APIs drift quarterly.

## When to use

Triggers (subset):

- "这个 stack trace 是什么意思"
  - "X 框架的 idiomatic 写法"
  - "refactor this module"
  - "为什么 test 挂了"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain engineering --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain engineering --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain engineering

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/engineering/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/engineering/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- engineering pages tagged confidence: low for > 180d SHOULD trigger a re-ingest.
- github_repo links should be checked against current default branch.

### Severity overrides

  - `data_gap` → **medium**

## Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: engineering
# optional (domain-specific)
# github_repo: ...   # owner/name when the page concerns a specific repo
# language: ...   # primary programming language
# framework_version: ...   # framework version at time of writing
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/engineering.jsonl`.  `harness-compile-skill --domain
engineering` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.38 -->` marker line to opt out of future regenerations._
