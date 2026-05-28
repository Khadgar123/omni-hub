---
name: travel-wiki
status: active-domain
description: |
  Itinerary / lodging / visa / seasonal timing.

  Triggers — invoke this skill when the user asks any of:
  - "东京 5 天行程"
  - "日本签证"
  - "川西自驾路线"
  - "巴厘岛雨季去合适吗"

  Source corpus: vault/wiki/domains/travel/.  Authoritative
  cascade: `xiaohongshu`, `bilibili`, `wikipedia`.  Stale threshold: 180 days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.38
omni_hub:
  kind: domain_wiki
  display_name: "Travel — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - xiaohongshu
    - bilibili
    - wikipedia
  tags:
    - wiki
    - domain
    - travel
  inputs:
    query: "user question text"
    domain: "travel"
    tier: "minimal | standard | expanded"
  outputs:
    context_pack: "ContextPack with cited wiki + research results"
---

<!-- omni-skill-stub: v0.38 -->

# Travel — Wiki Domain Skill

This is the **travel** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["travel"]`.

> Destinations, itineraries, transit, lodging, visa, seasonal timing.  Highly seasonal — Japan cherry-blossom claims valid Mar-Apr only.  Connectors land in v0.20 (小红书 + 马蜂窝 + TripAdvisor + 携程).

## When to use

Triggers (subset):

- "东京 5 天行程"
  - "日本签证"
  - "川西自驾路线"
  - "巴厘岛雨季去合适吗"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain travel --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain travel --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain travel

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/travel/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/travel/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- season + country combinations SHOULD trigger stale_fact when underlying season has passed by > 90d.
- visa / safety claims MUST cite government source (broken_cross_ref severity=high).

### Severity overrides

  - `broken_cross_ref` → **high**
  - `stale_fact` → **medium**

## Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: travel
# optional (domain-specific)
# country_iso: ...   # ISO 3166-1 alpha-3
# city: ...   # primary city / region
# trip_length_days: ...   # suggested itinerary length
# season: ...   # spring | summer | autumn | winter | year-round
# budget_tier: ...   # shoestring | mid | premium | luxury
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/travel.jsonl`.  `harness-compile-skill --domain
travel` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.38 -->` marker line to opt out of future regenerations._
