"""ResearchFlow / PaperBite knowledge source commands."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    status = subparsers.add_parser(
        "research-kb-status",
        help="Show ResearchFlow / PaperBite knowledge source status.",
    )

    search = subparsers.add_parser(
        "research-kb-search",
        help="Search ResearchFlow and PaperBite indexes.",
    )
    search.add_argument("--query", required=True)
    search.add_argument(
        "--source",
        default="all",
        choices=["all", "researchflow", "paperbite"],
        help="Knowledge source to search.",
    )
    search.add_argument("--limit", type=int, default=10)

    read = subparsers.add_parser(
        "research-kb-read",
        help="Read one ResearchFlow / PaperBite analysis note by index path.",
    )
    read.add_argument("--source", required=True, choices=["researchflow", "paperbite"])
    read.add_argument("--path", required=True)
    read.add_argument("--max-chars", type=int, default=4000)

    skills = subparsers.add_parser(
        "researchflow-skills",
        help="List ResearchFlow skills available from the pinned module.",
    )


def _status(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="research_kb_status",
            action="inspect",
            payload={},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _search(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="research_kb_search",
            action="search",
            payload={
                "query": args.query,
                "source": args.source,
                "limit": args.limit,
            },
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _read(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="research_kb_read",
            action="read",
            payload={
                "source": args.source,
                "path": args.path,
                "max_chars": args.max_chars,
            },
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _skills(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="researchflow_skill_inventory",
            action="inspect",
            payload={},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "research-kb-status": _status,
    "research-kb-search": _search,
    "research-kb-read": _read,
    "researchflow-skills": _skills,
}
