"""Scheduling CLI (v0.39) — calendar + personal-task + time-block planner.

Wraps v0.32 CalendarStore / PersonalTaskStore / TimeBlockPlanner through
OperationRunner.  Calendar writes + task creation go through Proposal[T]
in v0.39 (see ``calendar_add`` / ``task_add`` builtins for the gate).
"""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    cal_add = subparsers.add_parser(
        "cal-add",
        help="Add a CalendarEvent (lands as Proposal first).",
    )
    cal_add.add_argument("--user-id", required=True)
    cal_add.add_argument("--summary", required=True)
    cal_add.add_argument("--start", required=True,
                          help="ISO 8601 UTC, e.g. 2026-06-01T10:00:00+00:00")
    cal_add.add_argument("--end", required=True)
    cal_add.add_argument("--description", default="")
    cal_add.add_argument("--location", default="")
    cal_add.add_argument("--category", action="append", default=None)
    cal_add.add_argument("--auto-approve", action="store_true",
                          help="Skip the Proposal gate (default: gated)")

    cal_list = subparsers.add_parser(
        "cal-list", help="List CalendarEvents for a user.",
    )
    cal_list.add_argument("--user-id", required=True)
    cal_list.add_argument("--days-ahead", type=int, default=7)

    task_add = subparsers.add_parser(
        "personal-task-add",
        help="Add a PersonalTask (todo item).  Lands as Proposal first.",
    )
    task_add.add_argument("--user-id", required=True)
    task_add.add_argument("--title", required=True)
    task_add.add_argument("--description", default="")
    task_add.add_argument("--priority", type=int, default=3,
                           choices=[1, 2, 3, 4, 5])
    task_add.add_argument("--estimated-minutes", type=int, default=30)
    task_add.add_argument("--due-at", default="",
                           help="ISO 8601 UTC; empty = no due date")
    task_add.add_argument("--category", default="other",
                           choices=["work", "research", "personal", "health",
                                    "finance", "learning", "other"])
    task_add.add_argument("--auto-approve", action="store_true")

    task_list = subparsers.add_parser(
        "personal-task-list", help="List PersonalTasks for a user.",
    )
    task_list.add_argument("--user-id", required=True)
    task_list.add_argument("--status", default="",
                            choices=["", "open", "in_progress", "done", "cancelled"])
    task_list.add_argument("--limit", type=int, default=50)

    task_done = subparsers.add_parser(
        "personal-task-done", help="Mark a PersonalTask done.",
    )
    task_done.add_argument("--task-id", required=True)

    sched = subparsers.add_parser(
        "schedule-plan",
        help="Place open PersonalTasks into free CalendarEvents slots via "
             "the deterministic priority+duration solver.",
    )
    sched.add_argument("--user-id", required=True)
    sched.add_argument("--days-ahead", type=int, default=7)


def _cal_add(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="calendar_add", action="add",
            payload={
                "user_id": args.user_id, "summary": args.summary,
                "start": args.start, "end": args.end,
                "description": args.description, "location": args.location,
                "categories": list(args.category) if args.category else [],
                "auto_approve": bool(args.auto_approve),
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _cal_list(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="calendar_list", action="list",
            payload={"user_id": args.user_id, "days_ahead": args.days_ahead},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _task_add(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="task_add", action="add",
            payload={
                "user_id": args.user_id, "title": args.title,
                "description": args.description, "category": args.category,
                "priority": args.priority,
                "estimated_minutes": args.estimated_minutes,
                "due_at": args.due_at,
                "auto_approve": bool(args.auto_approve),
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _task_list(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="task_list_personal", action="list",
            payload={
                "user_id": args.user_id,
                "status": args.status or None,
                "limit": args.limit,
            },
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _task_done(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="task_done", action="done",
            payload={"task_id": args.task_id},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _schedule_plan(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="schedule_plan", action="plan",
            payload={"user_id": args.user_id, "days_ahead": args.days_ahead},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "cal-add": _cal_add,
    "cal-list": _cal_list,
    "personal-task-add": _task_add,
    "personal-task-list": _task_list,
    "personal-task-done": _task_done,
    "schedule-plan": _schedule_plan,
}
