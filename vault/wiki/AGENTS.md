---
omni_type: wiki_schema
schema_version: v0.19
---

# Omni Wiki Schema

This is the contract every agent reads before writing into `vault/wiki/`.
It is the Karpathy *schema layer* — the file that tells an LLM how the wiki
is structured. Editing this file is a wiki-wide change: bump `schema_version`
and append a migration note to `log.md`.

## Three-Layer Lineage

```
vault/raw/          append-only source material (retrieval cascade output)
vault/evidence/     parsed, normalised evidence (one record per source hit)
vault/wiki/         compiled, human-readable knowledge (THIS directory)
.omni/claims.jsonl  reviewed atomic claims, indexed by claim_id
```

The wiki is the **compiled layer**. It is rebuilt from raw+evidence on
`wiki-ingest`; it is NOT re-derived per query. The retrieval cascade
(`omni-hub retrieve`) is the upstream Ingest data source.

## Page Types

Every `.md` under `vault/wiki/` SHOULD declare a `page_type` in YAML
frontmatter. Exemptions: `AGENTS.md`, `index.md`, `log.md`.

| page_type     | Where                              | Purpose                                |
|---------------|------------------------------------|----------------------------------------|
| `concept`     | `concepts/<slug>.md`               | Named idea (e.g. context-engineering)  |
| `entity`      | `entities/<slug>.md`               | Person / org / product / model         |
| `event`       | `events/<slug>.md`                 | Conference / release / incident        |
| `method`      | `methods/<slug>.md`                | Technique / algorithm / pattern        |
| `synthesis`   | `syntheses/<slug>.md`              | Cross-source compiled findings         |
| `domain_page` | `domains/<domain>/<slug>.md`      | Deep page (paper, product, policy)     |

## Required Frontmatter

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: research | engineering | finance | us_policy | cn_policy
         | international_relations | ai_progress | photography | fashion
         | chat_relationships | agent_systems | social_en | social_zh
         | meta | fitness_wellness | cooking | travel | marketing
         | enterprise
claim_ids: [c_a1b2c3d4, c_e5f6g7h8]      # cross-ref into .omni/claims.jsonl
source_ids: [arxiv:2510.04618, doi:10.xx]  # canonical_id list backing this page
t_valid_from: 2026-05-28                  # when content becomes correct (bitemporal)
t_valid_to: null                          # null = still valid; set on supersede
superseded_by: null                       # path to replacement page, or null
confidence: high | medium | low
review_state: approved | proposed | conflict
---
```

`t_valid_from / t_valid_to` follow the Graphiti / Zep bitemporal model: old
facts are NEVER deleted, only closed by setting `t_valid_to`. This keeps the
audit trail intact and lets queries pin a viewing date.

## Linking Rules

- **Internal links use wiki style**: `[[other-page-slug]]` or
  `[[other-page-slug|Display Text]]`.
- **Never use absolute filesystem paths** to other wiki pages.
- **Evidence references use citation markers**: `[1]`, `[2]` corresponding to
  a trailing `## References` section listing `source_id` + `vault/evidence/...`
  path or external URL.
- **Cross-domain references** are allowed but both pages SHOULD declare each
  other in `claim_ids` to keep the graph consistent.

## Write Boundary

- **Agents propose, humans approve.** Agents write `Proposal(kind="wiki_update")`
  via `wiki-ingest` or `wiki-propose-research`. The proposal carries the
  target page body + a list of candidate claims. Only after human
  `propose-approve` and `wiki-apply-proposal` does content land in `vault/wiki/`.
- **Direct agent writes are forbidden.** The single exception: `log.md` is
  append-only and may be written by `wiki-log` operations as an audit trail.
- **Manual edits are first-class.** A human may edit any wiki page directly
  in Obsidian / a text editor. After manual edits, run `wiki-lint` to surface
  inconsistencies (broken refs, stale claims, conflicts).

## Lint Rules (`wiki-lint`)

`wiki-lint` produces `Proposal(kind="lint_finding")` for each issue. Rules:

1. **Contradiction** — two claims sharing a statement key but opposite stance
   (one in `support`, one in `against`).
2. **Stale fact** — page with `t_valid_to < now()` and no `superseded_by`.
3. **Orphan page** — page with no inbound `[[...]]` link from `index.md` or
   from any other page.
4. **Missing concept page** — a claim references an entity/concept slug that
   has no dedicated page under `concepts/` or `entities/`.
5. **Broken cross-ref** — frontmatter `claim_ids` entry absent from
   `.omni/claims.jsonl`.
6. **Data gap** — page tagged `confidence: low` for > 30 days with no
   subsequent `wiki-ingest` enrichment.

## Log Format

`log.md` is append-only and chronological. Each entry header MUST be:

```
## [YYYY-MM-DDTHH:MM:SSZ] op | one-line summary
- source: <path | proposal_id | run_id>
```

Where `op` is one of: `ingest`, `apply`, `lint`, `supersede`, `conflict-resolve`,
`manual`. Tail with `grep "^## \[" vault/wiki/log.md | tail -10`.

## Index Format

`index.md` is content-oriented navigation, not chronological. New entries
auto-append on `wiki-apply-proposal`. Edit by hand to add topical groupings,
"see also" sections, or to demote noisy pages.

## Domain Sub-Schemas

A domain MAY override or extend this schema by writing
`domains/<domain>/_schema.md`. Domain sub-schemas can:

- Add required frontmatter fields for that domain (e.g. research domain may
  require `paper_link`).
- Declare authoritative source priorities (e.g. policy domain prefers
  `federal_register` over `gdelt`).
- Define domain-specific lint rules.

A page's domain sub-schema takes precedence over this global schema where they
conflict; the global schema sets the floor.
