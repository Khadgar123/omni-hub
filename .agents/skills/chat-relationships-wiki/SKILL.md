---
name: chat-relationships-wiki
status: active-domain
description: |
  Conversational nuance + privacy-safe relationship context.

  Triggers — invoke this skill when the user asks any of:
  - "这条消息该怎么回"
  - "老板说 X 是什么意思"
  - "how to set this boundary"
  - "朋友冷战了怎么办"

  Source corpus: vault/wiki/domains/chat-relationships/.  Authoritative
  cascade: _(reactive — no cascade by default)_.  Stale threshold: 180 days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.19
---

<!-- omni-skill-stub: v0.19 -->

# Chat & Relationships — Wiki Domain Skill

This is the **chat_relationships** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["chat_relationships"]`.

> Purely reactive — no cascade hits.  Pages capture conversational patterns, social mappings, and shared context.  All ingest is via manual `wiki-propose-research` or `wiki-log --op manual`.

## When to use

Triggers (subset):

- "这条消息该怎么回"
  - "老板说 X 是什么意思"
  - "how to set this boundary"
  - "朋友冷战了怎么办"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain chat_relationships --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain chat_relationships --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain chat_relationships

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/chat-relationships/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/chat-relationships/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- data_gap is informational only — chat context decays naturally.
- missing_concept findings here often map to entity pages (people / roles).

### Severity overrides

  - `data_gap` → **skip**
  - `stale_fact` → **low**

## Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: chat_relationships
# optional (domain-specific)
# participants: ...   # list of named participants or roles
# context_window: ...   # time range the page covers
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/chat_relationships.jsonl`.  `harness-compile-skill --domain
chat_relationships` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.19 -->` marker line to opt out of future regenerations._
