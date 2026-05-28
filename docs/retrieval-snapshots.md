# Retrieval Snapshots — when the source isn't safe in the cascade hot path

Some sources are too brittle for live cascade calls but still valuable as
**periodic snapshots**: Selenium scrapers, paywalled APIs with quota,
sites that change DOM every quarter.  These get pinned under
`agent-harness/forks/` and refreshed on a schedule into the
`obsidian-vault/` snapshots layer, where `research-kb-*` commands can
query them.

This document defines the snapshot ritual + lists every snapshot source.

## When to snapshot vs cascade

| Property                              | Cascade source           | Snapshot source                  |
| ------------------------------------- | ------------------------ | -------------------------------- |
| Latency budget per call               | < 15 s wall              | hours (offline batch)            |
| Failure mode tolerated                | per-request retry        | quarterly re-run                 |
| Auth                                  | env var / paste cookie   | manual login session             |
| Output destination                    | `RetrievalRecord` stream | `obsidian-vault/snapshots/<src>` |
| Cascade integration                   | direct                   | indirect (via vault query)       |

Rules of thumb:

* Selenium / Playwright in the data path → snapshot only.
* DOM that changes per-season (Vogue Runway, lookbook sites) → snapshot.
* APIs with hard quotas where every retrieve call burns budget → snapshot.

## Snapshot sources (v0.10)

### Vogue Runway

* **Pinned fork:** `agent-harness/forks/vogue-runway-scraper` (upstream
  `TonyAssi/Vogue-Runway-Scraper`).
* **Cadence:** quarterly, after each fashion week (Feb / May / Sep / Nov
  closing day + 7).
* **Output layout:** `obsidian-vault/snapshots/vogue/<season>-<year>/
  <designer>/{looks.json, images/}`.
* **Cascade integration:** the `fashion` domain in `DEFAULT_DOMAIN_CASCADES`
  stays at `[wikipedia]`; snapshot data reaches the agent via
  `research-kb-search` over `obsidian-vault/snapshots/vogue/`.
* **Ritual:**

  ```bash
  cd agent-harness/forks/vogue-runway-scraper
  python -m vogue_runway_scraper --season "spring-2026" \
      --output ~/Desktop/简历/个人知识库/obsidian-vault/snapshots/vogue/spring-2026

  # then re-index the vault
  PYTHONPATH=src python3 -m omni_hub.cli research-kb-search --refresh
  ```

* **HF dataset fallback:** if scraping is broken on a given quarter, pull
  `tonyassi/vogue-fashion-collection-15` from HuggingFace Datasets as a
  static-snapshot rescue (loses the latest season but keeps history).

### (placeholder) future snapshot sources

* **WGSN trend reports** — paid; only after user authorises seat licence.
* **OECD iLibrary** — free but quota'd; quarterly snapshot of policy briefs.

## Anti-patterns

* **Do not** add Selenium / Playwright sources to `DEFAULT_DOMAIN_CASCADES`.
  The cascade fans out in 15 s wall-clock; a Selenium boot alone is 5 s.
* **Do not** copy snapshot output back into the cascade as a third party
  source.  Snapshots stay in the vault; the agent reads them through the
  vault path, never as a "retrieve" source.
* **Do not** check large image binaries into git.  Snapshots write to
  `obsidian-vault/snapshots/` which is .gitignored by default; rebuild
  on demand instead of versioning.
