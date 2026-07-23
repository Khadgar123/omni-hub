---
name: entity-timeline-build
status: active-foundation
description: |
  Build a unified chronological timeline for any entity in the watchlist
  (person / company / institution / topic) by aggregating signals across
  RSS / HN / Bluesky / Mastodon / Reddit / GDELT / EDGAR / Tavily /
  OpenAlex / YouTube transcript / Truth Social.

  Triggers — invoke this skill when the user says any of:
  - "build me a timeline for Karpathy"
  - "Anthropic 这一年的关键事件时间线"
  - "summarise the last 90 days for NVIDIA"
  - "what happened with Trump and AI executive orders in 2025"

  This is a **foundation primitive** (no domain knowledge baked in).
  It composes the existing ``follow_entity`` machinery + clusters
  records by date to emit a single time-ordered story.  All raw
  records still pass through ``Proposal[T]`` for human review before
  any persistent claim is recorded.
license: MIT
schema_version: v0.43
omni_hub:
  layer: foundation
  namespace: foundation_core
  bucket: knowledge_access
  display_name: "Entity Timeline Build"
  status: active
  version: 0.1.0
  entrypoint: "script:scripts/entity_timeline_build.py"
  risk_level: L0
  required_permissions: []
  connectors:
    - rss
    - hackernews
    - bluesky
    - mastodon
    - reddit
    - gdelt
    - openalex
    - tavily
    - edgar
    - youtube_transcript
    - truth_social
  tags:
    - foundation
    - knowledge_access
    - entity_watchlist
    - timeline
---

# Entity Timeline Build

Aggregate the canonical signals on any watchlisted entity into a single
chronological timeline.  Backs the v0.43 audit recommendation to track
"Karpathy / Musk / Trump" by **entity**, not by hard-coded per-person
skills.

## Input

* ``entity_id`` — must exist in ``config/entity-watchlist.yaml``
  (``karpathy``, ``musk``, ``trump``, ``anthropic``, ``nvidia``, …).
* ``days`` — lookback window (default 90).
* ``sources`` — optional source subset; defaults to all configured.

## Output

A time-ordered Markdown timeline with:

* **One row per event** — date, source, snippet, URL.
* **Section per entity facet** when multiple categories are configured
  (e.g. for ``musk``: separate sections for Tesla SEC filings vs
  Twitter/X mentions vs GDELT news).
* **BGE-reranked highlights** — top 5 records by cross-encoder relevance
  to the entity's display name, surfaced at the top.

## Compliance

* Outputs are **not** written to ``vault/wiki/`` directly.  If the
  user wants the timeline persisted, the skill writes evidence to
  ``vault/evidence/<domain>/<run_id>/`` and emits a
  ``Proposal(kind=wiki_update)`` for review.

## Related

* ``scripts/follow_entity.py`` — single-snapshot per entity.
* ``scripts/daily_follow_brief.py`` — cron-grade multi-entity brief.
* This skill = ``follow_entity`` × time-windowed clustering + BGE rerank.
