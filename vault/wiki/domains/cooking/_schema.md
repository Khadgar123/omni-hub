---
omni_type: domain_schema
domain: cooking
schema_version: v0.20
stale_after_days: 730
---

# Cooking Domain Schema

## Position

中餐 / 西餐 / 烘焙 / 发酵 / 食材保鲜.  Receptive domain: 小红书 + Bilibili 美食 + 下厨房 + Allrecipes (英文) provide candidate recipes; user feedback (complete-and-rate) drives the PreferenceStore.  Connectors land in v0.20.  Each recipe page tracks substitutions + per-step constraints.

## Authoritative Sources

- `xiaohongshu`
- `bilibili`
- `wikipedia`

## Optional Frontmatter

- `cuisine` — chinese-sichuan | chinese-cantonese | italian | japanese | thai | ...
- `technique` — braise | stir-fry | bake | ferment | sous-vide | ...
- `difficulty` — beginner | intermediate | advanced
- `time_active_min` — active cooking time in minutes
- `time_total_min` — total time including waiting

## Stale Threshold

`wiki-lint --rule data_gap` uses **730 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Domain-Specific Lint Hints

- Recipe pages SHOULD link to at least one source video / blog (broken_cross_ref severity=low).
- data_gap severity=low — cooking knowledge is durable.

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
