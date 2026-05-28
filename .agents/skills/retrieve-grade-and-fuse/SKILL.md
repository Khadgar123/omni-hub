---
name: retrieve-grade-and-fuse
status: active
mode: post-cascade-filter
description: |
  Applies CRAG-style grading + RRF fusion to cascade output. Drops records
  graded "incorrect" (paywall stubs, 404 pages, zero-overlap snippets); ranks
  surviving records by Reciprocal Rank Fusion score so cross-source order is
  meaningful.

  Use this skill when the user asks the agent to:
  - "rerank these results with RRF"
  - "filter the bad results before I show them to the model"
  - "why was record R7 dropped"
  - "raise the grader threshold for this query"

  Wraps the same logic the `retrieve` CLI applies via `--fusion rrf --grader
  heuristic` — but exposes it as a standalone step so post-hoc filtering
  is possible (e.g. after combining two prior runs).
license: MIT
---

# Retrieve — Grade and Fuse

## What it does

Two-step quality gate applied post-cascade:

1. **Fusion** — `concat` (cascade order) or `rrf` (Reciprocal Rank Fusion,
   `k=60`, identity = canonical_id ∨ url ∨ source-title).
2. **Grading** — calls a `Callable[(query, record), Verdict]` returning
   `"correct" | "ambiguous" | "incorrect"`; drops `"incorrect"`, counts the
   drops in `graded_dropped`.

Two grader implementations ship:

- `HeuristicGrader` — substring tells (paywall / 404 / "internal server
  error") + query-token overlap on title + snippet.  Deterministic, ~5 µs
  per record, no LLM call.
- `LLMJudgeGrader` — defers to a user-supplied `Callable[[prompt], str]`.
  Falls back to `"ambiguous"` if the model is unparseable or raises.

## Invocation via CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain research --fusion rrf --grader heuristic
```

## Invocation programmatically

```python
from omni_hub.retrieval import Cascade, HeuristicGrader, LLMJudgeGrader

cascade = Cascade(sources)
result = cascade.retrieve(
    "claude agent SDK",
    domain="ai_progress",
    fusion="rrf",
    grader=HeuristicGrader(min_overlap_ratio=0.15),
)
print(result.graded_dropped, "dropped")
```

## Tuning notes

- `HeuristicGrader.min_overlap_ratio` defaults to `0.15`. Raise to `0.25`
  for narrow factual queries; lower to `0.10` for exploratory queries
  (early in research).
- `LLMJudgeGrader.prompt_template` accepts a custom Jinja-less template
  string with `{query} {title} {url} {snippet}` placeholders.
- The cascade always assigns `cite_id` ("R1", "R2", …) AFTER fusion +
  grading, so dropped records never consume a `cite_id` slot.

## Anti-patterns

- **Do not** run an LLM judge over hundreds of records on every cascade —
  it inverts the cost benefit. Use the heuristic by default and only call
  out to LLMJudgeGrader for the final 5-10 records when a worker is about
  to act on them.
- **Do not** rerank with RRF when you only have one source. RRF needs ≥2
  sources to be informative; with one source it's a no-op vs `concat`.
- **Do not** silently swallow `graded_dropped > 0`. Surface it in the
  agent's response — the user should know 3 sources were paywalled.
