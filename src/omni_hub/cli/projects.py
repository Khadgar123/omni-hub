"""Projects CLI (v0.39) — high-level user-goal lifecycle."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    create = subparsers.add_parser(
        "project-create",
        help="Create a Project (high-level user goal that decomposes into "
             "worker subtasks).  Lands as Proposal first.",
    )
    create.add_argument("--user-id", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--description", default="")
    create.add_argument("--auto-approve", action="store_true")

    list_p = subparsers.add_parser(
        "project-list", help="List Projects, optionally filtered.",
    )
    list_p.add_argument("--user-id", default="")
    list_p.add_argument("--status", default="",
                         choices=["", "pending", "planning", "in_progress",
                                  "done", "cancelled"])
    list_p.add_argument("--limit", type=int, default=50)

    show = subparsers.add_parser(
        "project-show", help="Show a Project with its subtasks.",
    )
    show.add_argument("--project-id", required=True)


def _project_create(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="project_plan", action="plan",
            payload={
                "user_id": args.user_id, "title": args.title,
                "description": args.description,
                "auto_approve": bool(args.auto_approve),
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _project_list(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="project_list", action="list",
            payload={
                "user_id": args.user_id or None,
                "status": args.status or None,
                "limit": args.limit,
            },
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _project_show(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="project_show", action="show",
            payload={"project_id": args.project_id},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "project-create": _project_create,
    "project-list": _project_list,
    "project-show": _project_show,
}
