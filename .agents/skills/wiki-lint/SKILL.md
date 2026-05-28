---
name: wiki-lint
status: active-foundation
description: |
  Eight-rule scan (contradiction / stale_fact / orphan_page / missing_concept / broken_cross_ref / data_gap / cross_ref_asymmetry / abandoned_page) — emits Proposal(lint_finding) per issue.

  Triggers — invoke this skill when the user says any of:
  - "lint the wiki"
  - "wiki-lint --persist"
  - "scan for contradictions in the wiki"

  This is a **foundation primitive** (no domain knowledge baked in).  Use it
  as a building block from any other skill — see also ``app-report-build``,
  ``chat-route``, ``inbox-route``, and the 19 ``*-wiki`` domain skills.
license: MIT
schema_version: v0.40
omni_hub:
  layer: foundation
  namespace: foundation_write
  bucket: knowledge_update
  display_name: "Wiki Lint"
  status: active
  version: 0.1.0
  entrypoint: "operation:wiki_lint"
  risk_level: L1
  required_permissions: []
  connectors: []
  tags:
    - foundation
    - knowledge_update
    - foundation_write
---

<!-- omni-skill-stub: v0.40 -->

# Wiki Lint

Eight-rule scan (contradiction / stale_fact / orphan_page / missing_concept / broken_cross_ref / data_gap / cross_ref_asymmetry / abandoned_page) — emits Proposal(lint_finding) per issue.

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli wiki-lint [--help]
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
