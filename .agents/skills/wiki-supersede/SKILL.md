---
name: wiki-supersede
status: active-foundation
description: |
  Close an old claim's t_valid_to window + link superseded_by; never deletes (Graphiti/Zep pattern).

  Triggers — invoke this skill when the user says any of:
  - "supersede claim X with Y"
  - "wiki-supersede --new Y --old X"
  - "废弃旧 claim 改成新版本"

  This is a **foundation primitive** (no domain knowledge baked in).  Use it
  as a building block from any other skill — see also ``app-report-build``,
  ``chat-route``, ``inbox-route``, and the 19 ``*-wiki`` domain skills.
license: MIT
schema_version: v0.38
omni_hub:
  layer: foundation
  bucket: knowledge_update
  display_name: "Wiki Supersede (Bitemporal Close)"
  status: active
  version: 0.1.0
  entrypoint: "operation:wiki_supersede"
  risk_level: L1
  required_permissions: []
  connectors: []
  tags:
    - foundation
    - knowledge_update
---

<!-- omni-skill-stub: v0.38 -->

# Wiki Supersede (Bitemporal Close)

Close an old claim's t_valid_to window + link superseded_by; never deletes (Graphiti/Zep pattern).

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli wiki-supersede [--help]
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

_Auto-generated stub.  Remove the ``<!-- omni-skill-stub: v0.38 -->`` marker line to
opt out of regeneration (the v0.32 ``materialise_all`` rule)._
