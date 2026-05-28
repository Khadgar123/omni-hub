---
name: engineering-wiki
status: active-domain
description: |
  Software engineering — stack traces, frameworks, refactors.

  Triggers — invoke this skill when the user asks any of:
  - "这个 stack trace 是什么意思"
  - "X 框架的 idiomatic 写法"
  - "refactor this module"
  - "为什么 test 挂了"

  Source corpus: vault/wiki/domains/engineering/.  Authoritative
  cascade: `openalex`, `arxiv`, `wikipedia`.  Stale threshold: 180 days.

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
  display_name: "Engineering — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - openalex
    - arxiv
    - wikipedia
  tags:
    - wiki
    - domain
    - engineering
---

<!-- omni-skill-stub: v0.40 -->

# Engineering — Wiki Domain Skill

This is the **engineering** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["engineering"]`.

> Software engineering, programming languages, framework evolution, system design.  Faster-moving than research — 6-month-old framework docs are likely stale; library APIs drift quarterly.

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
  --query "..." --domain engineering --tier standard

# GraphRAG-style community probe (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

Authoritative cascade: `openalex`, `arxiv`, `wikipedia`.  When in doubt, default to ``tier=standard``.

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

- engineering pages tagged confidence: low for > 180d SHOULD trigger a re-ingest.
- github_repo links should be checked against current default branch.

Lint severity overrides:

  - `data_gap` → **medium**

## 4. Eval Metric

- Composite score = Judge composite (evidence_coverage / information_density / citation_support / style_fit / uncertainty_calibration) computed by
  ``omni-hub judge-evaluate --domain engineering --candidate ...``.
- Per-domain rubric weights live in
  ``src/omni_hub/harness/domain_profiles.py::_DOMAIN_RUBRIC_OVERRIDES``.
- PreferenceStore at ``.omni/preference/engineering.jsonl`` —
  ``harness-compile-skill --domain engineering`` consumes this weekly
  and proposes SKILL.md body updates as DSPy 5-component artifacts.
- A/B test variants with ``omni-hub ab-test --domain engineering``.

## 5. Write Policy

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/engineering/` directly.

```bash
# 1) Cascade retrieves evidence (read-only)
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain engineering --persist-evidence

# 2) Bridge to a Proposal(kind=wiki_update) — humans review
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain engineering

# 3) Human review
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land approved Proposal → vault/wiki/domains/engineering/ + claims.jsonl
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>

# Retire stale claims: bitemporal close, never delete.
PYTHONPATH=src python3 -m omni_hub.cli wiki-supersede --old <id> --new <id>
```

### Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: engineering
# optional (domain-specific)
# github_repo: ...   # owner/name when the page concerns a specific repo
# language: ...   # primary programming language
# framework_version: ...   # framework version at time of writing
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.40 -->` marker line to opt out of future regenerations._
