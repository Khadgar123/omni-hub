"""harness-report-{daily,weekly,monthly} commands.

These go through the same ``build_{daily,weekly,monthly}_report`` operations
that ``schedule-tick`` enqueues for the worker pool, so policy + audit
cover both manual and scheduled invocations identically.
"""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    for period in ("daily", "weekly", "monthly"):
        p = subparsers.add_parser(
            f"harness-report-{period}",
            help=f"Generate the {period} markdown report from memory + preferences.",
        )
        p.add_argument("--date", help="anchor date YYYY-MM-DD; defaults to today")
        p.add_argument(
            "--write-to",
            help="output path; defaults to vault/40_Reports/<period>/...",
        )


def _build_report(args, *, runner, workspace) -> int:
    period = args.command.split("-")[-1]
    payload: dict[str, object] = {}
    if args.date:
        payload["anchor"] = args.date
    if args.write_to:
        payload["write_to"] = args.write_to
    return run_and_print(
        runner,
        OperationSpec(
            name=f"build_{period}_report",
            action="build",
            payload=payload,
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


COMMANDS = {
    "harness-report-daily": _build_report,
    "harness-report-weekly": _build_report,
    "harness-report-monthly": _build_report,
}
