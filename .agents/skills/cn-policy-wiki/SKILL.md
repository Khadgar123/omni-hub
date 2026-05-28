---
name: cn-policy-wiki
status: active-domain
description: |
  中国政策 — 部委文件 / 五年规划 / 央行规定.

  Triggers — invoke this skill when the user asks any of:
  - "2026 中央财办文件"
  - "网信办最新规定"
  - "五年规划 X 章节"
  - "国发〔2026〕12号"

  Source corpus: vault/wiki/domains/cn-policy/.  Authoritative
  cascade: `gov_cn`, `stats_gov_cn`, `court_gov_cn`, `pbc_gov_cn`, `wikipedia`.  Stale threshold: 90 days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.38
omni_hub:
  kind: domain_wiki
  display_name: "China Policy — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - gov_cn
    - stats_gov_cn
    - court_gov_cn
    - pbc_gov_cn
    - wikipedia
  tags:
    - wiki
    - domain
    - cn_policy
  inputs:
    query: "user question text"
    domain: "cn_policy"
    tier: "minimal | standard | expanded"
  outputs:
    context_pack: "ContextPack with cited wiki + research results"
---

<!-- omni-skill-stub: v0.38 -->

# China Policy — Wiki Domain Skill

This is the **cn_policy** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["cn_policy"]`.

> 中国政策、法规、五年规划、各部委文件、中央财办、最高人民法院解释。Connectors land in v0.21 (gov.cn + 国务院 RSS + 各部委 + 央行).  Until then, pages here are populated manually via ``wiki-propose-research`` from user-curated PDF / link drops.  Companion to ``us_policy``; cross-references go through ``international_relations``.

## When to use

Triggers (subset):

- "2026 中央财办文件"
  - "网信办最新规定"
  - "五年规划 X 章节"
  - "国发〔2026〕12号"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain cn_policy --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain cn_policy --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain cn_policy

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/cn-policy/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/cn-policy/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- 中国政策更新节奏季度级;stale_after_days=90.
- contradiction severity=high — 与 us_policy 镜像;跨语言来源差异常见但必须 reconcile.
- broken_cross_ref severity=high — 文件号 / 五年规划 引用必须可解析.

### Severity overrides

  - `broken_cross_ref` → **high**
  - `contradiction` → **high**
  - `missing_concept` → **high**

## Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: cn_policy
# optional (domain-specific)
# document_id: ...   # 国务院 / 各部委文件号 (e.g. 国发〔2026〕12号)
# five_year_plan: ...   # 适用的五年规划 (e.g. 十四五 / 十五五)
# regulator: ...   # 发布机构 (国务院 / 央行 / 网信办 / 证监会 ...)
# jurisdiction: ...   # national | province-XX | municipality-XX
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/cn_policy.jsonl`.  `harness-compile-skill --domain
cn_policy` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.38 -->` marker line to opt out of future regenerations._
