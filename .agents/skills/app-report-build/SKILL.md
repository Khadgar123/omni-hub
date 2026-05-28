---
name: app-report-build
status: active-functional
description: |
  Cross-skill daily / weekly / monthly report — pure data rollup; --narrate enqueues a claude lane task for trend analysis (lands as Proposal(generation)).

  Triggers — invoke this skill when the user says any of:
  - "build a weekly report"
  - "today's daily digest"
  - "做一个本月 report"

  This is a **functional orchestrator** (Application Plane).  It composes
  the foundation primitives (`claims-show`, `wiki-lint`) into a user-visible product
  flow.  Domain knowledge stays in the routed ``*-wiki`` skills; this
  layer is the cross-domain glue.
license: MIT
schema_version: v0.38
omni_hub:
  layer: functional
  display_name: "App Report Build"
  status: active
  version: 0.1.0
  entrypoint: "operation:app_report_build"
  risk_level: L0
  composes:
    - claims-show
    - wiki-lint
  required_permissions: []
  tags:
    - functional
    - orchestrator
---

<!-- omni-skill-stub: v0.38 -->

# App Report Build

Cross-skill daily / weekly / monthly report — pure data rollup; --narrate enqueues a claude lane task for trend analysis (lands as Proposal(generation)).

## What it composes

- `claims-show` (foundation)
- `wiki-lint` (foundation)

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli app-report-build [--help]
```

## Hard rules

- Functional skills MAY orchestrate multiple foundation calls.  They do
  NOT bypass the Proposal[T] gate for any mutating step.
- Trigger phrases are the user-visible product surface; tune via
  PreferenceStore + ``harness-compile-skill --functional`` (v0.40).



---

_Auto-generated stub.  Remove the ``<!-- omni-skill-stub: v0.38 -->`` marker line to
opt out of regeneration._
