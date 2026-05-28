---
name: finance-screen
status: active-functional
description: |
  Read-only stock screening against existing connectors (EDGAR / FRED / Tushare / Crunchbase).  Returns typed StockSignal list. Never places orders.

  Triggers — invoke this skill when the user says any of:
  - "screen US large-cap AI plays"
  - "找 A 股新能源"
  - "screen by sector + market_cap"

  This is a **functional orchestrator** (Application Plane).  It composes
  the foundation primitives (`retrieve`, `context-pack`) into a user-visible product
  flow.  Domain knowledge stays in the routed ``*-wiki`` skills; this
  layer is the cross-domain glue.
license: MIT
schema_version: v0.38
omni_hub:
  layer: functional
  display_name: "Finance Screen"
  status: active
  version: 0.1.0
  entrypoint: "operation:finance_screen"
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

# Finance Screen

Read-only stock screening against existing connectors (EDGAR / FRED / Tushare / Crunchbase).  Returns typed StockSignal list. Never places orders.

## What it composes

- `retrieve` (foundation)
- `context-pack` (foundation)

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli finance-screen [--help]
```

## Hard rules

- Functional skills MAY orchestrate multiple foundation calls.  They do
  NOT bypass the Proposal[T] gate for any mutating step.
- Trigger phrases are the user-visible product surface; tune via
  PreferenceStore + ``harness-compile-skill --functional`` (v0.40).



---

_Auto-generated stub.  Remove the ``<!-- omni-skill-stub: v0.38 -->`` marker line to
opt out of regeneration._
