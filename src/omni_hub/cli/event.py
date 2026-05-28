"""``event-log`` subcommands — dump and verify the hash-chained event log."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    dump = subparsers.add_parser(
        "event-log",
        help="Dump the immutable event log for one task (or the global stream).",
    )
    dump.add_argument(
        "--task-id", type=int, default=0,
        help="Task id to dump.  Use 0 (default) for the global stream.",
    )
    dump.add_argument(
        "--verify", action="store_true",
        help="Also verify the hash chain; exits non-zero if tampered.",
    )

    listp = subparsers.add_parser(
        "event-log-list",
        help="List task ids that have an event-log file.",
    )


def _dump(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="event_log_dump",
            action="dump",
            payload={"task_id": int(args.task_id), "verify": bool(args.verify)},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _list(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="event_log_list",
            action="list",
            payload={},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "event-log": _dump,
    "event-log-list": _list,
}
