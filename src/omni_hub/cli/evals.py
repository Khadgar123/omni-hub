"""Eval flywheel CLI (v0.41).

Four subcommands that surface the ``omni_hub.evals`` plane:

* ``eval-list``     — list all EvalPacks in vault/evals/
* ``eval-show``     — show a single pack's cases + manifest
* ``eval-run``      — run a pack and persist EvalRun
* ``eval-promote``  — scan PreferenceStore for a domain and propose v0.X+1
                      via Proposal(kind=eval_pack_upgrade) (human-gated)
"""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser(
        "eval-list",
        help="List every EvalPack under vault/evals/ with pack_id + counts.",
    )

    show = subparsers.add_parser(
        "eval-show",
        help="Show one EvalPack's manifest + case list.",
    )
    show.add_argument("--domain", required=True)
    show.add_argument("--version", default="v0.1")
    show.add_argument("--include-holdout", action="store_true",
                       help="Also list private holdout cases (read-only).")

    run_p = subparsers.add_parser(
        "eval-run",
        help="Run an EvalPack against an echo candidate (no LLM) and "
             "persist the EvalRun to .omni/eval_runs.sqlite3.  Pass "
             "--judge llm to route through ccLoad / Anthropic SDK.",
    )
    run_p.add_argument("--domain", required=True)
    run_p.add_argument("--version", default="v0.1")
    run_p.add_argument("--judge", default="heuristic",
                       choices=["heuristic", "llm"])
    run_p.add_argument("--skill-version", default="",
                       help="Tag the run with the skill version under test.")
    run_p.add_argument("--include-holdout", action="store_true")

    promote = subparsers.add_parser(
        "eval-promote",
        help="Scan PreferenceStore for a domain; if accepted count crosses "
             "graduation threshold, emit Proposal(kind=eval_pack_upgrade) "
             "with candidate v0.X+1 cases.  Human approves via propose-approve.",
    )
    promote.add_argument("--domain", required=True)
    promote.add_argument("--new-version", default="v0.2")


def _eval_list(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="eval_list", action="list", payload={},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _eval_show(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="eval_show", action="show",
            payload={
                "domain": args.domain,
                "version": args.version,
                "include_holdout": bool(args.include_holdout),
            },
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _eval_run(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="eval_run", action="run",
            payload={
                "domain": args.domain,
                "version": args.version,
                "judge": args.judge,
                "skill_version": args.skill_version,
                "include_holdout": bool(args.include_holdout),
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _eval_promote(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="eval_promote", action="promote",
            payload={
                "domain": args.domain,
                "new_version": args.new_version,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


COMMANDS = {
    "eval-list": _eval_list,
    "eval-show": _eval_show,
    "eval-run": _eval_run,
    "eval-promote": _eval_promote,
}
