---
name: international-relations-wiki
status: active-domain
description: |
  Cross-border events / actors / scenarios.

  Triggers — invoke this skill when the user asks any of:
  - "中美关系最新"
  - "俄乌局势"
  - "台海动态"
  - "OPEC 决议"

  Source corpus: vault/wiki/domains/international-relations/.  Authoritative
  cascade: `acled`, `gdelt`, `world_bank`, `imf`, `wikipedia`.  Stale threshold: 7 days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.38
omni_hub:
  kind: domain_wiki
  display_name: "International Relations — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - acled
    - gdelt
    - world_bank
    - imf
    - wikipedia
  tags:
    - wiki
    - domain
    - international_relations
  inputs:
    query: "user question text"
    domain: "international_relations"
    tier: "minimal | standard | expanded"
  outputs:
    context_pack: "ContextPack with cited wiki + research results"
---

<!-- omni-skill-stub: v0.38 -->

# International Relations — Wiki Domain Skill

This is the **international_relations** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["international_relations"]`.

> Cross-border events, conflicts, multilateral data.  Highest velocity domain — daily news cycle, weekly stale threshold.

## When to use

Triggers (subset):

- "中美关系最新"
  - "俄乌局势"
  - "台海动态"
  - "OPEC 决议"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain international_relations --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain international_relations --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain international_relations

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/international-relations/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/international-relations/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- stale_fact severity=high — IR pages decay in days.
- contradiction frequent and EXPECTED — multiple narrative sources are the norm.

### Severity overrides

  - `contradiction` → **low**
  - `data_gap` → **high**
  - `stale_fact` → **high**

## Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: international_relations
# optional (domain-specific)
# country_iso: ...   # ISO 3166-1 alpha-3 country code(s)
# event_date: ...   # ISO date of the underlying event
# conflict_type: ...   # ACLED event_type if relevant
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/international_relations.jsonl`.  `harness-compile-skill --domain
international_relations` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.38 -->` marker line to opt out of future regenerations._
