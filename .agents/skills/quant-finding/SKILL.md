---
name: quant-finding
status: active-functional
description: |
  Fold a quant backtest finding (strategy hypothesis / backtest metrics / risk disclosure) into the finance-domain ClaimLedger via Proposal(kind=wiki_update).  Never ingests raw OHLCV -- only human-reviewable conclusions.  The quant->knowledge seam (the quant data/backtest plane stays in agent-harness/quant).

  Triggers — invoke this skill when the user says any of:
  - "record this backtest conclusion into the wiki"
  - "把这个量化策略结论 / 回测沉淀进 claims"
  - "log a quant finding for review"

  This is a **functional orchestrator** (Application Plane).  It composes
  the foundation primitives (`context-pack`, `claims-show`) into a user-visible product
  flow.  Domain knowledge stays in the routed ``*-wiki`` skills; this
  layer is the cross-domain glue.
license: MIT
schema_version: v0.40
omni_hub:
  layer: functional
  namespace: functional
  display_name: "Quant Finding Propose"
  status: active
  version: 0.1.0
  entrypoint: "operation:quant_finding_propose"
  risk_level: L1
  composes:
    - context-pack
    - claims-show
  required_permissions: []
  tags:
    - functional
    - orchestrator
    - active
---

<!-- omni-skill-stub: v0.40 -->

# Quant Finding Propose

Fold a quant backtest finding (strategy hypothesis / backtest metrics / risk disclosure) into the finance-domain ClaimLedger via Proposal(kind=wiki_update).  Never ingests raw OHLCV -- only human-reviewable conclusions.  The quant->knowledge seam (the quant data/backtest plane stays in agent-harness/quant).

## What it composes

- `context-pack` (foundation)
- `claims-show` (foundation)

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli quant-finding [--help]
```

## Hard rules

- Functional skills MAY orchestrate multiple foundation calls.  They do
  NOT bypass the Proposal[T] gate for any mutating step.
- Trigger phrases are the user-visible product surface; tune via
  PreferenceStore + ``harness-compile-skill --functional`` (v0.40).



---

_Auto-generated stub.  Remove the ``<!-- omni-skill-stub: v0.40 -->`` marker line to
opt out of regeneration._
