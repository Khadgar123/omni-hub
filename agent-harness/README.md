# Agent Harness

This workspace is the self-evolution harness for Omni Hub.

It follows the same large-project model as `api-management`: the main repository owns the product contract, test fixtures, decision records, and pinned service revisions; each major subsystem stays in its own fork with its own upstream history.

## Forks

```text
agent-harness/swe-agent -> https://github.com/Khadgar123/SWE-agent
agent-harness/promptfoo -> https://github.com/Khadgar123/promptfoo
agent-harness/argilla   -> https://github.com/Khadgar123/argilla
agent-harness/graphiti  -> https://github.com/Khadgar123/graphiti
```

## Roles

- `swe-agent`: engineering iteration core for issue-to-patch loops.
- `promptfoo`: evaluation, prompt regression, RAG/agent CI, and red-team checks.
- `argilla`: human preference capture, accepted/rejected sentence datasets, and domain feedback.
- `graphiti`: temporal knowledge graph, provenance, redundancy detection, and memory evolution.

`Opik` is intentionally not pinned yet. It remains an observability candidate until we run a local pilot against Langfuse/Phoenix-style alternatives.

## Lifecycle

Bootstrap after clone:

```bash
make harness-setup
```

Fast-forward maintained forks from upstream:

```bash
make harness-update
make test
git commit -m "Update agent harness forks"
```

If a fork has local changes or the upstream merge is not fast-forward, resolve inside that fork, push the fork branch, then commit the bumped gitlink in `omni-hub`.

## Harness Loop

```text
task packet
  -> SWE-agent/code agent attempts
  -> promptfoo/domain evals
  -> traces/observability candidate
  -> Argilla human preference
  -> Graphiti memory/provenance update
  -> regression case + prompt/program update
```

The invariant is that human choices and failure cases become versioned data, not loose chat history.
