---
omni_type: domain_schema
domain: agent_systems
schema_version: v0.20
stale_after_days: 30
---

# Agent Systems Domain Schema

## Position

Agent frameworks, SDKs, harness modules.  Pages here document the BUILD-vs-USE decisions and the pinned forks under `agent-harness/`.

## Authoritative Sources

- `wikipedia`
- `openalex`
- `gdelt`

## Optional Frontmatter

- `framework` — framework name (Letta / DSPy / Graphiti / etc.)
- `version` — version pinned in agent-harness
- `decision` — BUILD | USE | PIN-AS-FORK | DEFER | REJECT

## Stale Threshold

`wiki-lint --rule data_gap` uses **30 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Pinned References

- agent-harness/dspy (pending fork — see agent-harness/manifest.json)
- agent-harness/openhands (pending fork)
- agent-harness/opik (pending fork)
- agent-harness/graphiti, agent-harness/argilla, agent-harness/promptfoo (pinned)

## Domain-Specific Lint Hints

- decision field MUST be one of the BUILD-vs-USE template enum values.
- broken_cross_ref severity=high — pinned forks must exist as submodules.

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
