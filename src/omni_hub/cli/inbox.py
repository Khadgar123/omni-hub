"""Inbox CLI (v0.39) — forwarded-content classifier."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    classify = subparsers.add_parser(
        "inbox-classify",
        help="Classify a forwarded InboundMessage (URL / PDF / .ics / "
             "task / wiki) and recommend a downstream OperationSpec.",
    )
    classify.add_argument("--body", required=True,
                           help="Message body (or `file://path` for long content)")
    classify.add_argument("--subject", default="")
    classify.add_argument("--sender", default="cli-user")
    classify.add_argument("--channel", default="cli",
                           choices=["cli", "mcp", "email", "feishu", "discord"])
    classify.add_argument("--user-id", default="")
    classify.add_argument("--default-domain", default="")


def _inbox_classify(args, *, runner, workspace) -> int:
    body = args.body
    if body.startswith("file://"):
        from pathlib import Path
        path = Path(body[len("file://"):])
        if not path.is_absolute():
            path = workspace / path
        body = path.read_text(encoding="utf-8")
    return run_and_print(
        runner,
        OperationSpec(
            name="inbox_classify", action="classify",
            payload={
                "body": body, "subject": args.subject,
                "sender": args.sender, "channel": args.channel,
                "user_id": args.user_id,
                "default_domain": args.default_domain,
            },
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "inbox-classify": _inbox_classify,
}
