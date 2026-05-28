"""workflow-* CLI (v0.18-F/G Workflow Kernel + Signal/Query)."""

from __future__ import annotations

import argparse
import json as _json

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    list_p = subparsers.add_parser(
        "workflow-list",
        help="List WorkflowRuns by state (Temporal-shape state machine).",
    )
    list_p.add_argument("--state", choices=["pending", "running", "suspended",
                                              "done", "failed", "cancelled"],
                          default=None)
    list_p.add_argument("--limit", type=int, default=50)

    show = subparsers.add_parser(
        "workflow-show",
        help="Show a WorkflowRun + all its StepRuns + signal/query history.",
    )
    show.add_argument("--run", required=True, help="workflow_run_id")

    signal = subparsers.add_parser(
        "workflow-signal",
        help="Send an async signal into a running (or suspended) workflow.",
    )
    signal.add_argument("--run", required=True)
    signal.add_argument("--name", required=True)
    signal.add_argument("--payload-json", default="{}",
                          help="JSON payload (default: {})")

    query = subparsers.add_parser(
        "workflow-query",
        help="Sync read of workflow state (Temporal Query equivalent).",
    )
    query.add_argument("--run", required=True)
    query.add_argument("--name", default="state",
                         help="Query name (default: state)")

    resume = subparsers.add_parser(
        "workflow-resume",
        help="Transition a suspended workflow back to running.",
    )
    resume.add_argument("--run", required=True)


def _list(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="workflow_list", action="list",
            payload={"state": args.state, "limit": args.limit},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _show(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="workflow_show", action="show",
            payload={"run_id": args.run},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _signal(args, *, runner, workspace) -> int:
    try:
        payload = _json.loads(args.payload_json)
    except _json.JSONDecodeError as exc:
        raise SystemExit(f"--payload-json must be valid JSON: {exc}")
    return run_and_print(
        runner,
        OperationSpec(
            name="workflow_signal", action="signal",
            payload={"run_id": args.run, "name": args.name, "payload": payload},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _query(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="workflow_query", action="query",
            payload={"run_id": args.run, "name": args.name},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _resume(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="workflow_resume", action="resume",
            payload={"run_id": args.run},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


COMMANDS = {
    "workflow-list": _list,
    "workflow-show": _show,
    "workflow-signal": _signal,
    "workflow-query": _query,
    "workflow-resume": _resume,
}
