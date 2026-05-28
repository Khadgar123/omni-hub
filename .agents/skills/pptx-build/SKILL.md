---
name: pptx-build
status: active-functional
description: |
  Render a typed DeckOutline → real .pptx via the python-pptx shim in agent-harness/integrations/pptx/.  Never generates raw OOXML.

  Triggers — invoke this skill when the user says any of:
  - "build a pptx from this outline"
  - "做一份 deck"
  - "render this outline as a slide deck"

  This is a **functional orchestrator** (Application Plane).  It composes
  the foundation primitives (`context-pack`) into a user-visible product
  flow.  Domain knowledge stays in the routed ``*-wiki`` skills; this
  layer is the cross-domain glue.
license: MIT
schema_version: v0.38
omni_hub:
  layer: functional
  display_name: "PPTX Build"
  status: active
  version: 0.1.0
  entrypoint: "operation:pptx_build"
  risk_level: L1
  composes:
    - context-pack
  required_permissions: []
  tags:
    - functional
    - orchestrator
---

<!-- omni-skill-stub: v0.38 -->

# PPTX Build

Render a typed DeckOutline → real .pptx via the python-pptx shim in agent-harness/integrations/pptx/.  Never generates raw OOXML.

## What it composes

- `context-pack` (foundation)

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli pptx-build [--help]
```

## Hard rules

- Functional skills MAY orchestrate multiple foundation calls.  They do
  NOT bypass the Proposal[T] gate for any mutating step.
- Trigger phrases are the user-visible product surface; tune via
  PreferenceStore + ``harness-compile-skill --functional`` (v0.40).



---

_Auto-generated stub.  Remove the ``<!-- omni-skill-stub: v0.38 -->`` marker line to
opt out of regeneration._
