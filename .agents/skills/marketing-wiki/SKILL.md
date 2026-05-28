---
name: marketing-wiki
status: active-domain
description: |
  Playbooks / channel mix / ROI case studies.

  Triggers — invoke this skill when the user asks any of:
  - "SaaS 早期增长 playbook"
  - "小红书 投放经验"
  - "how to write better ad copy"
  - "漏斗优化案例"

  Source corpus: vault/wiki/domains/marketing/.  Authoritative
  cascade: `weibo`, `brave_search`, `gdelt`, `zhihu`, `wikipedia`.  Stale threshold: 60 days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.37
omni_hub:
  kind: domain_wiki
  display_name: "Marketing & Promotion — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - weibo
    - brave_search
    - gdelt
    - zhihu
    - wikipedia
  tags:
    - wiki
    - domain
    - marketing
  inputs:
    query: "user question text"
    domain: "marketing"
    tier: "minimal | standard | expanded"
  outputs:
    context_pack: "ContextPack with cited wiki + research results"
---

<!-- omni-skill-stub: v0.37 -->

# Marketing & Promotion — Wiki Domain Skill

This is the **marketing** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["marketing"]`.

> 营销策略、文案模式、增长黑客、品牌定位、ROI 案例.  Fast cycle — weekly trending playbook shifts.  Connectors land in v0.20 (微博热搜 + 抖音 + 营销博主 RSS) + v0.22 (Crunchbase 增长案例).

## When to use

Triggers (subset):

- "SaaS 早期增长 playbook"
  - "小红书 投放经验"
  - "how to write better ad copy"
  - "漏斗优化案例"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain marketing --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain marketing --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain marketing

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/marketing/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/marketing/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- case studies > 6mo old SHOULD trigger stale_fact (severity=medium).
- ROI claims without source severity=high (broken_cross_ref).

### Severity overrides

  - `broken_cross_ref` → **high**
  - `stale_fact` → **medium**

## Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: marketing
# optional (domain-specific)
# channel: ...   # social | seo | content | email | paid-ads | influencer
# industry: ...   # saas | consumer | b2b | retail | fintech | ...
# case_company: ...   # company the case study is about
# roi_metric: ...   # the metric this playbook claims to move
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/marketing.jsonl`.  `harness-compile-skill --domain
marketing` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.37 -->` marker line to opt out of future regenerations._
