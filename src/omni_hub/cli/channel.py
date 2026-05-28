"""Interface Plane channel commands (v0.19)."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser(
        "channel-list",
        help="List every registered Channel plus its health snapshot.",
    )

    health = subparsers.add_parser(
        "channel-health",
        help="Probe a single channel's health (cli|mcp|email|feishu|discord).",
    )
    health.add_argument("--name", required=True,
                          choices=["cli", "mcp", "email", "feishu", "discord"])


def _channel_list(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="channel_list",
            action="list",
            payload={},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _channel_health(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="channel_health",
            action="health",
            payload={"name": args.name},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "channel-list": _channel_list,
    "channel-health": _channel_health,
}
