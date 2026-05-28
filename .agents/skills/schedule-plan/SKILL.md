---
name: schedule-plan
status: active-functional
description: |
  Deterministic time-block solver: place PersonalTasks into free Calendar slots by priority + due_at + duration.

  Triggers — invoke this skill when the user says any of:
  - "plan my week"
  - "auto-block today's tasks"
  - "把待办排进日历"

  This is a **functional orchestrator** (Application Plane).  It composes
  the foundation primitives (`task-add`, `calendar-add`) into a user-visible product
  flow.  Domain knowledge stays in the routed ``*-wiki`` skills; this
  layer is the cross-domain glue.
license: MIT
schema_version: v0.40
omni_hub:
  layer: functional
  namespace: functional
  display_name: "Schedule Plan"
  status: active
  version: 0.1.0
  entrypoint: "operation:schedule_plan"
  risk_level: L1
  composes:
    - task-add
    - calendar-add
  required_permissions: []
  tags:
    - functional
    - orchestrator
    - active
---

<!-- omni-skill-stub: v0.40 -->

# Schedule Plan

Deterministic time-block solver: place PersonalTasks into free Calendar slots by priority + due_at + duration.

## What it composes

- `task-add` (foundation)
- `calendar-add` (foundation)

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli schedule-plan [--help]
```

## Hard rules

- Functional skills MAY orchestrate multiple foundation calls.  They do
  NOT bypass the Proposal[T] gate for any mutating step.
- Trigger phrases are the user-visible product surface; tune via
  PreferenceStore + ``harness-compile-skill --functional`` (v0.40).



---

_Auto-generated stub.  Remove the ``<!-- omni-skill-stub: v0.40 -->`` marker line to
opt out of regeneration._
