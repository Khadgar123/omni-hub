---
name: context-pack
status: active-foundation
description: |
  Assemble a tier-bounded context bundle (minimal/standard/expanded) from vault/wiki + research-kb for a given query + domain.

  Triggers — invoke this skill when the user says any of:
  - "build a context pack for X"
  - "上下文打包 X domain"
  - "把 X 主题的 wiki 段抽出来"

  This is a **foundation primitive** (no domain knowledge baked in).  Use it
  as a building block from any other skill — see also ``app-report-build``,
  ``chat-route``, ``inbox-route``, and the 19 ``*-wiki`` domain skills.
license: MIT
schema_version: v0.40
omni_hub:
  layer: foundation
  namespace: foundation_core
  bucket: knowledge_access
  display_name: "Context Pack Builder"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors: []
  tags:
    - foundation
    - knowledge_access
    - foundation_core
---

<!-- omni-skill-stub: v0.40 -->

# Context Pack Builder

Assemble a tier-bounded context bundle (minimal/standard/expanded) from vault/wiki + research-kb for a given query + domain.

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli context-pack [--help]
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
