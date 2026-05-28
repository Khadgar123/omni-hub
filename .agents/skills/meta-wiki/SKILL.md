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
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.37
omni_hub:
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
  inputs:
    query: "user question text"
    domain: "meta"
    tier: "minimal | standard | expanded"
  outputs:
    context_pack: "ContextPack with cited wiki + research results"
---

<!-- omni-skill-stub: v0.37 -->

# Meta (Self-Iteration) — Wiki Domain Skill

This is the **meta** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["meta"]`.

> The skill that improves omni-hub itself.  Corpus = own commit history + AGENTS.md / CLAUDE.md / docs/* + accepted PreferenceRecords across all other skills + open GitHub issues.  Outputs are pages documenting BUILD-vs-USE decisions, schema migration plans, cross-skill optimization wins, and proposed control-plane changes.  **Does not write to vault/wiki/ directly** — emits Proposal(kind=wiki_update) like every other skill, the irony being that meta-skill changes go through the same human gate as the skills it analyses.

## When to use

Triggers (subset):

- "omni-hub 接下来该做什么"
  - "哪些 skill 在掉点"
  - "应该 BUILD 还是 PIN-AS-FORK"
  - "v0.19 的下一步"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain meta --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain meta --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain meta

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/meta/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/meta/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- meta pages MUST reference a specific module / commit / lint pattern.
- broken_cross_ref severity=high — meta links to code must point at real files.
- data_gap severity=low — meta knowledge accumulates, not depletes.

### Severity overrides

  - `broken_cross_ref` → **high**
  - `data_gap` → **low**

## Required frontmatter on new pages

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

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/meta.jsonl`.  `harness-compile-skill --domain
meta` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.37 -->` marker line to opt out of future regenerations._
