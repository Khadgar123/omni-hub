"""harness-report-{daily,weekly,monthly} commands."""

from __future__ import annotations

import argparse
from datetime import date as _date
from pathlib import Path

from ._common import print_json


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
        p.add_argument(
            "--print", action="store_true",
            help="also print the report body to stdout",
        )


def _build_report(args, *, runner, workspace) -> int:
    from .. import reports as reports_mod

    period = args.command.split("-")[-1]
    anchor = _date.fromisoformat(args.date) if args.date else None
    builder = {
        "daily": reports_mod.build_daily,
        "weekly": reports_mod.build_weekly,
        "monthly": reports_mod.build_monthly,
    }[period]
    body, ctx = builder(anchor=anchor, workspace=workspace)
    out_path = (
        Path(args.write_to)
        if args.write_to
        else reports_mod.default_output_path(workspace, ctx)
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    if args.print:
        print(body)
    print_json(
        {
            "period": period,
            "anchor": ctx.anchor_date.isoformat(),
            "output": str(out_path),
            "bytes": len(body.encode("utf-8")),
        }
    )
    return 0


COMMANDS = {
    "harness-report-daily": _build_report,
    "harness-report-weekly": _build_report,
    "harness-report-monthly": _build_report,
}
