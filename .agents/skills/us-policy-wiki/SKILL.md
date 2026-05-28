---
name: us-policy-wiki
status: active-domain
description: |
  US federal/state policy — bills / regs / SCOTUS.

  Triggers — invoke this skill when the user asks any of:
  - "SCOTUS 2026 大案"
  - "Federal Register 最新法规"
  - "Congress 投票走向"
  - "X act 的影响"

  Source corpus: vault/wiki/domains/us-policy/.  Authoritative
  cascade: `federal_register`, `regulations_gov`, `congress_gov`, `courtlistener`, `gdelt`, `wikipedia`.  Stale threshold: 90 days.

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
  display_name: "US Policy — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - federal_register
    - regulations_gov
    - congress_gov
    - courtlistener
    - gdelt
    - wikipedia
  tags:
    - wiki
    - domain
    - us_policy
---

<!-- omni-skill-stub: v0.40 -->

# US Policy — Wiki Domain Skill

This is the **us_policy** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["us_policy"]`.

> US federal rules, dockets, bills, votes, Supreme Court rulings.  Per-domain cascade hits canonical .gov sources directly; secondary news (GDELT) backs context.  Quarterly update cycle.  Companion to ``cn_policy``; cross-references go through ``international_relations``.

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
  --query "..." --domain us_policy --tier standard

# GraphRAG-style community probe (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

Authoritative cascade: `federal_register`, `regulations_gov`, `congress_gov`, `courtlistener`, `gdelt`, `wikipedia`.  When in doubt, default to ``tier=standard``.

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

- missing_concept on bill_id / regulation_id SHOULD become an event page.
- contradiction severity=high — policy positions across sources require resolution.
- Cross-references to cn_policy / international_relations are encouraged for trade / sanctions / treaty topics.

Lint severity overrides:

  - `contradiction` → **high**
  - `missing_concept` → **high**

## 4. Eval Metric

- Composite score = Judge composite (evidence_coverage / information_density / citation_support / style_fit / uncertainty_calibration) computed by
  ``omni-hub judge-evaluate --domain us_policy --candidate ...``.
- Per-domain rubric weights live in
  ``src/omni_hub/harness/domain_profiles.py::_DOMAIN_RUBRIC_OVERRIDES``.
- PreferenceStore at ``.omni/preference/us_policy.jsonl`` —
  ``harness-compile-skill --domain us_policy`` consumes this weekly
  and proposes SKILL.md body updates as DSPy 5-component artifacts.
- A/B test variants with ``omni-hub ab-test --domain us_policy``.

## 5. Write Policy

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/us-policy/` directly.

```bash
# 1) Cascade retrieves evidence (read-only)
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain us_policy --persist-evidence

# 2) Bridge to a Proposal(kind=wiki_update) — humans review
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain us_policy

# 3) Human review
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land approved Proposal → vault/wiki/domains/us-policy/ + claims.jsonl
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>

# Retire stale claims: bitemporal close, never delete.
PYTHONPATH=src python3 -m omni_hub.cli wiki-supersede --old <id> --new <id>
```

### Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: us_policy
# optional (domain-specific)
# bill_id: ...   # Congress.gov bill number
# regulation_id: ...   # Federal Register doc number
# docket_id: ...   # regulations.gov docket id
# scotus_case: ...   # Supreme Court docket number when applicable
# jurisdiction: ...   # US-federal | US-state-XX | etc.
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.40 -->` marker line to opt out of future regenerations._
