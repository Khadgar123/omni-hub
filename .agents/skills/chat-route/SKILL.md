---
name: chat-route
status: active-functional
description: |
  Route a conversational query to the right domain skill via intent classification; recommend the downstream OperationSpec.

  Triggers — invoke this skill when the user says any of:
  - "route this question to the right skill"
  - "用 chat router 决定 domain"
  - "where should this query go"

  This is a **functional orchestrator** (Application Plane).  It composes
  the foundation primitives (`retrieve`, `context-pack`) into a user-visible product
  flow.  Domain knowledge stays in the routed ``*-wiki`` skills; this
  layer is the cross-domain glue.
license: MIT
schema_version: v0.38
omni_hub:
  layer: functional
  display_name: "Chat Route"
  status: active
  version: 0.1.0
  entrypoint: "operation:app_route_task"
  risk_level: L0
  composes:
    - retrieve
    - context-pack
  required_permissions: []
  tags:
    - functional
    - orchestrator
---

<!-- omni-skill-stub: v0.38 -->

# Chat Route

Route a conversational query to the right domain skill via intent classification; recommend the downstream OperationSpec.

## What it composes

- `retrieve` (foundation)
- `context-pack` (foundation)

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli chat-route [--help]
```

## Hard rules

- Functional skills MAY orchestrate multiple foundation calls.  They do
  NOT bypass the Proposal[T] gate for any mutating step.
- Trigger phrases are the user-visible product surface; tune via
  PreferenceStore + ``harness-compile-skill --functional`` (v0.40).



---

_Auto-generated stub.  Remove the ``<!-- omni-skill-stub: v0.38 -->`` marker line to
opt out of regeneration._
