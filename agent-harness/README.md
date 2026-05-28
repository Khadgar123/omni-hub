# Agent Harness

This workspace is the self-evolution harness for Omni Hub.

It follows the same large-project model as `api-management`: the main repository owns the product contract, test fixtures, decision records, and pinned service revisions; each major subsystem stays in its own fork with its own upstream history.

## Active modules

```text
agent-harness/swe-agent -> https://github.com/Khadgar123/SWE-agent
agent-harness/promptfoo -> https://github.com/Khadgar123/promptfoo
agent-harness/argilla   -> https://github.com/Khadgar123/argilla
agent-harness/graphiti  -> https://github.com/Khadgar123/graphiti
agent-harness/researchflow -> https://github.com/RipeMangoBox/ResearchFlow
agent-harness/paperbite    -> https://github.com/RipeMangoBox/PaperBite
```

Roles:

- `swe-agent`: minimal issue-to-patch CI harness; kept for benchmark-comparable model evaluation (`mini-swe-agent` is ~100 LOC).
- `promptfoo`: evaluation, prompt regression, RAG/agent CI, red-team checks.
- `argilla`: human preference capture, accepted/rejected sentence datasets, domain feedback.
- `graphiti`: temporal knowledge graph, provenance, redundancy detection, memory evolution.
- `researchflow`: local-first structured paper analysis and Research Memory workflow. It owns the paper-analysis skills, MinerU-backed analysis chain, JSONL index builder, query, ideation, focus, review, and export flow.
- `paperbite`: read-only public evidence vault derived from ResearchFlow. It provides the large-scale Markdown/index/manifests layer for ICLR 2026 paper analysis assets.

`researchflow` and `paperbite` intentionally track the RipeMangoBox upstream
repositories directly because the user is a ResearchFlow contributor; they are
not routed through a Khadgar123 personal fork.

## Pending forks (2026-05 reassessment)

These three are declared in `manifest.json` as `pending_forks` because they are not yet personal forks on GitHub. They are **decided forks** — the only remaining step is creating the personal mirror and converting them into submodules.

```text
agent-harness/dspy       -> upstream stanfordnlp/dspy
agent-harness/openhands  -> upstream All-Hands-AI/OpenHands
agent-harness/opik       -> upstream comet-ml/opik
```

Roles:

- `dspy`: declarative prompt-program compiler (`BootstrapFewShot`, `MIPROv2`). Closes the loop from Argilla accepted/rejected back to next-version prompts. **This is the engine that turns "the model didn't write it well last time" into "the model writes it correctly next time" without touching weights.**
- `openhands`: production-grade engineering agent platform (~72k stars, ~66–77% SWE-bench Verified). Complements `swe-agent`: OpenHands for daily product work, SWE-agent for CI parity benchmarks.
- `opik`: trace / cost / latency / eval dashboard. Promoted from candidate to fork after the 2026 landscape review confirmed maturity vs Langfuse/Phoenix.

To promote them into real submodules:

1. Fork each upstream to your personal account (Khadgar123) on github.com.
2. Run `make harness-add-pending dspy openhands opik` (or `all`).
3. Commit the resulting `.gitmodules` and gitlink changes.

The script `scripts/add_pending_harness_forks.sh` is idempotent.

## Lifecycle

Bootstrap after clone:

```bash
make harness-setup
```

Fast-forward maintained modules from upstream:

```bash
make harness-update
make test
git commit -m "Update agent harness forks"
```

See pending forks and their roles:

```bash
make harness-status
```

If a fork has local changes or the upstream merge is not fast-forward, resolve inside that fork, push the fork branch, then commit the bumped gitlink in `omni-hub`.

## Harness Loop

```text
TaskPacket (src/omni_hub/harness/models.py)
  -> retrieval (Graphiti / vault / local files)
  -> ensemble generation (src/omni_hub/harness/ensemble.py via ccLoad)
  -> judge ensemble (promptfoo + bias audit)
  -> Argilla human preference
  -> DSPy compile -> next-version prompt program
  -> promptfoo regression
  -> Graphiti provenance + memory update
  -> Opik dashboard
```

The invariant is that human choices and failure cases become versioned data, not loose chat history. Forks may change over time; the **`TaskPacket` and `GenerationRecord` contracts** are the unchanging core.
