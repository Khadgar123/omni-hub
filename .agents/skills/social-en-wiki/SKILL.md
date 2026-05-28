---
name: social-en-wiki
status: active-domain
description: |
  English social media — Twitter / Reddit / HN.

  Triggers — invoke this skill when the user asks any of:
  - "这条 tweet 火了"
  - "HN 在讨论 X"
  - "Reddit r/X 的态度"

  Source corpus: vault/wiki/domains/social-en/.  Authoritative
  cascade: `x_twitter`, `gdelt`.  Stale threshold: 14 days.

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
  display_name: "Social (English) — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - x_twitter
    - gdelt
  tags:
    - wiki
    - domain
    - social_en
---

<!-- omni-skill-stub: v0.40 -->

# Social (English) — Wiki Domain Skill

This is the **social_en** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["social_en"]`.

> Tier-2 paid/broker social-media domain.  Opt-in only — no default cascade hit.  twitterapi.io paid lane.  Reactive: pages mostly from user-shared links + GDELT news context.

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
  --query "..." --domain social_en --tier standard

# GraphRAG-style community probe (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

Authoritative cascade: `x_twitter`, `gdelt`.  When in doubt, default to ``tier=standard``.

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

- data_gap is expected — social pages reflect a moment, not a process.
- missing attribution / post_id = broken_cross_ref severity=medium.

Lint severity overrides:

  - `broken_cross_ref` → **medium**
  - `data_gap` → **skip**

## 4. Eval Metric

- Composite score = Judge composite (evidence_coverage / information_density / citation_support / style_fit / uncertainty_calibration) computed by
  ``omni-hub judge-evaluate --domain social_en --candidate ...``.
- Per-domain rubric weights live in
  ``src/omni_hub/harness/domain_profiles.py::_DOMAIN_RUBRIC_OVERRIDES``.
- PreferenceStore at ``.omni/preference/social_en.jsonl`` —
  ``harness-compile-skill --domain social_en`` consumes this weekly
  and proposes SKILL.md body updates as DSPy 5-component artifacts.
- A/B test variants with ``omni-hub ab-test --domain social_en``.

## 5. Write Policy

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/social-en/` directly.

```bash
# 1) Cascade retrieves evidence (read-only)
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain social_en --persist-evidence

# 2) Bridge to a Proposal(kind=wiki_update) — humans review
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain social_en

# 3) Human review
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land approved Proposal → vault/wiki/domains/social-en/ + claims.jsonl
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>

# Retire stale claims: bitemporal close, never delete.
PYTHONPATH=src python3 -m omni_hub.cli wiki-supersede --old <id> --new <id>
```

### Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: social_en
# optional (domain-specific)
# platform: ...   # x | reddit | hn | other
# post_id: ...   # platform-native ID
# author: ...   # post author handle
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.40 -->` marker line to opt out of future regenerations._
