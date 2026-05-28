---
name: task-add
status: active-functional
description: |
  Append a PersonalTask (user-facing todo, NOT a worker queue task) to .omni/personal_tasks.sqlite3.

  Triggers — invoke this skill when the user says any of:
  - "add a todo: ..."
  - "remind me to X"
  - "记一个任务: ..."

  This is a **functional orchestrator** (Application Plane).  It composes
  the foundation primitives (_(none — pure orchestrator)_) into a user-visible product
  flow.  Domain knowledge stays in the routed ``*-wiki`` skills; this
  layer is the cross-domain glue.
license: MIT
schema_version: v0.40
omni_hub:
  layer: functional
  namespace: functional
  display_name: "Task Add"
  status: active
  version: 0.1.0
  entrypoint: "operation:task_add"
  risk_level: L1
  composes:
    []
  required_permissions: []
  tags:
    - functional
    - orchestrator
    - active
---

<!-- omni-skill-stub: v0.40 -->

# Task Add

Append a PersonalTask (user-facing todo, NOT a worker queue task) to .omni/personal_tasks.sqlite3.

## What it composes

_(none — top-level entry point)_

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli task-add [--help]
```

## Hard rules

- Functional skills MAY orchestrate multiple foundation calls.  They do
  NOT bypass the Proposal[T] gate for any mutating step.
- Trigger phrases are the user-visible product surface; tune via
  PreferenceStore + ``harness-compile-skill --functional`` (v0.40).



---

_Auto-generated stub.  Remove the ``<!-- omni-skill-stub: v0.40 -->`` marker line to
opt out of regeneration._
