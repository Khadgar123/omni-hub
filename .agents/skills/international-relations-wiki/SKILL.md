---
name: international-relations-wiki
status: active-domain
description: |
  Cross-border events / actors / scenarios.

  Triggers — invoke this skill when the user asks any of:
  - "中美关系最新"
  - "俄乌局势"
  - "台海动态"
  - "OPEC 决议"

  Source corpus: vault/wiki/domains/international-relations/.  Authoritative
  cascade: `acled`, `gdelt`, `world_bank`, `imf`, `wikipedia`.  Stale threshold: 7 days.

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
  display_name: "International Relations — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - acled
    - gdelt
    - world_bank
    - imf
    - wikipedia
  tags:
    - wiki
    - domain
    - international_relations
---

<!-- omni-skill-stub: v0.40 -->

# International Relations — Wiki Domain Skill

This is the **international_relations** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["international_relations"]`.

> Cross-border events, conflicts, multilateral data.  Highest velocity domain — daily news cycle, weekly stale threshold.

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
  --query "..." --domain international_relations --tier standard

# GraphRAG-style community probe (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

Authoritative cascade: `acled`, `gdelt`, `world_bank`, `imf`, `wikipedia`.  When in doubt, default to ``tier=standard``.

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

- stale_fact severity=high — IR pages decay in days.
- contradiction frequent and EXPECTED — multiple narrative sources are the norm.

Lint severity overrides:

  - `contradiction` → **low**
  - `data_gap` → **high**
  - `stale_fact` → **high**

## 4. Eval Metric

- Composite score = Judge composite (evidence_coverage / information_density / citation_support / style_fit / uncertainty_calibration) computed by
  ``omni-hub judge-evaluate --domain international_relations --candidate ...``.
- Per-domain rubric weights live in
  ``src/omni_hub/harness/domain_profiles.py::_DOMAIN_RUBRIC_OVERRIDES``.
- PreferenceStore at ``.omni/preference/international_relations.jsonl`` —
  ``harness-compile-skill --domain international_relations`` consumes this weekly
  and proposes SKILL.md body updates as DSPy 5-component artifacts.
- A/B test variants with ``omni-hub ab-test --domain international_relations``.

## 5. Write Policy

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/international-relations/` directly.

```bash
# 1) Cascade retrieves evidence (read-only)
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain international_relations --persist-evidence

# 2) Bridge to a Proposal(kind=wiki_update) — humans review
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain international_relations

# 3) Human review
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land approved Proposal → vault/wiki/domains/international-relations/ + claims.jsonl
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>

# Retire stale claims: bitemporal close, never delete.
PYTHONPATH=src python3 -m omni_hub.cli wiki-supersede --old <id> --new <id>
```

### Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: international_relations
# optional (domain-specific)
# country_iso: ...   # ISO 3166-1 alpha-3 country code(s)
# event_date: ...   # ISO date of the underlying event
# conflict_type: ...   # ACLED event_type if relevant
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.40 -->` marker line to opt out of future regenerations._
