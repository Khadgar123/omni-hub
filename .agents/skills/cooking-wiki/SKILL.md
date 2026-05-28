---
name: cooking-wiki
status: active-domain
description: |
  Recipes / techniques / substitutions.

  Triggers — invoke this skill when the user asks any of:
  - "今晚做什么"
  - "红烧肉怎么做"
  - "麻婆豆腐的关键步骤"
  - "how do I temper chocolate"

  Source corpus: vault/wiki/domains/cooking/.  Authoritative
  cascade: `xiaohongshu`, `bilibili`, `wikipedia`.  Stale threshold: 730 days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.37
omni_hub:
  kind: domain_wiki
  display_name: "Cooking — Wiki Domain Skill"
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
    - cooking
  inputs:
    query: "user question text"
    domain: "cooking"
    tier: "minimal | standard | expanded"
  outputs:
    context_pack: "ContextPack with cited wiki + research results"
---

<!-- omni-skill-stub: v0.37 -->

# Cooking — Wiki Domain Skill

This is the **cooking** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["cooking"]`.

> 中餐 / 西餐 / 烘焙 / 发酵 / 食材保鲜.  Receptive domain: 小红书 + Bilibili 美食 + 下厨房 + Allrecipes (英文) provide candidate recipes; user feedback (complete-and-rate) drives the PreferenceStore.  Connectors land in v0.20.  Each recipe page tracks substitutions + per-step constraints.

## When to use

Triggers (subset):

- "今晚做什么"
  - "红烧肉怎么做"
  - "麻婆豆腐的关键步骤"
  - "how do I temper chocolate"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain cooking --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain cooking --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain cooking

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/cooking/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/cooking/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- Recipe pages SHOULD link to at least one source video / blog (broken_cross_ref severity=low).
- data_gap severity=low — cooking knowledge is durable.

### Severity overrides

  - `broken_cross_ref` → **low**
  - `data_gap` → **low**

## Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: cooking
# optional (domain-specific)
# cuisine: ...   # chinese-sichuan | chinese-cantonese | italian | japanese | thai | ...
# technique: ...   # braise | stir-fry | bake | ferment | sous-vide | ...
# difficulty: ...   # beginner | intermediate | advanced
# time_active_min: ...   # active cooking time in minutes
# time_total_min: ...   # total time including waiting
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/cooking.jsonl`.  `harness-compile-skill --domain
cooking` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.37 -->` marker line to opt out of future regenerations._
