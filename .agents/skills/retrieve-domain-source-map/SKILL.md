---
name: retrieve-domain-source-map
status: active
mode: lookup-only
description: |
  Picks the per-domain cascade source list for a query, given the domain
  profile (engineering / research / photography / fashion / chat_relationships
  / finance / policy / international_relations / ai_progress / default).

  Use this skill when the user asks the agent to:
  - "what sources will the cascade hit for X domain"
  - "is GDELT in the policy cascade"
  - "show me the source map"
  - is debugging an empty-result retrieve and needs to see the planned plan.

  This is pure metadata — no HTTP, no parsing. It just reads
  `src/omni_hub/retrieval/cascade.py::DEFAULT_DOMAIN_CASCADES`.
license: MIT
---

# Retrieve — Domain Source Map

## What it does

Returns the cascade order for one domain. Used as a debugging / explanation
tool before / instead of running a cascade.

## Invocation

```bash
PYTHONPATH=src python3 -c \
  "from omni_hub.retrieval.cascade import DEFAULT_DOMAIN_CASCADES as d; \
   import json; print(json.dumps(d, indent=2))"
```

Or, programmatically from the agent:

```python
from omni_hub.retrieval import DEFAULT_DOMAIN_CASCADES
cascade_order = DEFAULT_DOMAIN_CASCADES.get(domain, DEFAULT_DOMAIN_CASCADES["default"])
```

## Domain → cascade table (2026-Q2, v0.11)

See the `retrieve` router skill's source-map table for the canonical list.
Domains added in v0.10:
- `photography` gains unsplash + pexels
- `finance` gains edgar + fred (pinned from anthropics/financial-services)
- `policy` gains federal_register + regulations_gov + congress_gov
- `international_relations` gains acled + world_bank + imf
- `ai_progress` gains hf_daily_papers
Domains expanded in v0.11 for global-truth coverage:
- `default` gains wikidata + brave_search + crossref
- `research` gains crossref + wikidata
- `engineering` gains brave_search + crossref + wikidata
- `policy` and `international_relations` gain brave_search + wikidata
- `finance` gains crossref + wikidata

## When to use

- Before running an expensive cascade — verify the source list matches
  expectation.
- When `sources_succeeded` is empty in a result — the domain may be
  routing to sources that aren't registered.
- When designing a new domain profile — copy from an existing one.

## Anti-patterns

- **Do not** hard-code source lists in agent prompts. Always read from
  `DEFAULT_DOMAIN_CASCADES` so domain map changes propagate.
- **Do not** edit `DEFAULT_DOMAIN_CASCADES` in a worker session.  The
  source map is part of the cascade contract — modify only via a PR.
