---
name: photography-wiki
status: active-domain
description: |
  Visual decisions — light / lens / composition / edit.

  Triggers — invoke this skill when the user asks any of:
  - "街拍 35mm 还是 50mm"
  - "光圈优先 vs 快门优先"
  - "Lightroom 风格分析"
  - "how to expose for shadows in raw"

  Source corpus: vault/wiki/domains/photography/.  Authoritative
  cascade: `unsplash`, `pexels`, `wikipedia`.  Stale threshold: 365 days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.19
---

<!-- omni-skill-stub: v0.19 -->

# Photography — Wiki Domain Skill

This is the **photography** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["photography"]`.

> Reactive domain — content comes from user-forwarded links, not active ingest.  Wiki pages here are mostly portfolio notes, technique references, and gear comparisons.

## When to use

Triggers (subset):

- "街拍 35mm 还是 50mm"
  - "光圈优先 vs 快门优先"
  - "Lightroom 风格分析"
  - "how to expose for shadows in raw"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain photography --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain photography --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain photography

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/photography/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/photography/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- missing attribution = automatic broken_cross_ref severity high.
- low data-gap pressure — photography knowledge ages slowly.

### Severity overrides

  - `broken_cross_ref` → **high**
  - `data_gap` → **low**

## Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: photography
# required (domain-specific)
attribution: ...   # photographer credit + license (CC0, CC-BY, etc.)
# optional (domain-specific)
# camera_body: ...   # e.g. Sony α7 IV
# lens: ...   # lens used
# style_tags: ...   # list of style descriptors
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/photography.jsonl`.  `harness-compile-skill --domain
photography` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.19 -->` marker line to opt out of future regenerations._
