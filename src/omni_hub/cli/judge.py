"""Judge LLM CLI (v0.23).

Two CLI subcommands:

* ``judge-evaluate`` — score a candidate answer against a domain rubric.
  Default judge is ``heuristic`` (stdlib only).  ``--judge llm`` routes
  through ccLoad or the Anthropic SDK; falls back to heuristic when
  neither is configured.
* ``judge-list`` — list known Judges + their availability state.
"""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import print_json, run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    evaluate = subparsers.add_parser(
        "judge-evaluate",
        help="Score a candidate answer against a domain rubric.",
    )
    evaluate.add_argument("--domain", required=True,
                          help="Domain slug (e.g. research, finance, cn_policy)")
    evaluate.add_argument("--candidate", required=True,
                          help="Candidate answer text (or `file://path/to/file.md`)")
    evaluate.add_argument("--reference", default="",
                          help="Optional reference / ground-truth context "
                               "(plain text or `file://path/to/file.md`)")
    evaluate.add_argument("--judge", default="heuristic",
                          choices=["heuristic", "llm"])
    evaluate.add_argument("--rubric-evidence-coverage", type=float, default=None)
    evaluate.add_argument("--rubric-information-density", type=float, default=None)
    evaluate.add_argument("--rubric-citation-support", type=float, default=None)
    evaluate.add_argument("--rubric-style-fit", type=float, default=None)
    evaluate.add_argument("--rubric-uncertainty-calibration", type=float, default=None)

    subparsers.add_parser(
        "judge-list",
        help="List registered Judges and report each one's availability.",
    )


def _resolve_text(value: str, workspace) -> str:
    if not value.startswith("file://"):
        return value
    from pathlib import Path

    path = Path(value[len("file://"):])
    if not path.is_absolute():
        path = workspace / path
    return path.read_text(encoding="utf-8")


def _judge_evaluate(args, *, runner, workspace) -> int:
    rubric: dict[str, float] = {}
    for dimension in (
        "evidence_coverage", "information_density", "citation_support",
        "style_fit", "uncertainty_calibration",
    ):
        attr = f"rubric_{dimension}"
        value = getattr(args, attr, None)
        if value is not None:
            rubric[dimension] = float(value)

    candidate = _resolve_text(args.candidate, workspace)
    reference = _resolve_text(args.reference, workspace) if args.reference else ""

    return run_and_print(
        runner,
        OperationSpec(
            name="judge_evaluate",
            action="evaluate",
            payload={
                "domain": args.domain,
                "candidate": candidate,
                "reference": reference,
                "judge": args.judge,
                "rubric": rubric,
            },
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _judge_list(args, *, runner, workspace) -> int:
    # Build a Judges registry inline so listing is independent of runner state.
    from ..judge import HeuristicJudge, Judges, LLMJudge

    registry = Judges()
    registry.register(HeuristicJudge())
    llm = LLMJudge()
    registry.register(llm)

    out = [
        {"name": "heuristic", "available": True,
         "detail": "stdlib-only,deterministic"},
        {"name": "llm", "available": llm.available(),
         "detail": {
             "ccload_base": llm.ccload_base,
             "model": llm.model,
             "has_anthropic_sdk": llm._has_anthropic_sdk(),
             "has_ccload": llm._has_ccload(),
         }},
    ]
    print_json({"judges": out})
    return 0


COMMANDS = {
    "judge-evaluate": _judge_evaluate,
    "judge-list": _judge_list,
}
