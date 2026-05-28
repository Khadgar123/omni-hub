---
name: source-policy-grade
status: active-foundation
description: |
  Score every retrieval source on 5 axes — authority / freshness / cost
  tier / batchable / legal-risk — and emit a Markdown report users can
  read before they trust a source's output.  Used by the seed
  orchestrator to decide which sources to prefer for batch ingestion
  and which to defer.

  Triggers — invoke this skill when the user says any of:
  - "grade my sources"
  - "哪些 source 学术权威性高"
  - "score the retrieval cascade"
  - "audit source quality"

  Pure metadata operation — no HTTP calls, no LLM cost.  Reads
  ``src/omni_hub/retrieval/source_policy.py`` + each connector's
  ``check()`` + manifest comments to compose the report.
license: MIT
schema_version: v0.43
omni_hub:
  layer: foundation
  namespace: foundation_meta
  bucket: governance
  display_name: "Source Policy Grade"
  status: active
  version: 0.1.0
  entrypoint: "script:scripts/source_policy_grade.py"
  risk_level: L0
  required_permissions: []
  connectors: []
  tags:
    - foundation
    - governance
    - source_quality
---

# Source Policy Grade

The 5-Plane audit named "Source Mesh, not Tool Soup" as the right
direction.  This skill operationalises that — every source in the
cascade is scored before users build on it.

## Grading rubric

| Axis | Scale | What "high" means |
|---|---|---|
| **authority_score** | 0-10 | Official / academic / first-party = 10; aggregator = 5; scraping = 1 |
| **freshness_sla** | 0-10 | Real-time (15min) = 10; daily = 7; monthly = 4; ad-hoc = 1 |
| **cost_tier** | 0/1/2 | 0 = free + no key, 1 = free quota / personal key, 2 = paid / broker |
| **batchable** | bool | Can the source efficiently return >1000 records per query? |
| **legal_risk** | low/med/high | TOS scraping clauses, ToS-of-service for republish, etc. |

## Output

A grouped Markdown table with one row per source, plus headline
insights:

* Top 10 highest-authority free sources
* Top 5 freshest sources
* Sources to **avoid for batch** (legal risk + non-batchable combo)
* Coverage gaps where no high-authority source exists yet

## Use by seed_orchestrator

When ``--allow-paid`` is not set, the seed orchestrator already filters
out tier=2.  This skill goes further:

* For each domain in ``seed-source-manifest.yaml``, surface whether
  the bulk sources are high-authority + batchable.
* Flag domains where bulk path is missing or low-authority.

## Use by entity-watchlist

Per-entity ``sources:`` mapping is reviewed against grade table; the
skill emits a warning if you've configured a tier-2 broker as the
primary signal for a high-frequency entity.

## Why not just `retrieve-doctor`

``retrieve-doctor`` only reports connectivity (`ok` / `warn` / `off`).
This skill reports **fitness-for-purpose**: a working connector that's
legally fraught or non-batchable is still wrong for some uses.
