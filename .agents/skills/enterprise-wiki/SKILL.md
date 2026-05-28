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
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.19
---

<!-- omni-skill-stub: v0.19 -->

# Enterprise Analysis — Wiki Domain Skill

This is the **enterprise** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["enterprise"]`.

> 公司分析 — 团队、组织架构、投融资、产品线、产品迭代、关键人事变动. Crunchbase / LinkedIn / 财报 PDF / 招股书 are the primary sources (land in v0.22).  Per-company page is a living dossier; supersedes old quarterly snapshots via bitemporal.  Used by Application Plane for enterprise-due-diligence tasks.

## When to use

Triggers (subset):

- "OpenAI 最新组织架构"
  - "X 公司值得加入吗"
  - "分析这家公司的护城河"
  - "due diligence on Y startup"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain enterprise --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain enterprise --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain enterprise

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/enterprise/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/enterprise/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- stale_fact severity=high — outdated company info misleads investment / job decisions.
- broken_cross_ref severity=high — links to LinkedIn / Crunchbase must resolve.
- data_gap severity=high — missing quarterly update on tracked company is a real gap.

### Severity overrides

  - `broken_cross_ref` → **high**
  - `data_gap` → **high**
  - `stale_fact` → **high**

## Required frontmatter on new pages

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

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/enterprise.jsonl`.  `harness-compile-skill --domain
enterprise` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.19 -->` marker line to opt out of future regenerations._
