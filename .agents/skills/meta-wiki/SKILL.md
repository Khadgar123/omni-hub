---
name: meta-wiki
status: active-domain
description: |
  omni-hub self-iteration — what to BUILD / USE / PIN / DEFER.

  Triggers — invoke this skill when the user asks any of:
  - "omni-hub 接下来该做什么"
  - "哪些 skill 在掉点"
  - "应该 BUILD 还是 PIN-AS-FORK"
  - "v0.19 的下一步"

  Source corpus: vault/wiki/domains/meta/.  Authoritative
  cascade: _(reactive — no cascade by default)_.  Stale threshold: 60 days.

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
  display_name: "Meta (Self-Iteration) — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    []
  tags:
    - wiki
    - domain
    - meta
---

<!-- omni-skill-stub: v0.40 -->

# Meta (Self-Iteration) — Wiki Domain Skill

This is the **meta** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["meta"]`.

> The skill that improves omni-hub itself.  Corpus = own commit history + AGENTS.md / CLAUDE.md / docs/* + accepted PreferenceRecords across all other skills + open GitHub issues.  Outputs are pages documenting BUILD-vs-USE decisions, schema migration plans, cross-skill optimization wins, and proposed control-plane changes.  **Does not write to vault/wiki/ directly** — emits Proposal(kind=wiki_update) like every other skill, the irony being that meta-skill changes go through the same human gate as the skills it analyses.

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
  --query "..." --domain meta --tier standard

# GraphRAG-style community probe (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

Authoritative cascade: _(reactive — no cascade by default)_.  When in doubt, default to ``tier=standard``.

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

- meta pages MUST reference a specific module / commit / lint pattern.
- broken_cross_ref severity=high — meta links to code must point at real files.
- data_gap severity=low — meta knowledge accumulates, not depletes.

Lint severity overrides:

  - `broken_cross_ref` → **high**
  - `data_gap` → **low**

## 4. Eval Metric

- Composite score = Judge composite (evidence_coverage / information_density / citation_support / style_fit / uncertainty_calibration) computed by
  ``omni-hub judge-evaluate --domain meta --candidate ...``.
- Per-domain rubric weights live in
  ``src/omni_hub/harness/domain_profiles.py::_DOMAIN_RUBRIC_OVERRIDES``.
- PreferenceStore at ``.omni/preference/meta.jsonl`` —
  ``harness-compile-skill --domain meta`` consumes this weekly
  and proposes SKILL.md body updates as DSPy 5-component artifacts.
- A/B test variants with ``omni-hub ab-test --domain meta``.

## 5. Write Policy

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/meta/` directly.

```bash
# 1) Cascade retrieves evidence (read-only)
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain meta --persist-evidence

# 2) Bridge to a Proposal(kind=wiki_update) — humans review
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain meta

# 3) Human review
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land approved Proposal → vault/wiki/domains/meta/ + claims.jsonl
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>

# Retire stale claims: bitemporal close, never delete.
PYTHONPATH=src python3 -m omni_hub.cli wiki-supersede --old <id> --new <id>
```

### Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: meta
# optional (domain-specific)
# affects_modules: ...   # list of src/omni_hub modules a meta page concerns
# decision: ...   # BUILD | USE | PIN-AS-FORK | DEFER | REJECT
# triggered_by: ...   # preference_drift | lint_pattern | user_request | commit_pattern
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.40 -->` marker line to opt out of future regenerations._
