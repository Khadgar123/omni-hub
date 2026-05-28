---
name: retrieve-evidence-pin
status: active
mode: persist-and-replay
description: |
  Persists a cascade run as `.omni/retrieval/<run_id>/{run_manifest.json,
  sources.json, evidence.jsonl}` so:
  - the harness ensemble can re-read provenance after context compaction,
  - propose-approve / propose-reject can replay what was shown to the model,
  - event_log stays the agent-step log; this is the evidence log.

  Use this skill when the user asks the agent to:
  - "save this retrieval for replay"
  - "what did we cite in run X"
  - "rebuild evidence for the prior claim about Y"
  - "list the recent retrieve runs"

  Mirrors the PaperQA2 + deep-research-skill pattern: evidence is a
  first-class artifact, not throwaway model context.
license: MIT
---

# Retrieve — Evidence Pin

## What it does

Writes / reads per-run evidence on disk:

```
.omni/retrieval/<run_id>/
    run_manifest.json   { query, domain, fusion, timestamps, source diagnostics }
    sources.json        deduped URLs + by_cite_id lookup
    evidence.jsonl      one RetrievalRecord per line
```

`run_id` defaults to `<UTC-timestamp>-<8-char hex>`, sortable. A custom
`run_id` (e.g. `task-42-retrieve-1`) threads evidence to a queue task.

## Write — from CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli retrieve \
  --query "..." --domain research --persist-evidence \
  [--run-id task-42-retrieve-1]
```

The CLI prints the run_id and the three file paths in the JSON output's
`evidence` block.

## Read — list runs

```bash
ls -t .omni/retrieval/                       # newest first
PYTHONPATH=src python3 -c \
  "from omni_hub.retrieval import EvidenceStore; \
   print(EvidenceStore('.').list_runs(limit=10))"
```

## Read — one run

```bash
cat .omni/retrieval/<run_id>/run_manifest.json     # what was asked
cat .omni/retrieval/<run_id>/sources.json          # unique URLs + cite_id map
head -5 .omni/retrieval/<run_id>/evidence.jsonl    # records
```

## Read programmatically

```python
from omni_hub.retrieval import EvidenceStore
store = EvidenceStore(".")
manifest = store.read_manifest("20260528T013300Z-aabbccdd")
# evidence.jsonl is one record per line — stream it
import json, pathlib
path = pathlib.Path(".omni/retrieval/<run_id>/evidence.jsonl")
for line in path.read_text(encoding="utf-8").splitlines():
    record = json.loads(line)
    ...
```

## When to use vs the router

- Use this skill when the task **is** the evidence (replay, audit, propose).
- Use the `retrieve` router when the task is a fresh search.
- Use directly when wiring a worker that should always persist — pass
  `extra_manifest={"task_id": "...", "lane": "..."}` so the evidence
  manifest carries queue-task provenance.

## Anti-patterns

- **Do not** persist evidence for every cascade — only when records will
  be cited downstream. The default in `retrieve-cascade` is `persist=False`.
- **Do not** edit evidence files after write. Treat them as append-only
  artifacts; supersede with a NEW run, never amend.
- **Do not** put secrets / API keys in `extra_manifest`. The manifest is
  on disk in plaintext and is meant to be agent-readable.
