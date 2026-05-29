---
omni_type: domain_schema
domain: meta
schema_version: v0.20
stale_after_days: 60
---

# Meta (Self-Iteration) Domain Schema

## Position

The skill that improves omni-hub itself.  Corpus = own commit history + AGENTS.md / CLAUDE.md / docs/* + accepted PreferenceRecords across all other skills + open GitHub issues.  Outputs are pages documenting BUILD-vs-USE decisions, schema migration plans, cross-skill optimization wins, and proposed control-plane changes.  **Does not write to vault/wiki/ directly** — emits Proposal(kind=wiki_update) like every other skill, the irony being that meta-skill changes go through the same human gate as the skills it analyses.

## Authoritative Sources

- (none — purely reactive domain; manual ingest only)

## Optional Frontmatter

- `affects_modules` — list of src/omni_hub modules a meta page concerns
- `decision` — BUILD | USE | PIN-AS-FORK | DEFER | REJECT
- `triggered_by` — preference_drift | lint_pattern | user_request | commit_pattern

## Stale Threshold

`wiki-lint --rule data_gap` uses **60 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Domain-Specific Lint Hints

- meta pages MUST reference a specific module / commit / lint pattern.
- broken_cross_ref severity=high — meta links to code must point at real files.
- data_gap severity=low — meta knowledge accumulates, not depletes.

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
