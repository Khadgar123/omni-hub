---
name: inbox-route
status: active-functional
description: |
  Classify a forwarded item (URL / PDF / .ics / task / wiki).  v0.40: classifier only — does NOT yet dispatch to the typed handlers (url-capture, calendar-add, task-add, wiki-propose-research).  Dispatch lands in v0.41 once each downstream handler returns a Proposal so audit + approval apply uniformly.

  > **Status: stub** — contracts exist but the operation returns placeholder data.  See description for what's missing.

  Triggers — invoke this skill when the user says any of:
  - "I just forwarded this — handle it"
  - "把这个内容收进 KB"
  - "convert this email into the right action"

  This is a **functional orchestrator** (Application Plane).  It composes
  the foundation primitives (`url-capture`, `calendar-add`, `task-add`, `wiki-propose-research`) into a user-visible product
  flow.  Domain knowledge stays in the routed ``*-wiki`` skills; this
  layer is the cross-domain glue.
license: MIT
schema_version: v0.40
omni_hub:
  layer: functional
  namespace: functional
  display_name: "Inbox Route (Forwarded Content)"
  status: stub
  version: 0.1.0
  entrypoint: "operation:inbox_classify"
  risk_level: L0
  composes:
    - url-capture
    - calendar-add
    - task-add
    - wiki-propose-research
  required_permissions: []
  tags:
    - functional
    - orchestrator
    - stub
---

<!-- omni-skill-stub: v0.40 -->

# Inbox Route (Forwarded Content)

Classify a forwarded item (URL / PDF / .ics / task / wiki).  v0.40: classifier only — does NOT yet dispatch to the typed handlers (url-capture, calendar-add, task-add, wiki-propose-research).  Dispatch lands in v0.41 once each downstream handler returns a Proposal so audit + approval apply uniformly.

## What it composes

- `url-capture` (foundation)
- `calendar-add` (foundation)
- `task-add` (foundation)
- `wiki-propose-research` (foundation)

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli inbox-route [--help]
```

## Hard rules

- Functional skills MAY orchestrate multiple foundation calls.  They do
  NOT bypass the Proposal[T] gate for any mutating step.
- Trigger phrases are the user-visible product surface; tune via
  PreferenceStore + ``harness-compile-skill --functional`` (v0.40).



---

_Auto-generated stub.  Remove the ``<!-- omni-skill-stub: v0.40 -->`` marker line to
opt out of regeneration._
