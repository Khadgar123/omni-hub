---
name: cooking-wiki
status: active-domain
description: |
  Recipes / techniques / substitutions.

  Triggers — invoke this skill when the user asks any of:
  - "今晚做什么"
  - "红烧肉怎么做"
  - "麻婆豆腐的关键步骤"
  - "how do I temper chocolate"

  Source corpus: vault/wiki/domains/cooking/.  Authoritative
  cascade: `xiaohongshu`, `bilibili`, `wikipedia`.  Stale threshold: 730 days.

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
  display_name: "Cooking — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - xiaohongshu
    - bilibili
    - wikipedia
  tags:
    - wiki
    - domain
    - cooking
---

<!-- omni-skill-stub: v0.40 -->

# Cooking — Wiki Domain Skill

This is the **cooking** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["cooking"]`.

> 中餐 / 西餐 / 烘焙 / 发酵 / 食材保鲜.  Receptive domain: 小红书 + Bilibili 美食 + 下厨房 + Allrecipes (英文) provide candidate recipes; user feedback (complete-and-rate) drives the PreferenceStore.  Connectors land in v0.20.  Each recipe page tracks substitutions + per-step constraints.

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
  --query "..." --domain cooking --tier standard

# GraphRAG-style community probe (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

Authoritative cascade: `xiaohongshu`, `bilibili`, `wikipedia`.  When in doubt, default to ``tier=standard``.

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

- Recipe pages SHOULD link to at least one source video / blog (broken_cross_ref severity=low).
- data_gap severity=low — cooking knowledge is durable.

Lint severity overrides:

  - `broken_cross_ref` → **low**
  - `data_gap` → **low**

## 4. Eval Metric

- Composite score = Judge composite (evidence_coverage / information_density / citation_support / style_fit / uncertainty_calibration) computed by
  ``omni-hub judge-evaluate --domain cooking --candidate ...``.
- Per-domain rubric weights live in
  ``src/omni_hub/harness/domain_profiles.py::_DOMAIN_RUBRIC_OVERRIDES``.
- PreferenceStore at ``.omni/preference/cooking.jsonl`` —
  ``harness-compile-skill --domain cooking`` consumes this weekly
  and proposes SKILL.md body updates as DSPy 5-component artifacts.
- A/B test variants with ``omni-hub ab-test --domain cooking``.

## 5. Write Policy

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/cooking/` directly.

```bash
# 1) Cascade retrieves evidence (read-only)
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain cooking --persist-evidence

# 2) Bridge to a Proposal(kind=wiki_update) — humans review
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain cooking

# 3) Human review
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land approved Proposal → vault/wiki/domains/cooking/ + claims.jsonl
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>

# Retire stale claims: bitemporal close, never delete.
PYTHONPATH=src python3 -m omni_hub.cli wiki-supersede --old <id> --new <id>
```

### Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: cooking
# optional (domain-specific)
# cuisine: ...   # chinese-sichuan | chinese-cantonese | italian | japanese | thai | ...
# technique: ...   # braise | stir-fry | bake | ferment | sous-vide | ...
# difficulty: ...   # beginner | intermediate | advanced
# time_active_min: ...   # active cooking time in minutes
# time_total_min: ...   # total time including waiting
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.40 -->` marker line to opt out of future regenerations._
