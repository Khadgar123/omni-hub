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
    api_management,
    argilla,
    capture,
    claims,
    command,
    event,
    harness,
    mcp,
    memory,
    optimizer,
    policy,
    propose,
    reports,
    research_kb,
    retrieve,
    skill,
    task,
    wiki,
    worker,
)

_AREAS = [
    capture, memory, skill, api_management, policy, argilla,
    propose, task, worker, harness, reports, optimizer, event, mcp,
    retrieve, research_kb, wiki, claims, command,
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
