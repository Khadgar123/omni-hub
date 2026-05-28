"""Omni Hub command-line entry.

Each subdomain lives in a sibling submodule that exports:

* ``register(subparsers)`` — register argparse subparsers for that domain
* ``COMMANDS`` — ``{command-name: handler}`` map for dispatch

Handlers receive ``(args, *, runner, workspace)`` and return an exit code.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..builtins import build_default_registry
from ..runner import OperationRunner
from . import (
    ab,
    api_management,
    app,
    argilla,
    capture,
    channel,
    claims,
    command,
    evals,
    event,
    finance,
    harness,
    inbox,
    judge,
    mcp,
    memory,
    meta,
    optimizer,
    policy,
    pptx,
    projects,
    propose,
    reports,
    research_kb,
    retrieve,
    projection,
    scheduling,
    skill,
    task,
    users,
    wiki,
    worker,
    workflow,
)

_AREAS = [
    capture, memory, skill, api_management, policy, argilla,
    propose, task, worker, harness, reports, optimizer, event, mcp,
    retrieve, research_kb, wiki, claims, command, workflow, projection,
    channel, app,                # v0.19 Interface + Application Plane
    judge,                       # v0.23 Judge LLM framework
    meta,                        # v0.28 Cross-skill transfer
    ab,                          # v0.29 A/B test framework
    users, scheduling, inbox,    # v0.39 — expose v0.31-v0.33 to CLI
    projects, pptx, finance,     # v0.39 — expose v0.34-v0.36 to CLI
    evals,                       # v0.41 — eval flywheel
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omni-hub",
        description="Run Omni Hub operations from the local workspace.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for area in _AREAS:
        area.register(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = Path.cwd()
    runner = OperationRunner(build_default_registry(workspace))

    for area in _AREAS:
        handler = area.COMMANDS.get(args.command)
        if handler is not None:
            return handler(args, runner=runner, workspace=workspace)

    raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
