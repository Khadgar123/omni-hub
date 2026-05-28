# Project-level Agent Skills

This directory holds **business-specific** skills for `omni-hub`. Generic
methodology skills (TDD, debugging, code review) live in the global
`~/.agents/skills/` and are reached via the symlinks below.

## Layout

```text
.agents/skills/                  ← single source of truth (this dir)
.claude/skills  -> .agents/skills   (symlink, for Claude Code)
.codex/skills   -> .agents/skills   (symlink, for Codex CLI)
```

To create the symlinks for a fresh clone, run `./install-skills.sh` from the
repo root. The same script also sets up the home-directory single source of
truth at `~/.agents/skills/`.

## What to put here

Only skills tightly coupled to **this repository's domain**: API management,
vault capture, memory search, registry orchestration. One subdirectory per
skill, each with its own `SKILL.md`.

| skill id                       | scope                                                                |
| ------------------------------ | -------------------------------------------------------------------- |
| api-management-status          | inspect metapi + ccLoad local stack                                  |
| retrieve                       | router for the federated Retrieval Plane (cascade + RRF + grader)    |
| retrieve-cascade               | run cascade with parallel fan-out + RRF / concat fusion              |
| retrieve-domain-source-map     | pure lookup — which sources serve which domain                       |
| retrieve-grade-and-fuse        | CRAG-style grader + RRF post-cascade quality gate                    |
| retrieve-evidence-pin          | persist `.omni/retrieval/<run_id>/{manifest,sources,evidence.jsonl}` |
| url-capture (TODO)             | wrap omni_hub.cli capture-url                                        |
| memory-search (TODO)           | wrap omni_hub.cli memory-search                                      |
| skill-registry (TODO)          | wrap omni_hub.cli skill-register / skill-list                        |

## Anti-patterns

- **Do not** put `tdd`, `code-review`, `systematic-debugging` here. Those are
  community skills and belong to `~/.agents/skills/` so all your projects share
  the same version.
- **Do not** copy SKILL.md content from upstream repos by hand — let the
  installer clone them so `git pull` keeps them fresh.
- **Do not** put real API keys, tokens, or upstream credentials in any
  SKILL.md. They are agent-readable.

## Cross-reference

Each business skill here should also have a matching entry in
`registry/skills.json` so the `skill-recommend` / `skill-analyze` CLI in
`omni_hub` can reason about it alongside the markdown-only ones.
