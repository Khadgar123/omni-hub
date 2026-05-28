---
name: agent-systems-wiki
status: active-domain
description: |
  Agent frameworks / SDKs / BUILD-vs-USE decisions.

  Triggers — invoke this skill when the user asks any of:
  - "Letta vs Mem0 怎么选"
  - "DSPy GEPA 真的有用吗"
  - "OpenHands worker 怎么部署"
  - "应该 fork 还是 pin"

  Source corpus: vault/wiki/domains/agent-systems/.  Authoritative
  cascade: `wikipedia`, `openalex`, `gdelt`.  Stale threshold: 30 days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.38
omni_hub:
  kind: domain_wiki
  display_name: "Agent Systems — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - wikipedia
    - openalex
    - gdelt
  tags:
    - wiki
    - domain
    - agent_systems
  inputs:
    query: "user question text"
    domain: "agent_systems"
    tier: "minimal | standard | expanded"
  outputs:
    context_pack: "ContextPack with cited wiki + research results"
---

<!-- omni-skill-stub: v0.38 -->

# Agent Systems — Wiki Domain Skill

This is the **agent_systems** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["agent_systems"]`.

> Agent frameworks, SDKs, harness modules.  Pages here document the BUILD-vs-USE decisions and the pinned forks under `agent-harness/`.

## When to use

Triggers (subset):

- "Letta vs Mem0 怎么选"
  - "DSPy GEPA 真的有用吗"
  - "OpenHands worker 怎么部署"
  - "应该 fork 还是 pin"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain agent_systems --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain agent_systems --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain agent_systems

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/agent-systems/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/agent-systems/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- decision field MUST be one of the BUILD-vs-USE template enum values.
- broken_cross_ref severity=high — pinned forks must exist as submodules.

### Severity overrides

  - `broken_cross_ref` → **high**
  - `data_gap` → **medium**

## Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: agent_systems
# optional (domain-specific)
# framework: ...   # framework name (Letta / DSPy / Graphiti / etc.)
# version: ...   # version pinned in agent-harness
# decision: ...   # BUILD | USE | PIN-AS-FORK | DEFER | REJECT
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/agent_systems.jsonl`.  `harness-compile-skill --domain
agent_systems` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.38 -->` marker line to opt out of future regenerations._
