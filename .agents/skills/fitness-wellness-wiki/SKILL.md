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
  cascade: `pubmed`, `europe_pmc`, `wikipedia`.  Stale threshold: 365 days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.19
---

<!-- omni-skill-stub: v0.19 -->

# Fitness & Wellness — Wiki Domain Skill

This is the **fitness_wellness** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["fitness_wellness"]`.

> 健身、营养、康复、睡眠、心理健康。 RCT-backed claims preferred; Bilibili / Instagram 健身博主 claims need supporting study links or are marked confidence: low.  Connectors land in v0.20 (PubMed + Bilibili).  High guard against pseudo-science: lint hints require RCT / meta-analysis citation for any 'do X to achieve Y' claim.

## When to use

Triggers (subset):

- "增肌应该怎么练"
  - "睡眠不好怎么办"
  - "creatine RCT meta-analysis"
  - "减脂期蛋白质摄入"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain fitness_wellness --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain fitness_wellness --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain fitness_wellness

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/fitness-wellness/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/fitness-wellness/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- evidence_grade frontmatter REQUIRED for any 'do X to achieve Y' claim.
- missing_concept severity=high on supplement / drug names — must link to safety profile.
- contradiction severity=high — fitness folklore frequently contradicts trials.

### Severity overrides

  - `broken_cross_ref` → **medium**
  - `contradiction` → **high**
  - `missing_concept` → **high**

## Required frontmatter on new pages

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

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/fitness_wellness.jsonl`.  `harness-compile-skill --domain
fitness_wellness` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.19 -->` marker line to opt out of future regenerations._
