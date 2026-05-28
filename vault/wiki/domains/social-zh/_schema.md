---
omni_type: domain_schema
domain: social_zh
schema_version: v0.13
stale_after_days: 14
---

# Social (Chinese) Domain Schema

## Position

Tier-2 broker-routed Chinese social-media.  Xiaohongshu via jackwener/xiaohongshu-cli subprocess bridge; WeChat MP via self-hosted rachelos/we-mp-rss RSS.  Legal personal-use only, share-link parsing rather than scraping.

## Authoritative Sources

- `xiaohongshu`
- `wechat_mp`

## Optional Frontmatter

- `platform` — xiaohongshu | wechat_mp | weibo | other
- `post_id` — platform-native ID
- `author` — post author handle / public account name

## Stale Threshold

`wiki-lint --rule data_gap` uses **14 days** as the default for this domain.  Override per-page via frontmatter when the underlying fact has known longer/shorter validity.

## Domain-Specific Lint Hints

- DO NOT propose pages from auto-scrape; only manual share-link parse.
- broken_cross_ref severity=medium when post is deleted upstream (expected).

---

_Auto-generated from `src/omni_hub/domain_schemas.py`._  Edits will be overwritten on the next `wiki-init` when `schema_version` advances.  To customise: bump the version in code, do not hand-edit this file.
