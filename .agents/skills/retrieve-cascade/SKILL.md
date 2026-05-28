---
name: retrieve-cascade
status: active
mode: parallel-fan-out
description: |
  Runs the federated retrieval cascade: parallel ThreadPoolExecutor dispatch
  across the per-domain source list, RRF (Reciprocal Rank Fusion, k=60) or
  concat fusion, canonical_id semantic dedup, SQLite TTL cache, and
  per-source error capture.

  Use this skill when the user asks the agent to:
  - "search across all sources for X"
  - "run the retrieval cascade with RRF"
  - "rerun the same query but skip the planner / grader"
  - asks for raw RetrievalRecord output rather than a synthesized answer.

  Triggered from the retrieve router OR directly when the cascade settings
  are explicit (e.g. "fusion=rrf, no grader, no cache").
license: MIT
---

# Retrieve Cascade — Parallel Fan-Out + RRF

Single-purpose: take a query + domain (or explicit source list) and return
fused `RetrievalRecord`s.

## What the cascade does

1. Pick the source list (from `domain` argument, or override via
   `--sources a,b,c`).
2. Cache lookup per source (SQLite TTL: Wikidata/Wikipedia 7d,
   OpenAlex/S2/Crossref 24h, arXiv 12h, Brave/GDELT 1h, Jina 5min).
3. `ThreadPoolExecutor` fan-out over cache-misses with a 15s wall-clock cap.
   Per-source failures are captured into `errors[]`, never aborting.
4. Per-source result lists are fused:
   - `fusion=concat` (default, deterministic): cascade order, then dedup.
   - `fusion=rrf`: `score(r) = Σ_lists 1/(60 + rank_in_list)` over lists.
5. Dedup by `canonical_id` (DOI / arxiv_id / wp:lang:pid) then `url`.
6. Optional grader (CRAG-style) drops records graded `"incorrect"`.
7. Assign `cite_id` (`R1`, `R2`, …) post-fusion so downstream prompts can
   emit `<cite r="R3"/>` markers that `citations.py` renders as `[3]` +
   References block.

## Invocation

```bash
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query   "<NL query>" \
  --domain  research|engineering|finance|policy|... \
  --fusion  rrf|concat \
  --per-source-limit 5 \
  --total-limit 20 \
  [--sources wikidata,brave_search,crossref,wikipedia]  # override domain default
  [--cache]                                # use SQLite TTL cache
  [--grader heuristic]                     # CRAG drop
```

Output is JSON:
```json
{
  "query": "...", "domain": "...", "fusion": "rrf", "count": 12,
  "records": [{"source":"openalex","cite_id":"R1","canonical_id":"doi:..."}],
  "sources_tried": ["openalex","arxiv","wikipedia"],
  "sources_succeeded": ["openalex","wikipedia"],
  "errors": [{"source":"arxiv","error":"timed out after 15s"}],
  "graded_dropped": 0
}
```

## When to use vs the router

- Use directly when **the cascade parameters are the message** ("rerun
  with RRF off"). The router exists for vaguer requests.
- Use directly inside a worker loop where you want the raw records back
  rather than letting the router persist them.

## Anti-patterns

- **Do not** call this with no `--domain` if the query is domain-specific.
  The `default` cascade is broad (Wikidata + Wikipedia + Brave + Crossref
  + OpenAlex + GDELT), but domain cascades still encode better authority
  ordering.
- **Do not** retry on errors silently. The `errors[]` array is the
  audit trail — surface it.
