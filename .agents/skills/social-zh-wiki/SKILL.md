---
name: social-zh-wiki
status: active-domain
description: |
  中文社交媒体 — 微博 / 小红书 / 公众号.

  Triggers — invoke this skill when the user asks any of:
  - "小红书最近在炒什么"
  - "微博热搜 X"
  - "公众号 X 的最新文章"

  Source corpus: vault/wiki/domains/social-zh/.  Authoritative
  cascade: `xiaohongshu`, `wechat_mp`.  Stale threshold: 14 days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.38
omni_hub:
  kind: domain_wiki
  display_name: "Social (Chinese) — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - xiaohongshu
    - wechat_mp
  tags:
    - wiki
    - domain
    - social_zh
  inputs:
    query: "user question text"
    domain: "social_zh"
    tier: "minimal | standard | expanded"
  outputs:
    context_pack: "ContextPack with cited wiki + research results"
---

<!-- omni-skill-stub: v0.38 -->

# Social (Chinese) — Wiki Domain Skill

This is the **social_zh** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["social_zh"]`.

> Tier-2 broker-routed Chinese social-media.  Xiaohongshu via jackwener/xiaohongshu-cli subprocess bridge; WeChat MP via self-hosted rachelos/we-mp-rss RSS.  Legal personal-use only, share-link parsing rather than scraping.

## When to use

Triggers (subset):

- "小红书最近在炒什么"
  - "微博热搜 X"
  - "公众号 X 的最新文章"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain social_zh --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain social_zh --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain social_zh

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/social-zh/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/social-zh/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- DO NOT propose pages from auto-scrape; only manual share-link parse.
- broken_cross_ref severity=medium when post is deleted upstream (expected).

### Severity overrides

  - `broken_cross_ref` → **medium**
  - `data_gap` → **skip**

## Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: social_zh
# optional (domain-specific)
# platform: ...   # xiaohongshu | wechat_mp | weibo | other
# post_id: ...   # platform-native ID
# author: ...   # post author handle / public account name
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/social_zh.jsonl`.  `harness-compile-skill --domain
social_zh` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.38 -->` marker line to opt out of future regenerations._
