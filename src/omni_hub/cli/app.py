"""Application Plane CLI commands (v0.19).

Provides the user-facing entry points for cross-skill reports and the
conversational task router.  Both commands are deterministic and stdlib-
only — they do NOT call LLMs.  For narrative report generation, the
recommended flow is::

    omni-hub app-report-build --period daily --persist
    omni-hub task-enqueue --lane claude --task-type report_narrate \\
        --packet-json '{"goal": "narrative summary of yesterday\'s daily report"}'

That keeps the narrative step inside the Proposal[T] gate, matching the
project's hard write-boundary rule.
"""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    report = subparsers.add_parser(
        "app-report-build",
        help="Cross-skill report (daily | weekly | monthly).  Aggregates "
             "claims / lint / preference / workflow signals — no LLM call.",
    )
    report.add_argument("--period", required=True,
                          choices=["daily", "weekly", "monthly"])
    report.add_argument("--persist", action="store_true",
                          help="Write to vault/40_Reports/app/<period>-<stamp>.md")

    route = subparsers.add_parser(
        "app-route-task",
        help="Route a conversational query to a skill domain "
             "(heuristic; no LLM call).  Returns the chosen skill_id + "
             "a recommended OperationSpec the caller should run.",
    )
    route.add_argument("--query", required=True)
    route.add_argument("--subject", default="")
    route.add_argument("--sender", default="cli-user")
    route.add_argument("--channel", default="cli",
                       choices=["cli", "mcp", "email", "feishu", "discord"])

    sync = subparsers.add_parser(
        "skill-stubs-sync",
        help="Regenerate .agents/skills/<slug>-wiki/SKILL.md stubs from "
             "DOMAIN_SCHEMAS.  Hand-edited files are preserved.",
    )
    sync.add_argument("--skills-root", default=".agents/skills",
                       help="Override the skills root (default: .agents/skills)")


def _app_report_build(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="app_report_build",
            action="build",
            payload={
                "period": args.period,
                "persist": bool(args.persist),
            },
            risk_level=RiskLevel.LOCAL_WRITE if args.persist else RiskLevel.READ_ONLY,
        ),
    )


def _app_route_task(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="app_route_task",
            action="route",
            payload={
                "query": args.query,
                "subject": args.subject,
                "sender": args.sender,
                "channel": args.channel,
            },
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _skill_stubs_sync(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="skill_stubs_sync",
            action="sync",
            payload={"skills_root": args.skills_root},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


COMMANDS = {
    "app-report-build": _app_report_build,
    "app-route-task": _app_route_task,
    "skill-stubs-sync": _skill_stubs_sync,
}
