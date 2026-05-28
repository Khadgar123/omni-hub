"""skill-* commands."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    skill_register = subparsers.add_parser("skill-register")
    skill_register.add_argument("--id", required=True)
    skill_register.add_argument("--name", required=True)
    skill_register.add_argument("--kind", required=True)
    skill_register.add_argument("--description", required=True)
    skill_register.add_argument("--version", default="0.1.0")
    skill_register.add_argument("--status", default="draft")
    skill_register.add_argument("--entrypoint", default="")
    skill_register.add_argument("--risk", default="L0")
    skill_register.add_argument("--permission", action="append", default=[])
    skill_register.add_argument("--connector", action="append", default=[])
    skill_register.add_argument("--tag", action="append", default=[])
    skill_register.add_argument("--source-path", default="")
    skill_register.add_argument("--no-card", action="store_true")

    skill_list = subparsers.add_parser("skill-list")
    skill_list.add_argument("--kind")
    skill_list.add_argument("--status")
    skill_list.add_argument("--tag")

    skill_get = subparsers.add_parser("skill-get")
    skill_get.add_argument("--id", required=True)

    skill_disable = subparsers.add_parser("skill-disable")
    skill_disable.add_argument("--id", required=True)

    skill_recommend = subparsers.add_parser("skill-recommend")
    skill_recommend.add_argument("--query", required=True)
    skill_recommend.add_argument("--limit", type=int, default=10)
    skill_recommend.add_argument("--max-risk")
    skill_recommend.add_argument("--include-disabled", action="store_true")

    skill_analyze = subparsers.add_parser("skill-analyze")
    skill_analyze.add_argument("--id", action="append", required=True)

    skill_sync = subparsers.add_parser(
        "skill-sync",
        help="Reconcile .agents/skills/<id>/SKILL.md with registry/skills.json",
    )
    skill_sync.add_argument(
        "--apply", action="store_true",
        help="Actually write registry/skills.json (default is dry-run diff).",
    )


def _register(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="register_skill",
            action="register",
            payload={
                "skill_id": args.id,
                "name": args.name,
                "kind": args.kind,
                "description": args.description,
                "version": args.version,
                "status": args.status,
                "entrypoint": args.entrypoint,
                "risk_level": args.risk,
                "required_permissions": args.permission,
                "connectors": args.connector,
                "tags": args.tag,
                "source_path": args.source_path,
                "write_card": not args.no_card,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _list(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="list_skills",
            action="list",
            payload={"kind": args.kind, "status": args.status, "tag": args.tag},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _get(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="get_skill",
            action="read",
            payload={"skill_id": args.id},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _disable(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="disable_skill",
            action="disable",
            payload={"skill_id": args.id},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _recommend(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="recommend_skills",
            action="recommend",
            payload={
                "query": args.query,
                "limit": args.limit,
                "max_risk": args.max_risk,
                "include_disabled": args.include_disabled,
            },
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _analyze(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="analyze_skills",
            action="analyze",
            payload={"skill_ids": args.id},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _sync(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="skill_sync",
            action="sync",
            payload={"apply": bool(args.apply)},
            risk_level=RiskLevel.LOCAL_WRITE if args.apply else RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "skill-register": _register,
    "skill-list": _list,
    "skill-get": _get,
    "skill-disable": _disable,
    "skill-recommend": _recommend,
    "skill-analyze": _analyze,
    "skill-sync": _sync,
}
