---
omni_type: domain_schema
domain: marketing
schema_version: v0.20
stale_after_days: 60
---

# Marketing & Promotion Domain Schema

## Position

营销策略、文案模式、增长黑客、品牌定位、ROI 案例.  Fast cycle — weekly trending playbook shifts.  Connectors land in v0.20 (微博热搜 + 抖音 + 营销博主 RSS) + v0.22 (Crunchbase 增长案例).

## Authoritative Sources

- `weibo`
- `brave_search`
- `gdelt`
- `zhihu`
- `wikipedia`

## Optional Frontmatter

- `channel` — social | seo | content | email | paid-ads | influencer
- `industry` — saas | consumer | b2b | retail | fintech | ...
- `case_company` — company the case study is about
- `roi_metric` — the metric this playbook claims to move

## Stale Threshold

`wiki-lint --rule data_gap` uses **60 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Domain-Specific Lint Hints

- case studies > 6mo old SHOULD trigger stale_fact (severity=medium).
- ROI claims without source severity=high (broken_cross_ref).

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
