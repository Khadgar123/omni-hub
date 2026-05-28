# ResearchFlow / PaperBite Integration

## Position

`ResearchFlow` is the workflow and skill layer. It owns collection, download,
MinerU-backed analysis, index build, query, ideation, focus, review, audit, and
export contracts.

`PaperBite` is the read-only evidence vault. It publishes the large Markdown /
JSONL / manifest layer produced from the ResearchFlow analysis framework.
Current pinned snapshot:

- `ResearchFlow`: `agent-harness/researchflow`, upstream
  `RipeMangoBox/ResearchFlow`, commit `1e6a9ed`.
- `PaperBite`: `agent-harness/paperbite`, upstream
  `RipeMangoBox/PaperBite`, commit `b0810fd`.
- `PaperBite` records: `951` index rows, `951` analysis notes in Git, ICLR
  2026 slice. On the current macOS checkout one case-colliding duplicate note
  is sparse-excluded from the working tree; `research-kb-read` can still read it
  from the submodule git object.

Both modules are pinned as gitlinks under `agent-harness/`. They are not copied
into the main repository and are not routed through a personal fork.

## ResearchFlow Skills

| Skill | Harness role | Notes |
| --- | --- | --- |
| `research-workflow` | router | Maps work to import / collect / download / analyze / build / query / ideate / focus / review / audit / export. |
| `papers-collect-from-web` | ingestion candidate source | Web paper candidate collection. |
| `papers-collect-from-github-repo` | ingestion candidate source | GitHub README / paper-list collection. |
| `papers-download-from-list` | ingestion materialization | Downloads and verifies PDFs into the vault layout. |
| `papers-batch-analyze` | worker task template | Splits CSV queues and runs batch paper analysis. |
| `paper-report` | structured generation target | Deep single-paper report contract. |
| `papers-build-index` | index compiler | Builds `index.jsonl` and Obsidian navigation pages. |
| `papers-query-knowledge-base` | retrieval skill | Query / compare over analysis notes. |
| `code-context-paper-retrieval` | engineering retrieval | Retrieves papers relevant to a code task. |
| `research-brainstorm-from-kb` | ideation generator | Generates ideas from local KB evidence. |
| `idea-emerge` | frontier idea generator | Combines KB, web papers, operators, traces, and constraints. |
| `idea-focus-coach` | proposal reducer | Narrows broad ideas into testable plans. |
| `reviewer-stress-test` | judge / critique | Reviewer-style pressure test with repair paths. |
| `papers-audit-metadata-consistency` | quality gate | Audits metadata, duplicates, structure, and link completeness. |
| `notes-export-share-version` | export gate | Produces shareable Markdown. |
| `rf-obsidian-markdown` | format contract | Obsidian Markdown rules for notes and indexes. |
| `domain-fork` | design reference | Maps ResearchFlow concepts into a new professional domain. |
| `skill-fit-guard` | skill quality feedback | Captures skill mismatch patterns for improvement. |
| `write-daily-log` | research ops reporting | Generates research decision logs. |

## Harness Loop

```text
TaskPacket / user research task
  -> research-kb-search over ResearchFlow + PaperBite
  -> context pack with cited analysis_path rows
  -> Claude / Codex / OpenHands worker generation
  -> GroundingReport: citation density + low-signal spans
  -> promptfoo regression / LLM-as-judge
  -> Proposal[T] for human approval
  -> Argilla accepted/rejected spans
  -> DSPy / GEPA / MIPRO compile
  -> ResearchFlow skill prompt update or task template update
  -> PaperBite / ResearchFlow index used in next retrieval pass
```

Quality boundary:

- ResearchFlow and PaperBite are read-only inputs to omni-hub by default.
- Agent workers may propose notes, skill changes, or derived datasets, but
  high-risk writes still go through `Proposal[T]`.
- PaperBite is not mutated by background workers. Updates happen by moving the
  pinned gitlink after upstream publishes a new evidence snapshot.

## Omni-Hub Knowledge Access

The main repository now has a stdlib-only adapter:

```text
src/omni_hub/research_assets.py
```

CLI surface:

```bash
PYTHONPATH=src python -m omni_hub.cli research-kb-status
PYTHONPATH=src python -m omni_hub.cli research-kb-search --query "agent context engineering" --source all --limit 5
PYTHONPATH=src python -m omni_hub.cli research-kb-read --source paperbite --path "analysis/ICLR_2026/<note>.md"
PYTHONPATH=src python -m omni_hub.cli researchflow-skills
```

This makes the ResearchFlow demo vault and PaperBite evidence vault part of the
omni-hub knowledge plane without copying their notes into `vault/`.

## Development Rule

Use `ResearchFlow` for workflow and skill changes. Use `PaperBite` as evidence
data. If the analysis schema changes upstream, update the pinned gitlinks and
adjust only the adapter contract in `research_assets.py`.
