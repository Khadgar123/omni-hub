---
name: us-policy-wiki
status: active-domain
description: |
  US federal/state policy — bills / regs / SCOTUS.

  Triggers — invoke this skill when the user asks any of:
  - "SCOTUS 2026 大案"
  - "Federal Register 最新法规"
  - "Congress 投票走向"
  - "X act 的影响"

  Source corpus: vault/wiki/domains/us-policy/.  Authoritative
  cascade: `federal_register`, `regulations_gov`, `congress_gov`, `courtlistener`, `gdelt`, `wikipedia`.  Stale threshold: 90 days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.37
omni_hub:
  kind: domain_wiki
  display_name: "US Policy — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - federal_register
    - regulations_gov
    - congress_gov
    - courtlistener
    - gdelt
    - wikipedia
  tags:
    - wiki
    - domain
    - us_policy
  inputs:
    query: "user question text"
    domain: "us_policy"
    tier: "minimal | standard | expanded"
  outputs:
    context_pack: "ContextPack with cited wiki + research results"
---

<!-- omni-skill-stub: v0.37 -->

# US Policy — Wiki Domain Skill

This is the **us_policy** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["us_policy"]`.

> US federal rules, dockets, bills, votes, Supreme Court rulings.  Per-domain cascade hits canonical .gov sources directly; secondary news (GDELT) backs context.  Quarterly update cycle.  Companion to ``cn_policy``; cross-references go through ``international_relations``.

## When to use

Triggers (subset):

- "SCOTUS 2026 大案"
  - "Federal Register 最新法规"
  - "Congress 投票走向"
  - "X act 的影响"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain us_policy --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain us_policy --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain us_policy

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/us-policy/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/us-policy/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- missing_concept on bill_id / regulation_id SHOULD become an event page.
- contradiction severity=high — policy positions across sources require resolution.
- Cross-references to cn_policy / international_relations are encouraged for trade / sanctions / treaty topics.

### Severity overrides

  - `contradiction` → **high**
  - `missing_concept` → **high**

## Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: us_policy
# optional (domain-specific)
# bill_id: ...   # Congress.gov bill number
# regulation_id: ...   # Federal Register doc number
# docket_id: ...   # regulations.gov docket id
# scotus_case: ...   # Supreme Court docket number when applicable
# jurisdiction: ...   # US-federal | US-state-XX | etc.
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/us_policy.jsonl`.  `harness-compile-skill --domain
us_policy` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.37 -->` marker line to opt out of future regenerations._
