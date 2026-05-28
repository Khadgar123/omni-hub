---
name: project-plan
status: active-functional
description: |
  Create a high-level Project row.  v0.40: stub — only persists the Project; does NOT yet enqueue a claude-lane planner task or emit a Proposal(kind=project_plan).  Full planner+decompose flow lands in v0.41.

  > **Status: stub** — contracts exist but the operation returns placeholder data.  See description for what's missing.

  Triggers — invoke this skill when the user says any of:
  - "plan a project to ship X"
  - "decompose this multi-week effort"
  - "做个 plan 把 X 拆成子任务"

  This is a **functional orchestrator** (Application Plane).  It composes
  the foundation primitives (`task-add`) into a user-visible product
  flow.  Domain knowledge stays in the routed ``*-wiki`` skills; this
  layer is the cross-domain glue.
license: MIT
schema_version: v0.40
omni_hub:
  layer: functional
  namespace: functional
  display_name: "Project Plan"
  status: stub
  version: 0.1.0
  entrypoint: "operation:project_plan"
  risk_level: L1
  composes:
    - task-add
  required_permissions: []
  tags:
    - functional
    - orchestrator
    - stub
---

<!-- omni-skill-stub: v0.40 -->

# Project Plan

Create a high-level Project row.  v0.40: stub — only persists the Project; does NOT yet enqueue a claude-lane planner task or emit a Proposal(kind=project_plan).  Full planner+decompose flow lands in v0.41.

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

_Auto-generated stub.  Remove the ``<!-- omni-skill-stub: v0.40 -->`` marker line to
opt out of regeneration._
