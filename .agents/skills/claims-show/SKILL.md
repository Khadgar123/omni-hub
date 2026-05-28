---
name: claims-show
status: active-foundation
description: |
  Inspect a single ClaimLedger record by claim_id (bitemporal: shows t_valid_from/to + superseded_by chain).

  Triggers — invoke this skill when the user says any of:
  - "show claim c_abc123"
  - "看一下 claim c_xyz"
  - "look up the supersedes chain of X"

  This is a **foundation primitive** (no domain knowledge baked in).  Use it
  as a building block from any other skill — see also ``app-report-build``,
  ``chat-route``, ``inbox-route``, and the 19 ``*-wiki`` domain skills.
license: MIT
schema_version: v0.40
omni_hub:
  layer: foundation
  namespace: foundation_core
  bucket: knowledge_access
  display_name: "Claims Lookup"
  status: active
  version: 0.1.0
  entrypoint: "operation:claims_show"
  risk_level: L0
  required_permissions: []
  connectors: []
  tags:
    - foundation
    - knowledge_access
    - foundation_core
---

<!-- omni-skill-stub: v0.40 -->

# Claims Lookup

Inspect a single ClaimLedger record by claim_id (bitemporal: shows t_valid_from/to + superseded_by chain).

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli claims-show [--help]
```

(See ``src/omni_hub/cli/`` for the argparse definition — every foundation
skill has a matching CLI subcommand by the same name.)

## When to use

The Trigger phrases above are intentionally narrow.  Foundation primitives
do not carry domain knowledge — if a query mentions a specific domain
(finance, fitness, cooking, etc.), route through ``chat-route`` first,
which will select the right ``*-wiki`` skill and feed the answer back.



## Hard rules

- Foundation skills NEVER write to ``vault/wiki/`` directly — all
  mutating paths land a ``Proposal[T]`` and wait for human approval.
- Foundation skills NEVER call an LLM directly.  Generation happens
  through claude/codex worker lanes — this primitive is the
  deterministic part of the loop.

---

_Auto-generated stub.  Remove the ``<!-- omni-skill-stub: v0.40 -->`` marker line to
opt out of regeneration (the v0.32 ``materialise_all`` rule)._
