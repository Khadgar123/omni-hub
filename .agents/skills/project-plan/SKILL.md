---
name: project-plan
status: active-functional
description: |
  Create a high-level Project; enqueue a claude lane planner task that emits a plan_markdown + subtask decomposition as Proposal(kind=project_plan).

  Triggers — invoke this skill when the user says any of:
  - "plan a project to ship X"
  - "decompose this multi-week effort"
  - "做个 plan 把 X 拆成子任务"

  This is a **functional orchestrator** (Application Plane).  It composes
  the foundation primitives (`task-add`) into a user-visible product
  flow.  Domain knowledge stays in the routed ``*-wiki`` skills; this
  layer is the cross-domain glue.
license: MIT
schema_version: v0.38
omni_hub:
  layer: functional
  display_name: "Project Plan"
  status: active
  version: 0.1.0
  entrypoint: "operation:project_plan"
  risk_level: L1
  composes:
    - task-add
  required_permissions: []
  tags:
    - functional
    - orchestrator
---

<!-- omni-skill-stub: v0.38 -->

# Project Plan

Create a high-level Project; enqueue a claude lane planner task that emits a plan_markdown + subtask decomposition as Proposal(kind=project_plan).

## What it composes

- `task-add` (foundation)

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli project-plan [--help]
```

## Hard rules

- Functional skills MAY orchestrate multiple foundation calls.  They do
  NOT bypass the Proposal[T] gate for any mutating step.
- Trigger phrases are the user-visible product surface; tune via
  PreferenceStore + ``harness-compile-skill --functional`` (v0.40).



---

_Auto-generated stub.  Remove the ``<!-- omni-skill-stub: v0.38 -->`` marker line to
opt out of regeneration._
