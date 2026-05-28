---
name: order-propose
status: active-functional
description: |
  Emit an OrderIntent + RiskCheckResult as Proposal(kind=order_intent).  Hard-blocks > 25% portfolio position; warns > 10%; refuses MARKET-without-price.  Human approves; broker CLI in agent-harness executes.

  Triggers — invoke this skill when the user says any of:
  - "propose a BUY of NVDA at limit 195"
  - "下一个 limit 单 (走 Proposal)"
  - "place an order — Proposal first"

  This is a **functional orchestrator** (Application Plane).  It composes
  the foundation primitives (`finance-screen`, `propose-approve`) into a user-visible product
  flow.  Domain knowledge stays in the routed ``*-wiki`` skills; this
  layer is the cross-domain glue.
license: MIT
schema_version: v0.40
omni_hub:
  layer: functional
  namespace: functional
  display_name: "Order Propose"
  status: active
  version: 0.1.0
  entrypoint: "operation:order_propose"
  risk_level: L2
  composes:
    - finance-screen
    - propose-approve
  required_permissions: []
  tags:
    - functional
    - orchestrator
    - active
---

<!-- omni-skill-stub: v0.40 -->

# Order Propose

Emit an OrderIntent + RiskCheckResult as Proposal(kind=order_intent).  Hard-blocks > 25% portfolio position; warns > 10%; refuses MARKET-without-price.  Human approves; broker CLI in agent-harness executes.

## What it composes

- `finance-screen` (foundation)
- `propose-approve` (foundation)

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli order-propose [--help]
```

## Hard rules

- Functional skills MAY orchestrate multiple foundation calls.  They do
  NOT bypass the Proposal[T] gate for any mutating step.
- Trigger phrases are the user-visible product surface; tune via
  PreferenceStore + ``harness-compile-skill --functional`` (v0.40).



---

_Auto-generated stub.  Remove the ``<!-- omni-skill-stub: v0.40 -->`` marker line to
opt out of regeneration._
