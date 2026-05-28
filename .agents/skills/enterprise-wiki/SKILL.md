---
name: enterprise-wiki
status: active-domain
description: |
  Per-company dossiers — team / funding / product / changes.

  Triggers — invoke this skill when the user asks any of:
  - "OpenAI 最新组织架构"
  - "X 公司值得加入吗"
  - "分析这家公司的护城河"
  - "due diligence on Y startup"

  Source corpus: vault/wiki/domains/enterprise/.  Authoritative
  cascade: `crossref`, `edgar`, `brave_search`, `wikidata`, `wikipedia`.  Stale threshold: 90 days.

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
  display_name: "Enterprise Analysis — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - crossref
    - edgar
    - brave_search
    - wikidata
    - wikipedia
  tags:
    - wiki
    - domain
    - enterprise
---

<!-- omni-skill-stub: v0.40 -->

# Enterprise Analysis — Wiki Domain Skill

This is the **enterprise** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["enterprise"]`.

> 公司分析 — 团队、组织架构、投融资、产品线、产品迭代、关键人事变动. Crunchbase / LinkedIn / 财报 PDF / 招股书 are the primary sources (land in v0.22).  Per-company page is a living dossier; supersedes old quarterly snapshots via bitemporal.  Used by Application Plane for enterprise-due-diligence tasks.

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
  --query "..." --domain enterprise --tier standard

# GraphRAG-style community probe (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

Authoritative cascade: `crossref`, `edgar`, `brave_search`, `wikidata`, `wikipedia`.  When in doubt, default to ``tier=standard``.

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

- stale_fact severity=high — outdated company info misleads investment / job decisions.
- broken_cross_ref severity=high — links to LinkedIn / Crunchbase must resolve.
- data_gap severity=high — missing quarterly update on tracked company is a real gap.

Lint severity overrides:

  - `broken_cross_ref` → **high**
  - `data_gap` → **high**
  - `stale_fact` → **high**

## 4. Eval Metric

- Composite score = Judge composite (evidence_coverage / information_density / citation_support / style_fit / uncertainty_calibration) computed by
  ``omni-hub judge-evaluate --domain enterprise --candidate ...``.
- Per-domain rubric weights live in
  ``src/omni_hub/harness/domain_profiles.py::_DOMAIN_RUBRIC_OVERRIDES``.
- PreferenceStore at ``.omni/preference/enterprise.jsonl`` —
  ``harness-compile-skill --domain enterprise`` consumes this weekly
  and proposes SKILL.md body updates as DSPy 5-component artifacts.
- A/B test variants with ``omni-hub ab-test --domain enterprise``.

## 5. Write Policy

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/enterprise/` directly.

```bash
# 1) Cascade retrieves evidence (read-only)
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain enterprise --persist-evidence

# 2) Bridge to a Proposal(kind=wiki_update) — humans review
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain enterprise

# 3) Human review
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land approved Proposal → vault/wiki/domains/enterprise/ + claims.jsonl
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>

# Retire stale claims: bitemporal close, never delete.
PYTHONPATH=src python3 -m omni_hub.cli wiki-supersede --old <id> --new <id>
```

### Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: enterprise
# required (domain-specific)
company_id: ...   # Crunchbase UUID or LinkedIn company slug
# optional (domain-specific)
# ticker: ...   # stock ticker if public
# hq_country: ...   # ISO 3166-1 alpha-3
# stage: ...   # seed | series-A..F | public | private-equity | acquired
# vertical: ...   # saas | fintech | bio | retail | ...
# headcount_band: ...   # <10 | 10-50 | 50-200 | 200-1000 | 1000+
# funding_total_usd: ...   # cumulative funding raised (USD)
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.40 -->` marker line to opt out of future regenerations._
