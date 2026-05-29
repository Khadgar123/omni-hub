---
omni_type: domain_schema
domain: travel
schema_version: v0.20
stale_after_days: 180
---

# Travel Domain Schema

## Position

Destinations, itineraries, transit, lodging, visa, seasonal timing.  Highly seasonal — Japan cherry-blossom claims valid Mar-Apr only.  Connectors land in v0.20 (小红书 + 马蜂窝 + TripAdvisor + 携程).

## Authoritative Sources

- `xiaohongshu`
- `bilibili`
- `wikipedia`

## Optional Frontmatter

- `country_iso` — ISO 3166-1 alpha-3
- `city` — primary city / region
- `trip_length_days` — suggested itinerary length
- `season` — spring | summer | autumn | winter | year-round
- `budget_tier` — shoestring | mid | premium | luxury

## Stale Threshold

`wiki-lint --rule data_gap` uses **180 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Domain-Specific Lint Hints

- season + country combinations SHOULD trigger stale_fact when underlying season has passed by > 90d.
- visa / safety claims MUST cite government source (broken_cross_ref severity=high).

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
