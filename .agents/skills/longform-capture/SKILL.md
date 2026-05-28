---
name: longform-capture
status: active-foundation
description: |
  Capture a long-form piece (blog post / interview transcript / podcast /
  book section / docs page) into the knowledge base via the standard
  retrieve → evidence → wiki-ingest chain.

  Triggers — invoke this skill when the user says any of:
  - "capture this blog post into the wiki"
  - "import this podcast transcript"
  - "把这个 YouTube 演讲全文存进去"
  - "longform: <URL>"

  This is a **foundation primitive** for the long-form layer of the
  knowledge mesh.  Wraps ``Trafilatura`` (static HTML), ``YouTube
  transcript`` (captions), and ``RSS`` (single-item) into one
  user-facing capture path.
license: MIT
schema_version: v0.43
omni_hub:
  layer: foundation
  namespace: foundation_core
  bucket: knowledge_ingestion
  display_name: "Long-form Capture"
  status: active
  version: 0.1.0
  entrypoint: "script:scripts/longform_capture.py"
  risk_level: L0
  required_permissions: []
  connectors:
    - trafilatura
    - youtube_transcript
    - rss
    - jina_reader
  tags:
    - foundation
    - knowledge_ingestion
    - longform
---

# Long-form Capture

The cascade is great at *finding* short snippets across many sources.
This skill is the dual: take **one URL** (or YouTube ID) and produce a
**clean full-text artifact** plus an evidence record.

## Backend selection

| Input shape | Connector | Why |
|---|---|---|
| ``https://blog.example.com/post`` (static HTML) | ``trafilatura`` | best-in-class boilerplate stripping, local |
| ``https://www.youtube.com/watch?v=…`` | ``youtube_transcript`` | auto-caption extraction, no API key |
| RSS/Atom feed URL | ``rss`` | feed-style multi-item |
| Heavy SPA / JS-rendered | ``jina_reader`` | fallback when Trafilatura returns empty |

The skill auto-routes by URL pattern; users don't need to pick.

## Output

* Cleaned text → ``vault/raw/<domain>/<run_id>/<idx>.md``
* Evidence record → ``vault/evidence/<domain>/``
* (Optional, with ``--propose``) ``Proposal(kind=wiki_update)`` ready
  for human review → wiki-apply

## Domain inference

If ``--domain`` not passed, infer from URL:

* ``arxiv.org`` / ``*.edu`` / journal sites → ``research``
* ``karpathy.github.io`` / personal AI blogs → ``ai_progress``
* ``anthropic.com/news`` / ``openai.com/blog`` → ``ai_progress``
* ``sec.gov`` / ``federalreserve.gov`` → ``finance`` / ``us_policy``
* fallback → ``default``

## Compliance

Same write path as every other ingestion: evidence is auto-persisted,
wiki page lands only after Proposal approval.
