"""A/B test CLI (v0.29) — run / list / show / stats.

Variants come from CLI args (or ``file://path`` for long candidates).
Judge defaults to heuristic; pass ``--judge llm`` for ccLoad-backed
scoring.  All runs persist to ``.omni/ab_tests.sqlite3``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    run = subparsers.add_parser(
        "ab-test",
        help="Run one A/B test: judge two candidates side-by-side, persist verdict.",
    )
    run.add_argument("--domain", required=True)
    run.add_argument("--candidate-a", required=True,
                      help="Candidate A text (or `file://path/to/a.md`)")
    run.add_argument("--candidate-b", required=True,
                      help="Candidate B text (or `file://path/to/b.md`)")
    run.add_argument("--label-a", default="A")
    run.add_argument("--label-b", default="B")
    run.add_argument("--notes-a", default="")
    run.add_argument("--notes-b", default="")
    run.add_argument("--reference", default="",
                      help="Optional reference / ground-truth context "
                           "(text or `file://path`)")
    run.add_argument("--judge", default="heuristic", choices=["heuristic", "llm"])

    list_p = subparsers.add_parser(
        "ab-list",
        help="List recent A/B runs (optionally filtered by --domain).",
    )
    list_p.add_argument("--domain", default="")
    list_p.add_argument("--limit", type=int, default=20)

    show = subparsers.add_parser(
        "ab-show",
        help="Show full verdict for a specific A/B run.",
    )
    show.add_argument("--id", dest="run_id", required=True)

    stats = subparsers.add_parser(
        "ab-stats",
        help="Aggregate win-rate (a / b / tie) across runs.",
    )
    stats.add_argument("--domain", default="")


def _resolve_text(value: str, workspace) -> str:
    if not value.startswith("file://"):
        return value
    path = Path(value[len("file://"):])
    if not path.is_absolute():
        path = workspace / path
    return path.read_text(encoding="utf-8")


def _ab_test(args, *, runner, workspace) -> int:
    candidate_a = _resolve_text(args.candidate_a, workspace)
    candidate_b = _resolve_text(args.candidate_b, workspace)
    reference = _resolve_text(args.reference, workspace) if args.reference else ""
    return run_and_print(
        runner,
        OperationSpec(
            name="ab_test_run",
            action="run",
            payload={
                "domain": args.domain,
                "candidate_a": candidate_a,
                "candidate_b": candidate_b,
                "label_a": args.label_a,
                "label_b": args.label_b,
                "notes_a": args.notes_a,
                "notes_b": args.notes_b,
                "reference": reference,
                "judge": args.judge,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _ab_list(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="ab_test_list", action="list",
            payload={"domain": args.domain or None, "limit": args.limit},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _ab_show(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="ab_test_show", action="show",
            payload={"run_id": args.run_id},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _ab_stats(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="ab_test_stats", action="stats",
            payload={"domain": args.domain or None},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "ab-test": _ab_test,
    "ab-list": _ab_list,
    "ab-show": _ab_show,
    "ab-stats": _ab_stats,
}
