---
name: retrieve
status: active-router
mode: federated-cascade
description: |
  Unified entry for the omni-hub Retrieval Plane — federated search across
  scholarly, biomedical, legal, statistical, archive, encyclopedia, news, web,
  and social sources with optional paid/key-gated tiers, per-domain source maps,
  RRF cross-source fusion, CRAG-style grading, and PaperQA2-style evidence
  persistence.

  Use this skill whenever the user asks the agent to:
  - find papers / sources / news / docs on a topic from outside the local vault
  - "调研一下 X" / "research X" / "find me sources on Y"
  - look up a URL's contents (forwarded link, paper PDF, blog post)
  - compare what multiple sources say about the same topic
  - rebuild evidence for a prior claim (`.omni/retrieval/<run_id>/`)

  Routes to one of the stage skills:
  - retrieve-domain-source-map → pick the cascade for the query's domain
  - retrieve-cascade           → run the cascade (parallel + RRF + cache)
  - retrieve-grade-and-fuse    → drop graded-incorrect records
  - retrieve-evidence-pin      → persist run as .jsonl / sources / manifest

  Do NOT trigger for: vault-internal lookups (use papers-query-knowledge-base),
  filesystem search (use grep/Read), or capture-url single-URL fetch (use
  the fetch-url CLI directly).
license: MIT
---

# Retrieve — Federated Retrieval Plane Router

This is the single entry point for the omni-hub Retrieval Plane. It does not
re-implement the cascade — it routes to four stage skills and the existing
`omni-hub retrieve` CLI.

## Stage model

```
                       ┌─────────────────────────────┐
                       │  user query + maybe domain  │
                       └────────────┬────────────────┘
                                    │
              retrieve-domain-source-map  (pick cascade)
                                    │
                          retrieve-cascade  (parallel fan-out)
                                    │
                       retrieve-grade-and-fuse  (RRF + CRAG drop)
                                    │
                       retrieve-evidence-pin    (persist run)
                                    │
                              records + cite_id
```

Each stage skill is single-purpose and idempotent. They share the same
underlying CLI (`omni-hub retrieve …`) and the same data shape
(`RetrievalRecord` with `cite_id` / `canonical_id`).

## When to use this router vs a stage skill directly

- **User intent is "find sources on X"** → invoke this router, it picks the
  cascade for you.
- **User intent is "rerun the same query but with grader off"** → go directly
  to `retrieve-cascade` (skip planning).
- **User intent is "what did we cite in run 20260528T…"** → go directly to
  `retrieve-evidence-pin`'s read path.

## Source map (defaults, 2026-Q2)

| Domain                  | Default cascade order                                   |
| ----------------------- | ------------------------------------------------------- |
| engineering             | brave_search → crossref → openalex → arxiv → wikidata → wikipedia |
| research                | crossref → openalex → semantic_scholar → arxiv → europe_pmc → pubmed → wikidata → wikipedia |
| biomedical              | europe_pmc → pubmed → crossref → openalex → wikidata → wikipedia |
| photography             | unsplash → pexels → wikipedia                           |
| fashion                 | wikipedia (use research-kb-* for vault snapshots)       |
| chat_relationships      | (purely reactive — no cascade)                          |
| finance                 | edgar → fred → crossref → wikidata → openalex → wikipedia |
| policy                  | federal_register → regulations_gov → congress_gov → courtlistener → brave_search → gdelt → internet_archive → wikidata → wikipedia |
| law                     | courtlistener → federal_register → regulations_gov → congress_gov → internet_archive → wikidata → wikipedia |
| international_relations | acled → gdelt → world_bank → imf → brave_search → wikidata → wikipedia |
| statistics              | data_commons → world_bank → imf → fred → wikidata → wikipedia |
| ai_progress             | hf_daily_papers → arxiv → crossref → openalex → brave_search → wikidata → wikipedia |
| default                 | wikidata → wikipedia → brave_search → crossref → openalex → gdelt → internet_archive |

Sources that require keys remain registered so `retrieve-doctor` can explain
setup gaps; the cascade fail-soft-skips unavailable sources and records them in
`errors[]`.

## Canonical commands the agent should know

```bash
# Basic federated search
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "Anthropic Agent Skills" --domain ai_progress --fusion rrf

# Add grader + persist evidence for replay
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "FRED unemployment rate" --domain finance \
  --grader heuristic --persist-evidence

# Inspect a run's evidence
ls .omni/retrieval/                                     # list runs
cat .omni/retrieval/<run_id>/run_manifest.json          # what was asked
cat .omni/retrieval/<run_id>/sources.json               # unique URLs
head -3 .omni/retrieval/<run_id>/evidence.jsonl         # records
```

## Anti-patterns

- **Do not** fan-out to arbitrary URLs without domain routing. The cascade
  guarantees correct source selection per query domain — bypassing it loses
  the RRF fusion and the per-source TTL cache.
- **Do not** persist evidence for one-off lookups. Only persist when the
  result will be cited downstream (e.g. by a propose-knowledge call).
- **Do not** write reverse-engineered scraping code in the main repo. If a
  source needs anti-bot handling, pin its scraper as a fork under
  `agent-harness/forks/` and shell out via subprocess.
