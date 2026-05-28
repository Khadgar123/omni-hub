"""propose-list / propose-approve / propose-reject commands.

The ``propose-note`` command (which builds a knowledge proposal from a
vault note) still lives under ``cli/capture.py`` because it operates on a
vault path; the commands here are the generic review surface for any
proposal kind already in the store.
"""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    list_p = subparsers.add_parser(
        "propose-list",
        help="List proposals from the unified store (filter by state/kind).",
    )
    list_p.add_argument("--state", choices=["pending", "approved", "rejected"])
    list_p.add_argument("--kind")
    list_p.add_argument("--limit", type=int, default=50)

    approve = subparsers.add_parser(
        "propose-approve",
        help="Mark a proposal as approved.",
    )
    approve.add_argument("--id", required=True)
    approve.add_argument("--reason", default="")
    approve.add_argument("--decided-by", default="local-user")

    reject = subparsers.add_parser(
        "propose-reject",
        help="Mark a proposal as rejected.",
    )
    reject.add_argument("--id", required=True)
    reject.add_argument("--reason", default="")
    reject.add_argument("--decided-by", default="local-user")


def _list(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="list_proposals",
            action="list",
            payload={"state": args.state, "kind": args.kind, "limit": args.limit},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _approve(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="approve_proposal",
            action="approve",
            payload={
                "proposal_id": args.id,
                "reason": args.reason,
                "decided_by": args.decided_by,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _reject(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="reject_proposal",
            action="reject",
            payload={
                "proposal_id": args.id,
                "reason": args.reason,
                "decided_by": args.decided_by,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


COMMANDS = {
    "propose-list": _list,
    "propose-approve": _approve,
    "propose-reject": _reject,
}
