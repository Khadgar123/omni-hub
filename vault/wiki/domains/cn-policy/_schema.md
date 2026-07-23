---
omni_type: domain_schema
domain: cn_policy
schema_version: v0.21
stale_after_days: 90
---

# China Policy Domain Schema

## Position

中国政策、法规、五年规划、各部委文件、中央财办、最高人民法院解释。Connectors land in v0.21 (gov.cn + 国务院 RSS + 各部委 + 央行).  Until then, pages here are populated manually via ``wiki-propose-research`` from user-curated PDF / link drops.  Companion to ``us_policy``; cross-references go through ``international_relations``.

## Authoritative Sources

- `gov_cn`
- `stats_gov_cn`
- `court_gov_cn`
- `pbc_gov_cn`
- `wikipedia`

## Optional Frontmatter

- `document_id` — 国务院 / 各部委文件号 (e.g. 国发〔2026〕12号)
- `five_year_plan` — 适用的五年规划 (e.g. 十四五 / 十五五)
- `regulator` — 发布机构 (国务院 / 央行 / 网信办 / 证监会 ...)
- `jurisdiction` — national | province-XX | municipality-XX

## Stale Threshold

`wiki-lint --rule data_gap` uses **90 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Domain-Specific Lint Hints

- 中国政策更新节奏季度级;stale_after_days=90.
- contradiction severity=high — 与 us_policy 镜像;跨语言来源差异常见但必须 reconcile.
- broken_cross_ref severity=high — 文件号 / 五年规划 引用必须可解析.

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
