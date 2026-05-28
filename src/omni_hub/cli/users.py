"""User-plane CLI (v0.39).

Wraps the v0.31 UserProfileStore + 3-tier memory through OperationRunner
so multi-tenant identity + persona-block management lands in the
audit + policy chain.
"""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser("user-list", help="List enrolled users.")

    enroll = subparsers.add_parser(
        "user-enroll",
        help="Enroll a new user (PENDING until approved).",
    )
    enroll.add_argument("--handle", required=True)

    approve = subparsers.add_parser(
        "user-approve",
        help="Promote a PENDING user to ACTIVE.",
    )
    approve.add_argument("--user-id", required=True)

    persona = subparsers.add_parser(
        "user-set-persona",
        help="Set / overwrite a user's persona_block (Letta core memory).",
    )
    persona.add_argument("--user-id", required=True)
    persona.add_argument("--block", required=True,
                          help="Text or `file://path/to/block.md`")

    recall = subparsers.add_parser(
        "user-memory-recall",
        help="List a user's recent recall-tier memory entries.",
    )
    recall.add_argument("--user-id", required=True)
    recall.add_argument("--limit", type=int, default=50)

    arch = subparsers.add_parser(
        "user-memory-archival",
        help="Substring-search a user's archival memory.",
    )
    arch.add_argument("--user-id", required=True)
    arch.add_argument("--query", required=True)
    arch.add_argument("--limit", type=int, default=20)


def _user_list(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(name="user_list", action="list",
                       payload={}, risk_level=RiskLevel.READ_ONLY),
    )


def _user_enroll(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(name="user_enroll", action="enroll",
                       payload={"handle": args.handle},
                       risk_level=RiskLevel.LOCAL_WRITE),
    )


def _user_approve(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(name="user_approve", action="approve",
                       payload={"user_id": args.user_id},
                       risk_level=RiskLevel.LOCAL_WRITE),
    )


def _user_set_persona(args, *, runner, workspace) -> int:
    block = args.block
    if block.startswith("file://"):
        from pathlib import Path
        path = Path(block[len("file://"):])
        if not path.is_absolute():
            path = workspace / path
        block = path.read_text(encoding="utf-8")
    return run_and_print(
        runner,
        OperationSpec(name="user_set_persona", action="set_persona",
                       payload={"user_id": args.user_id, "block": block},
                       risk_level=RiskLevel.LOCAL_WRITE),
    )


def _user_memory_recall(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(name="user_memory_recall", action="recall",
                       payload={"user_id": args.user_id, "limit": args.limit},
                       risk_level=RiskLevel.READ_ONLY),
    )


def _user_memory_archival(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(name="user_memory_archival", action="search",
                       payload={"user_id": args.user_id,
                                "query": args.query, "limit": args.limit},
                       risk_level=RiskLevel.READ_ONLY),
    )


COMMANDS = {
    "user-list": _user_list,
    "user-enroll": _user_enroll,
    "user-approve": _user_approve,
    "user-set-persona": _user_set_persona,
    "user-memory-recall": _user_memory_recall,
    "user-memory-archival": _user_memory_archival,
}
