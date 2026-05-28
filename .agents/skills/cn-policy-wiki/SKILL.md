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
  Proposal[T] (see "Write Policy" below).
license: MIT
schema_version: v0.40
omni_hub:
  layer: domain
  namespace: domain
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
---

<!-- omni-skill-stub: v0.40 -->

# China Policy — Wiki Domain Skill

This is the **cn_policy** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["cn_policy"]`.

> 中国政策、法规、五年规划、各部委文件、中央财办、最高人民法院解释。Connectors land in v0.21 (gov.cn + 国务院 RSS + 各部委 + 央行).  Until then, pages here are populated manually via ``wiki-propose-research`` from user-curated PDF / link drops.  Companion to ``us_policy``; cross-references go through ``international_relations``.

Every domain skill ships the v0.40 **5-section contract** — Retrieve /
Apply / Guardrails / Eval Metric / Write Policy — so reviewers can audit
each domain to the same checklist.

## 1. Retrieve Knowledge

```bash
# In-wiki query (FTS5 + substring fallback; filters superseded by default)
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Tier-bounded context bundle (minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain cn_policy --tier standard

# GraphRAG-style community probe (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

Authoritative cascade: `gov_cn`, `stats_gov_cn`, `court_gov_cn`, `pbc_gov_cn`, `wikipedia`.  When in doubt, default to ``tier=standard``.

## 2. Apply Knowledge

What this skill **does** with the retrieved context (the contract a
caller can rely on):

- Synthesise a cited answer to the user's question, drawing only from
  pages whose ``review_state == approved`` and ``t_valid_to`` either
  null or in the future.
- For factual claims, cite ``claim_id`` from ``.omni/claims.jsonl`` —
  callers can re-resolve via ``claims-show``.
- For methodological / procedural questions, walk the
  ``methods/`` + ``concepts/`` subfolders before falling back to
  ``syntheses/``.
- If the context pack returns empty, surface "no claims yet" rather
  than hallucinating — let the user choose to ingest more evidence
  via the section below.

## 3. Guardrails

- 中国政策更新节奏季度级;stale_after_days=90.
- contradiction severity=high — 与 us_policy 镜像;跨语言来源差异常见但必须 reconcile.
- broken_cross_ref severity=high — 文件号 / 五年规划 引用必须可解析.

Lint severity overrides:

  - `broken_cross_ref` → **high**
  - `contradiction` → **high**
  - `missing_concept` → **high**

## 4. Eval Metric

- Composite score = Judge composite (evidence_coverage / information_density / citation_support / style_fit / uncertainty_calibration) computed by
  ``omni-hub judge-evaluate --domain cn_policy --candidate ...``.
- Per-domain rubric weights live in
  ``src/omni_hub/harness/domain_profiles.py::_DOMAIN_RUBRIC_OVERRIDES``.
- PreferenceStore at ``.omni/preference/cn_policy.jsonl`` —
  ``harness-compile-skill --domain cn_policy`` consumes this weekly
  and proposes SKILL.md body updates as DSPy 5-component artifacts.
- A/B test variants with ``omni-hub ab-test --domain cn_policy``.

## 5. Write Policy

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/cn-policy/` directly.

```bash
# 1) Cascade retrieves evidence (read-only)
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain cn_policy --persist-evidence

# 2) Bridge to a Proposal(kind=wiki_update) — humans review
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain cn_policy

# 3) Human review
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land approved Proposal → vault/wiki/domains/cn-policy/ + claims.jsonl
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>

# Retire stale claims: bitemporal close, never delete.
PYTHONPATH=src python3 -m omni_hub.cli wiki-supersede --old <id> --new <id>
```

### Required frontmatter on new pages

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

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.40 -->` marker line to opt out of future regenerations._
