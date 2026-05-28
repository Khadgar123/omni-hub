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
      research/
      engineering/
      photography/
      fashion/
      chat-relationships/
      finance/
      policy/
      international-relations/
      ai-progress/
      agent-systems/
.omni/
  claims.jsonl      # reviewed atomic claims
  context_packs/    # task-specific context bundles
```

`vault/wiki` is the human-readable source of compiled knowledge.  SQLite stores
remain control-plane state:

- `.omni/proposals.sqlite3` stores pending and reviewed changes.
- `.omni/memory.sqlite3` stores approved long-term memory and entity/relation
  fallback data.
- `.omni/claims.jsonl` stores reviewed atomic claims derived from approved wiki
  proposals.

## Commands

```bash
PYTHONPATH=src python -m omni_hub.cli wiki-init
PYTHONPATH=src python -m omni_hub.cli wiki-status
PYTHONPATH=src python -m omni_hub.cli wiki-search --query "context engineering"

PYTHONPATH=src python -m omni_hub.cli wiki-propose-research \
  --source paperbite \
  --path "analysis/ICLR_2026/Agentic_Context_Engineering_Evolving_Contexts_for_Self-Improving_Language_Models.md"

PYTHONPATH=src python -m omni_hub.cli propose-approve --id <proposal-id> --reason "reviewed"
PYTHONPATH=src python -m omni_hub.cli wiki-apply-proposal --proposal <proposal-id>

PYTHONPATH=src python -m omni_hub.cli context-pack-build \
  --query "context engineering agent memory" \
  --domain research \
  --persist
```

Agent-written wiki updates do not write directly to `vault/wiki`.  They create
`Proposal(kind="wiki_update")`; only approved proposals can be applied.

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
