"""Karpathy-style wiki / context-pack commands."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser("wiki-init")
    subparsers.add_parser("wiki-status")

    search = subparsers.add_parser("wiki-search")
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=10)

    propose = subparsers.add_parser("wiki-propose-research")
    propose.add_argument("--source", required=True, choices=["researchflow", "paperbite"])
    propose.add_argument("--path", required=True)
    propose.add_argument("--domain", default="research")

    apply = subparsers.add_parser("wiki-apply-proposal")
    apply.add_argument("--proposal", required=True)

    pack = subparsers.add_parser("context-pack-build")
    pack.add_argument("--query", required=True)
    pack.add_argument("--domain", default="research")
    pack.add_argument("--wiki-limit", type=int, default=6)
    pack.add_argument("--research-limit", type=int, default=6)
    pack.add_argument("--persist", action="store_true")


def _init(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_init",
            action="init",
            payload={},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _status(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_status",
            action="status",
            payload={},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _search(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_search",
            action="search",
            payload={"query": args.query, "limit": args.limit},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _propose_research(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_propose_research",
            action="write_proposal",
            payload={
                "source": args.source,
                "path": args.path,
                "domain": args.domain,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _apply_proposal(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_apply_proposal",
            action="apply",
            payload={"proposal": args.proposal},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _context_pack(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="context_pack_build",
            action="build",
            payload={
                "query": args.query,
                "domain": args.domain,
                "wiki_limit": args.wiki_limit,
                "research_limit": args.research_limit,
                "persist": args.persist,
            },
            risk_level=RiskLevel.LOCAL_WRITE if args.persist else RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "wiki-init": _init,
    "wiki-status": _status,
    "wiki-search": _search,
    "wiki-propose-research": _propose_research,
    "wiki-apply-proposal": _apply_proposal,
    "context-pack-build": _context_pack,
}
