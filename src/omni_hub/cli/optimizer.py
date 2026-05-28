"""optimizer-* commands for skill version and DSPy/GEPA run metadata."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    register_skill = subparsers.add_parser(
        "optimizer-skill-register",
        help="Register a versioned skill/prompt/program artifact.",
    )
    register_skill.add_argument("--skill-id", required=True)
    register_skill.add_argument("--version", required=True)
    register_skill.add_argument("--domain", default="engineering")
    register_skill.add_argument("--prompt-path", default="")
    register_skill.add_argument("--module-path", default="")
    register_skill.add_argument("--optimizer", default="manual")
    register_skill.add_argument("--source-run-id", default="")
    register_skill.add_argument("--status", default="candidate")
    register_skill.add_argument("--notes", default="")

    list_skills = subparsers.add_parser(
        "optimizer-skill-list",
        help="List registered optimizer skill versions.",
    )
    list_skills.add_argument("--skill-id")
    list_skills.add_argument("--limit", type=int, default=50)

    run_record = subparsers.add_parser(
        "optimizer-run-record",
        help="Record one DSPy/GEPA/MIPRO optimization run and gate decision.",
    )
    run_record.add_argument("--skill-id", required=True)
    run_record.add_argument("--optimizer", required=True)
    run_record.add_argument("--from-version", required=True)
    run_record.add_argument("--to-version", required=True)
    run_record.add_argument("--train-count", type=int, default=0)
    run_record.add_argument("--dev-count", type=int, default=0)
    run_record.add_argument("--holdout-count", type=int, default=0)
    run_record.add_argument("--metric", action="append", default=[],
                            help="Holdout metric as name=value; repeatable.")
    run_record.add_argument("--threshold", action="append", default=[],
                            help="Gate threshold as name=value; repeatable.")
    run_record.add_argument("--min-holdout-count", type=int, default=0)
    run_record.add_argument("--pareto-candidates", type=int, default=0)
    run_record.add_argument("--notes", default="")

    run_list = subparsers.add_parser(
        "optimizer-run-list",
        help="List optimizer runs.",
    )
    run_list.add_argument("--skill-id")
    run_list.add_argument("--limit", type=int, default=50)


def _parse_metric_pairs(items: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"expected name=value, got {item!r}")
        name, value = item.split("=", 1)
        name = name.strip()
        if not name:
            raise SystemExit(f"metric name is empty in {item!r}")
        out[name] = float(value)
    return out


def _register_skill(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="optimizer_register_skill_version",
            action="register",
            payload={
                "skill_id": args.skill_id,
                "version": args.version,
                "domain": args.domain,
                "prompt_path": args.prompt_path,
                "module_path": args.module_path,
                "optimizer": args.optimizer,
                "source_run_id": args.source_run_id,
                "status": args.status,
                "notes": args.notes,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _list_skills(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="optimizer_list_skill_versions",
            action="list",
            payload={"skill_id": args.skill_id, "limit": args.limit},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _record_run(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="optimizer_record_run",
            action="record",
            payload={
                "skill_id": args.skill_id,
                "optimizer": args.optimizer,
                "from_version": args.from_version,
                "to_version": args.to_version,
                "train_count": args.train_count,
                "dev_count": args.dev_count,
                "holdout_count": args.holdout_count,
                "holdout_metrics": _parse_metric_pairs(args.metric),
                "metric_thresholds": _parse_metric_pairs(args.threshold),
                "min_holdout_count": args.min_holdout_count,
                "pareto_candidates": args.pareto_candidates,
                "notes": args.notes,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _list_runs(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="optimizer_list_runs",
            action="list",
            payload={"skill_id": args.skill_id, "limit": args.limit},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "optimizer-skill-register": _register_skill,
    "optimizer-skill-list": _list_skills,
    "optimizer-run-record": _record_run,
    "optimizer-run-list": _list_runs,
}
