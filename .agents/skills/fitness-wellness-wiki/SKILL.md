---
name: fitness-wellness-wiki
status: active-domain
description: |
  Training / nutrition / recovery / sleep — RCT-backed.

  Triggers — invoke this skill when the user asks any of:
  - "增肌应该怎么练"
  - "睡眠不好怎么办"
  - "creatine RCT meta-analysis"
  - "减脂期蛋白质摄入"

  Source corpus: vault/wiki/domains/fitness-wellness/.  Authoritative
  cascade: `pubmed`, `europe_pmc`, `bilibili`, `wikipedia`.  Stale threshold: 365 days.

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
  display_name: "Fitness & Wellness — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - pubmed
    - europe_pmc
    - bilibili
    - wikipedia
  tags:
    - wiki
    - domain
    - fitness_wellness
---

<!-- omni-skill-stub: v0.40 -->

# Fitness & Wellness — Wiki Domain Skill

This is the **fitness_wellness** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["fitness_wellness"]`.

> 健身、营养、康复、睡眠、心理健康。 RCT-backed claims preferred; Bilibili / Instagram 健身博主 claims need supporting study links or are marked confidence: low.  Connectors land in v0.20 (PubMed + Bilibili).  High guard against pseudo-science: lint hints require RCT / meta-analysis citation for any 'do X to achieve Y' claim.

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
  --query "..." --domain fitness_wellness --tier standard

# GraphRAG-style community probe (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

Authoritative cascade: `pubmed`, `europe_pmc`, `bilibili`, `wikipedia`.  When in doubt, default to ``tier=standard``.

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

- evidence_grade frontmatter REQUIRED for any 'do X to achieve Y' claim.
- missing_concept severity=high on supplement / drug names — must link to safety profile.
- contradiction severity=high — fitness folklore frequently contradicts trials.

Lint severity overrides:

  - `broken_cross_ref` → **medium**
  - `contradiction` → **high**
  - `missing_concept` → **high**

## 4. Eval Metric

- Composite score = Judge composite (evidence_coverage / information_density / citation_support / style_fit / uncertainty_calibration) computed by
  ``omni-hub judge-evaluate --domain fitness_wellness --candidate ...``.
- Per-domain rubric weights live in
  ``src/omni_hub/harness/domain_profiles.py::_DOMAIN_RUBRIC_OVERRIDES``.
- PreferenceStore at ``.omni/preference/fitness_wellness.jsonl`` —
  ``harness-compile-skill --domain fitness_wellness`` consumes this weekly
  and proposes SKILL.md body updates as DSPy 5-component artifacts.
- A/B test variants with ``omni-hub ab-test --domain fitness_wellness``.

## 5. Write Policy

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/fitness-wellness/` directly.

```bash
# 1) Cascade retrieves evidence (read-only)
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain fitness_wellness --persist-evidence

# 2) Bridge to a Proposal(kind=wiki_update) — humans review
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain fitness_wellness

# 3) Human review
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land approved Proposal → vault/wiki/domains/fitness-wellness/ + claims.jsonl
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>

# Retire stale claims: bitemporal close, never delete.
PYTHONPATH=src python3 -m omni_hub.cli wiki-supersede --old <id> --new <id>
```

### Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: fitness_wellness
# optional (domain-specific)
# modality: ...   # strength | hypertrophy | cardio | mobility | nutrition | sleep | mental
# evidence_grade: ...   # RCT | meta-analysis | observational | expert-opinion | n=1
# rct_link: ...   # DOI of supporting RCT when claim is causal
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.40 -->` marker line to opt out of future regenerations._
