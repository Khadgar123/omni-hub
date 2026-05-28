---
name: finance-wiki
status: active-domain
description: |
  Markets / filings / rates / risk disclosure.

  Triggers — invoke this skill when the user asks any of:
  - "NVDA 财报"
  - "美联储利率路径"
  - "A 股新能源板块"
  - "how to read a 10-K"

  Source corpus: vault/wiki/domains/finance/.  Authoritative
  cascade: `edgar`, `fred`, `openalex`, `wikipedia`.  Stale threshold: 30 days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: v0.38
omni_hub:
  kind: domain_wiki
  display_name: "Finance — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
  connectors:
    - edgar
    - fred
    - openalex
    - wikipedia
  tags:
    - wiki
    - domain
    - finance
  inputs:
    query: "user question text"
    domain: "finance"
    tier: "minimal | standard | expanded"
  outputs:
    context_pack: "ContextPack with cited wiki + research results"
---

<!-- omni-skill-stub: v0.38 -->

# Finance — Wiki Domain Skill

This is the **finance** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["finance"]`.

> SEC filings, central-bank time-series, scholarly finance.  Data moves quarterly (10-K) or monthly (FRED); short stale threshold.

## When to use

Triggers (subset):

- "NVDA 财报"
  - "美联储利率路径"
  - "A 股新能源板块"
  - "how to read a 10-K"

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \
  --query "..." --domain finance --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain finance --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \
  --run-id <run_id> --domain finance

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/finance/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/finance/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

- stale_fact severity=high: outdated financial data is dangerous.
- broken_cross_ref on cik/ticker MUST be repaired before next ingest.

### Severity overrides

  - `broken_cross_ref` → **high**
  - `data_gap` → **high**
  - `stale_fact` → **high**

## Required frontmatter on new pages

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: finance
# required (domain-specific)
period: ...   # data period (e.g. 2026-Q1)
# optional (domain-specific)
# ticker: ...   # stock ticker, e.g. NVDA
# cik: ...   # SEC central index key
# fred_series_id: ...   # FRED series identifier
# currency: ...   # ISO 4217 code
# global bitemporal
t_valid_from: YYYY-MM-DD
t_valid_to: null
review_state: approved | proposed | conflict
---
```

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/finance.jsonl`.  `harness-compile-skill --domain
finance` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`<!-- omni-skill-stub: v0.38 -->` marker line to opt out of future regenerations._
