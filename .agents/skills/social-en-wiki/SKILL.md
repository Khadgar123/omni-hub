---
name: social-en-wiki
status: active-domain
description: |
  English social media — Twitter / Reddit / HN.

  Triggers — invoke this skill when the user asks any of:
  - "这条 tweet 火了"
  - "HN 在讨论 X"
  - "Reddit r/X 的态度"

  Source corpus: vault/wiki/domains/social-en/.  Authoritative
  cascade: `x_twitter`, `gdelt`.  Stale threshold: 14 days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.37
omni_hub:
  kind: domain_wiki
  display_name: "Social (English) — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - x_twitter
    - gdelt
  tags:
    - wiki
    - domain
    - social_en
  inputs:
    query: "user question text"
    domain: "social_en"
    tier: "minimal | standard | expanded"
  outputs:
    context_pack: "ContextPack with cited wiki + research results"
---

<!-- omni-skill-stub: v0.37 -->

# Social (English) — Wiki Domain Skill

This is the **social_en** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["social_en"]`.

> Tier-2 paid/broker social-media domain.  Opt-in only — no default cascade hit.  twitterapi.io paid lane.  Reactive: pages mostly from user-shared links + GDELT news context.

## When to use

Triggers (subset):

- "这条 tweet 火了"
  - "HN 在讨论 X"
  - "Reddit r/X 的态度"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain social_en --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain social_en --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain social_en

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/social-en/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/social-en/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- data_gap is expected — social pages reflect a moment, not a process.
- missing attribution / post_id = broken_cross_ref severity=medium.

### Severity overrides

  - `broken_cross_ref` → **medium**
  - `data_gap` → **skip**

## Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: social_en
# optional (domain-specific)
# platform: ...   # x | reddit | hn | other
# post_id: ...   # platform-native ID
# author: ...   # post author handle
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/social_en.jsonl`.  `harness-compile-skill --domain
social_en` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.37 -->` marker line to opt out of future regenerations._
