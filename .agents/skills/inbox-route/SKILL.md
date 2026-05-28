---
name: inbox-route
status: active-functional
description: |
  Classify a forwarded item (URL / PDF / .ics / task / wiki) and dispatch to the right handler (capture-url, calendar-add, task-add, wiki-propose-research).

  Triggers — invoke this skill when the user says any of:
  - "I just forwarded this — handle it"
  - "把这个内容收进 KB"
  - "convert this email into the right action"

  This is a **functional orchestrator** (Application Plane).  It composes
  the foundation primitives (`url-capture`, `calendar-add`, `task-add`, `wiki-propose-research`) into a user-visible product
  flow.  Domain knowledge stays in the routed ``*-wiki`` skills; this
  layer is the cross-domain glue.
license: MIT
schema_version: v0.38
omni_hub:
  layer: functional
  display_name: "Inbox Route (Forwarded Content)"
  status: active
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
---

<!-- omni-skill-stub: v0.38 -->

# Inbox Route (Forwarded Content)

Classify a forwarded item (URL / PDF / .ics / task / wiki) and dispatch to the right handler (capture-url, calendar-add, task-add, wiki-propose-research).

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

_Auto-generated stub.  Remove the ``<!-- omni-skill-stub: v0.38 -->`` marker line to
opt out of regeneration._
