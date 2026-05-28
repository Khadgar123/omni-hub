"""memory-* commands."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    digest = subparsers.add_parser("memory-digest-proposal")
    digest.add_argument("--proposal", required=True)

    memory_search = subparsers.add_parser("memory-search")
    memory_search.add_argument("--query", required=True)
    memory_search.add_argument("--limit", type=int, default=10)

    subparsers.add_parser("memory-stats")


def _digest(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="digest_proposal",
            action="digest_proposal",
            payload={"proposal": args.proposal},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _search(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="search_memory",
            action="search",
            payload={"query": args.query, "limit": args.limit},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _stats(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="memory_stats",
            action="stats",
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "memory-digest-proposal": _digest,
    "memory-search": _search,
    "memory-stats": _stats,
}
