---
name: memory-search-foundation
status: active-foundation
description: |
  Query archival memory across documents / entities / relations by case-insensitive substring (stdlib FTS).

  Triggers — invoke this skill when the user says any of:
  - "search memory for X"
  - "memory 里提过 X 吗"
  - "what do we remember about X"

  This is a **foundation primitive** (no domain knowledge baked in).  Use it
  as a building block from any other skill — see also ``app-report-build``,
  ``chat-route``, ``inbox-route``, and the 19 ``*-wiki`` domain skills.
license: MIT
schema_version: v0.40
omni_hub:
  layer: foundation
  namespace: foundation_core
  bucket: knowledge_access
  display_name: "Memory Search (Foundation)"
  status: active
  version: 0.1.0
  entrypoint: "operation:search_memory"
  risk_level: L0
  required_permissions: []
  connectors: []
  tags:
    - foundation
    - knowledge_access
    - foundation_core
---

<!-- omni-skill-stub: v0.40 -->

# Memory Search (Foundation)

Query archival memory across documents / entities / relations by case-insensitive substring (stdlib FTS).

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli memory-search-foundation [--help]
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
