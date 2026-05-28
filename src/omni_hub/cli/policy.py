"""check-policy command."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import print_json


def register(subparsers: argparse._SubParsersAction) -> None:
    policy = subparsers.add_parser("check-policy")
    policy.add_argument("--name", default="manual_check")
    policy.add_argument("--connector", default="local")
    policy.add_argument("--action", default="read")
    policy.add_argument("--risk", default="L0")


def _check(args, *, runner, workspace) -> int:
    spec = OperationSpec(
        name=args.name,
        action=args.action,
        connector=args.connector,
        risk_level=RiskLevel.parse(args.risk),
    )
    decision = runner.policy.evaluate(spec)
    print_json(
        {
            "allowed": decision.allowed,
            "requires_approval": decision.requires_approval,
            "requires_sandbox": decision.requires_sandbox,
            "reason": decision.reason,
        }
    )
    return 0


COMMANDS = {
    "check-policy": _check,
}
