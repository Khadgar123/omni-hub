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
    report.add_argument("--narrate", action="store_true",
                          help="v0.26: enqueue a claude-lane report_narrate task "
                               "that lands a Proposal(kind=generation) with "
                               "trend analysis + decisions.")
    report.add_argument("--audience", default="self",
                          help="Narrative audience hint (default: self)")
    report.add_argument("--notes", default="",
                          help="Extra notes appended to the narrative task")

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

    intent = subparsers.add_parser(
        "app-intent-route",
        help="v0.40: 2-level router (intent first, domain second, "
             "foundation_tools third).  Returns AppRouteDecision with the "
             "three axes explicitly separated — used by functional skills "
             "to pick which orchestrator to dispatch into.",
    )
    intent.add_argument("--query", required=True)
    intent.add_argument("--subject", default="")
    intent.add_argument("--sender", default="cli-user")
    intent.add_argument("--channel", default="cli",
                         choices=["cli", "mcp", "email", "feishu", "discord"])

    multi = subparsers.add_parser(
        "app-route-multi",
        help="Plan a query that may span MULTIPLE knowledge domains "
             "(cross-domain).  Returns an ordered per-domain plan, each with "
             "its recommended op — gather one context-pack per domain, then "
             "synthesise once.  Heuristic; no LLM call.",
    )
    multi.add_argument("--query", required=True)
    multi.add_argument("--subject", default="")
    multi.add_argument("--sender", default="cli-user")
    multi.add_argument("--channel", default="cli",
                       choices=["cli", "mcp", "email", "feishu", "discord"])
    multi.add_argument("--min-ratio", type=float, default=0.5,
                       help="Keep domains scoring >= min_ratio × top (default 0.5)")
    multi.add_argument("--max-domains", type=int, default=4)

    orch = subparsers.add_parser(
        "app-orchestrate",
        help="Multi-domain orchestrator (WS2): route a query to N domains and "
             "fan out ONE shared retrieval per domain with explicit delegation "
             "contracts.  Gather-only — synthesis/claims still go via Proposal[T].",
    )
    orch.add_argument("--query", required=True)
    orch.add_argument("--max-domains", type=int, default=4)
    orch.add_argument("--min-ratio", type=float, default=0.5)
    orch.add_argument("--per-source-limit", type=int, default=5)
    orch.add_argument("--total-limit", type=int, default=12)
    orch.add_argument("--fusion", choices=["rrf", "concat"], default="rrf")

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
                "narrate": bool(args.narrate),
                "audience": args.audience,
                "notes": args.notes,
            },
            risk_level=(
                RiskLevel.LOCAL_WRITE
                if (args.persist or args.narrate)
                else RiskLevel.READ_ONLY
            ),
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


def _app_intent_route(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="app_intent_route",
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


def _app_route_multi(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="app_route_multi",
            action="route",
            payload={
                "query": args.query,
                "subject": args.subject,
                "sender": args.sender,
                "channel": args.channel,
                "min_ratio": args.min_ratio,
                "max_domains": args.max_domains,
            },
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _app_orchestrate(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="app_orchestrate",
            action="orchestrate",
            payload={
                "query": args.query,
                "max_domains": args.max_domains,
                "min_ratio": args.min_ratio,
                "per_source_limit": args.per_source_limit,
                "total_limit": args.total_limit,
                "fusion": args.fusion,
            },
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "app-report-build": _app_report_build,
    "app-route-task": _app_route_task,
    "app-route-multi": _app_route_multi,
    "app-orchestrate": _app_orchestrate,
    "app-intent-route": _app_intent_route,
    "skill-stubs-sync": _skill_stubs_sync,
}
