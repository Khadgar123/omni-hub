---
omni_type: domain_schema
domain: social_en
schema_version: v0.20
stale_after_days: 14
---

# Social (English) Domain Schema

## Position

Tier-2 paid/broker social-media domain.  Opt-in only — no default cascade hit.  twitterapi.io paid lane.  Reactive: pages mostly from user-shared links + GDELT news context.

## Authoritative Sources

- `x_twitter`
- `gdelt`

## Optional Frontmatter

- `platform` — x | reddit | hn | other
- `post_id` — platform-native ID
- `author` — post author handle

## Stale Threshold

`wiki-lint --rule data_gap` uses **14 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Domain-Specific Lint Hints

- data_gap is expected — social pages reflect a moment, not a process.
- missing attribution / post_id = broken_cross_ref severity=medium.

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
