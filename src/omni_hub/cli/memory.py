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

    remember = subparsers.add_parser(
        "memory-remember",
        help="Pin a fact in core memory (Letta-style).",
    )
    remember.add_argument("--key", required=True)
    remember.add_argument("--value", required=True)
    remember.add_argument("--confidence", type=float, default=1.0)

    forget = subparsers.add_parser(
        "memory-forget",
        help="Remove a fact from core memory.",
    )
    forget.add_argument("--key", required=True)

    recall = subparsers.add_parser(
        "memory-recall",
        help="Read from a memory tier (core list / recall search / archival search).",
    )
    recall.add_argument("--tier", default="recall",
                         choices=["core", "recall", "archival"])
    recall.add_argument("--query", default="",
                         help="Search query (required for recall + archival).")
    recall.add_argument("--limit", type=int, default=20)

    promote = subparsers.add_parser(
        "memory-promote-recall",
        help="Append a session summary to recall memory (preference flywheel hook).",
    )
    promote.add_argument("--content", required=True)
    promote.add_argument("--source-kind", default="preference")
    promote.add_argument("--source-id", default="")
    promote.add_argument("--score", type=float, default=0.0)


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


def _remember(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="memory_remember_core",
            action="remember",
            payload={
                "key": args.key,
                "value": args.value,
                "confidence": args.confidence,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _forget(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="memory_forget_core",
            action="forget",
            payload={"key": args.key},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _recall(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="memory_recall",
            action="recall",
            payload={"tier": args.tier, "query": args.query, "limit": args.limit},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _promote_recall(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="memory_promote_recall",
            action="promote_recall",
            payload={
                "content": args.content,
                "source_kind": args.source_kind,
                "source_id": args.source_id,
                "score": args.score,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


COMMANDS = {
    "memory-digest-proposal": _digest,
    "memory-search": _search,
    "memory-stats": _stats,
    "memory-remember": _remember,
    "memory-forget": _forget,
    "memory-recall": _recall,
    "memory-promote-recall": _promote_recall,
}
