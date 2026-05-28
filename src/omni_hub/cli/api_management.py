"""api-management-* commands."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    api_status = subparsers.add_parser("api-management-status")
    api_status.add_argument("--timeout-seconds", type=float, default=0.5)


def _status(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="api_management_status",
            action="status",
            payload={"timeout_seconds": args.timeout_seconds},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "api-management-status": _status,
}
