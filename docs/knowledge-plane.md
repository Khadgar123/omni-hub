# Knowledge Plane

## Position

Omni Hub now treats the knowledge base as a Karpathy-style compiled wiki, not
as a bare RAG chunk store and not as a SQLite-only memory database.

The durable shape is:

```text
raw sources -> evidence notes -> compiled wiki -> claims ledger
            -> context packs -> eval / preference / optimizer flywheel
```

The goal is an evidence-grounded, provenance-preserving, conflict-aware
knowledge base.  "Truth" is represented as reviewed claims with source support,
uncertainty, time, and conflict state, not as a single model summary.

## Local Layout

`wiki-init` creates the first local layout:

```text
vault/
  raw/              # append-only source material
  evidence/         # parsed or normalized evidence
  wiki/
    AGENTS.md       # schema: how agents maintain the wiki
    index.md        # content-oriented navigation
    log.md          # append-only operation timeline
    claims/         # claim-oriented pages
    concepts/
    entities/
    events/
    methods/
    syntheses/
    domains/
      research/_schema.md      # domain sub-schema (auto-generated, v0.13)
      engineering/_schema.md
      photography/_schema.md
      fashion/_schema.md
      chat-relationships/_schema.md
      finance/_schema.md
      policy/_schema.md
      international-relations/_schema.md
      ai-progress/_schema.md
      agent-systems/_schema.md
      social-en/_schema.md      # Tier-2 paid, opt-in
      social-zh/_schema.md      # Tier-2 broker-routed, opt-in
.omni/
  claims.jsonl      # reviewed atomic claims with bitemporal fields
  context_packs/    # task-specific context bundles
  retrieval/        # per-cascade-run evidence (drives wiki-ingest)
```

Each `_schema.md` declares authoritative sources for that domain, required +
optional frontmatter, per-domain `stale_after_days` (research=730d, finance=30d,
international_relations=7d, ai_progress=14d, …), and domain-specific lint hints.
Edit by bumping `schema_version` in `src/omni_hub/domain_schemas.py`; the file
is auto-refreshed on the next `wiki-init` when the marker advances.  Hand-edits
made under the current version are preserved.

`vault/wiki` is the human-readable source of compiled knowledge.  SQLite stores
remain control-plane state:

- `.omni/proposals.sqlite3` stores pending and reviewed changes.
- `.omni/memory.sqlite3` stores approved long-term memory and entity/relation
  fallback data.
- `.omni/claims.jsonl` stores reviewed atomic claims derived from approved wiki
  proposals.

## Commands

Lifecycle (Karpathy Ingest → Query → Lint):

```bash
# Setup — idempotent, refreshes AGENTS.md + 12 domain _schema.md when versions advance.
PYTHONPATH=src python -m omni_hub.cli wiki-init
PYTHONPATH=src python -m omni_hub.cli wiki-status

# Ingest from a retrieval cascade run.
PYTHONPATH=src python -m omni_hub.cli retrieve --query "..." --persist-evidence
PYTHONPATH=src python -m omni_hub.cli wiki-ingest --run-id <run-id> --domain ai_progress

# Or ingest a single PaperBite/ResearchFlow analysis note.
PYTHONPATH=src python -m omni_hub.cli wiki-propose-research \
  --source paperbite --path "analysis/ICLR_2026/<note>.md"

# Review + apply.
PYTHONPATH=src python -m omni_hub.cli propose-list --kind wiki_update --state pending
PYTHONPATH=src python -m omni_hub.cli propose-approve --id <pid> --reason "reviewed"
PYTHONPATH=src python -m omni_hub.cli wiki-apply-proposal --proposal <pid>

# Query (bitemporal + state filter on by default).
PYTHONPATH=src python -m omni_hub.cli wiki-search --query "context engineering"
PYTHONPATH=src python -m omni_hub.cli wiki-search --query "..." --include-closed

# Browse the claims ledger.
PYTHONPATH=src python -m omni_hub.cli claims-stats
PYTHONPATH=src python -m omni_hub.cli claims-list --domain research --state approved
PYTHONPATH=src python -m omni_hub.cli claims-show --id <claim-id>

# Lint — six rules (contradiction / stale_fact / orphan_page / missing_concept /
# broken_cross_ref / data_gap).  --persist promotes findings to Proposal(kind=lint_finding).
PYTHONPATH=src python -m omni_hub.cli wiki-lint --persist
PYTHONPATH=src python -m omni_hub.cli wiki-lint --rule contradiction --domain research

# Conflict resolution (Graphiti bitemporal close, claims are never deleted).
PYTHONPATH=src python -m omni_hub.cli wiki-conflict-resolve \
  --proposal <lint-finding-id> --decision supersede
PYTHONPATH=src python -m omni_hub.cli wiki-supersede --new <new-id> --old <old-id> \
  --reason "newer paper is canonical"

# Audit log (Karpathy log.md format).
PYTHONPATH=src python -m omni_hub.cli wiki-log --op manual --summary "..." --source <ref>

# Context-pack with progressive disclosure (Anthropic Skills pattern).
PYTHONPATH=src python -m omni_hub.cli context-pack-build \
  --query "context engineering agent memory" \
  --domain research --tier expanded --persist
```

Agent-written wiki updates do not write directly to `vault/wiki`.  They create
`Proposal(kind="wiki_update")` or `Proposal(kind="lint_finding")`; only approved
proposals can land in the wiki / claims ledger.

## Bitemporal Claims (Graphiti / Zep Pattern)

Each entry in `.omni/claims.jsonl` carries:

```json
{
  "claim_id": "<16-char hex>",
  "domain": "research",
  "statement": "...",
  "support": [{"source_id": "arxiv:2510.04618", "cite_id": "R1", "url": "..."}],
  "against": [],
  "confidence": 0.85,
  "review_state": "approved | proposed | conflict | rejected | superseded",
  "t_valid_from": "2026-05-28T...",
  "t_valid_to": null,                            // null = open; ISO ts = closed
  "supersedes": ["<older-claim-id>", ...],
  "superseded_by": null | "<newer-claim-id>",
  "uncertainty": "...",
  "proposal_id": "...",
  "target_path": "vault/wiki/<...>.md"
}
```

`wiki-supersede` closes the old claim's `t_valid_to` and links the chain — old
claims are NEVER deleted, only window-closed.  This satisfies audit/replay
requirements (EU AI Act-style) without graph-DB infrastructure.

## Progressive Disclosure Context Packs

`context-pack-build --tier` follows the Anthropic Skills pattern:

| Tier | Per-result content | Approx tokens |
|---|---|---|
| `minimal` | page frontmatter only (page_type, domain, claim_ids, t_valid_from/to) | ~1k |
| `standard` | + 320-char snippet around match | ~5k |
| `expanded` | + body excerpt up to 8000 chars (frontmatter stripped) | up to ~30k |

The pack carries `char_budget` and `total_chars` so the caller can verify it
fits the model's context.

## Retrieval Sources

The live retrieval cascade is the upstream ingest path for claim evidence.  The
global/default profile now combines structured entity anchors, encyclopedia
grounding, optional broad-web discovery, scholarly DOI metadata, scholarly work
search, and recent-news discovery:

```text
default -> wikidata -> wikipedia -> brave_search -> crossref -> openalex
        -> gdelt -> internet_archive
```

Domain profiles can override the order.  For example, `research` leads with
Crossref/OpenAlex/Semantic Scholar/arXiv and then biomedical indexes
(`europe_pmc`, `pubmed`), while `policy` leads with federal primary sources,
CourtListener, broad web/news, and archive fallback.  Dedicated profiles also
exist for `biomedical`, `law`, and `statistics`.

Key-gated sources stay in the registry but fail-soft when unset:
`brave_search` uses `BRAVE_SEARCH_API_KEY`; `data_commons` uses
`DATACOMMONS_API_KEY`.  Polite-but-anonymous sources include `crossref`
(`CROSSREF_MAILTO` recommended), `pubmed` (`NCBI_EMAIL` recommended), and
`courtlistener` (`COURTLISTENER_TOKEN` optional for higher limits).

## ResearchFlow Role

ResearchFlow and PaperBite feed the research domain:

```text
ResearchFlow / PaperBite evidence
  -> wiki-propose-research
  -> Proposal(kind="wiki_update")
  -> human approve
  -> vault/wiki/domains/research/*
  -> .omni/claims.jsonl
  -> context-pack-build
  -> promptfoo / Argilla / DSPy-GEPA loop
```

ResearchFlow remains the specialized research workflow and paper analysis
engine.  PaperBite remains the read-only public evidence vault.  Omni Hub
compiles selected evidence into the global wiki instead of bulk-copying every
paper note into `.omni/memory.sqlite3`.

## Daily Schedule Integration

`schedule-tick --period daily` enqueues `wiki-lint --persist` alongside the
existing redundancy scan + daily report (see `scripts/launchd/*daily*.plist`).
Findings land as `Proposal(kind=lint_finding)` for next-morning review via
`propose-list --kind lint_finding --state pending`.

## MCP Surface

Claude desktop / other MCP clients see the following wiki/claims tools (full
list via `omni-hub mcp-serve` + `tools/list`):

- `wiki-status`, `wiki-search`, `wiki-ingest`, `wiki-lint`
- `claims-list`, `claims-show`, `claims-stats`
- `context-pack-build`
- `propose-list`, `propose-approve`, `propose-reject` (already exposed; now
  cover `wiki_update` and `lint_finding` kinds)

`wiki-ingest` is marked `idempotentHint=true` (run_id de-dupes); the rest
follow their standard risk-level annotation (READ_ONLY → `readOnlyHint=true`).

## Enterprise Boundary

The main repository owns the contracts, audit, Proposal gate, local wiki
schema, and context-pack assembly.  Heavy systems stay modular:

- `qmd` can become the Markdown search sidecar.
- `Graphiti` can ingest approved claims and temporal facts.
- `promptfoo` runs regression and red-team checks.
- `Argilla` stores human span-level preferences.
- `DSPy` / `GEPA` compile accepted behavior back into skills.
- `Opik` tracks traces, cost, latency, and eval experiments.

This keeps the core reproducible while allowing mature external projects to
evolve independently.
