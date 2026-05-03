from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .builtins import build_default_registry
from .models import OperationSpec, RiskLevel
from .runner import OperationRunner


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omni-hub",
        description="Run Omni Hub operations from the local workspace.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    summarize = subparsers.add_parser("summarize-text")
    summarize.add_argument("--text", required=True)
    summarize.add_argument("--max-chars", type=int, default=800)

    write = subparsers.add_parser("write-markdown")
    write.add_argument("--path", required=True)
    write.add_argument("--title", default="")
    write.add_argument("--body", required=True)
    write.add_argument("--approve", action="store_true")

    capture = subparsers.add_parser("capture-url")
    capture.add_argument("--url", required=True)
    capture.add_argument("--note", default="")
    capture.add_argument("--no-fetch", action="store_true")
    capture.add_argument("--max-bytes", type=int, default=2_000_000)
    capture.add_argument("--timeout-seconds", type=int, default=20)

    policy = subparsers.add_parser("check-policy")
    policy.add_argument("--name", default="manual_check")
    policy.add_argument("--connector", default="local")
    policy.add_argument("--action", default="read")
    policy.add_argument("--risk", default="L0")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = Path.cwd()
    runner = OperationRunner(build_default_registry(workspace))

    if args.command == "summarize-text":
        spec = OperationSpec(
            name="summarize_text",
            action="summarize",
            payload={"text": args.text, "max_chars": args.max_chars},
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "write-markdown":
        spec = OperationSpec(
            name="write_markdown",
            action="write",
            payload={"path": args.path, "title": args.title, "body": args.body},
            risk_level=RiskLevel.LOCAL_WRITE,
        )
        result = runner.run(spec, approved=args.approve)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "capture-url":
        spec = OperationSpec(
            name="capture_url",
            connector="web",
            action="capture_url",
            payload={
                "url": args.url,
                "note": args.note,
                "fetch": not args.no_fetch,
                "max_bytes": args.max_bytes,
                "timeout_seconds": args.timeout_seconds,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "check-policy":
        spec = OperationSpec(
            name=args.name,
            action=args.action,
            connector=args.connector,
            risk_level=RiskLevel.parse(args.risk),
        )
        decision = runner.policy.evaluate(spec)
        _print_json(
            {
                "allowed": decision.allowed,
                "requires_approval": decision.requires_approval,
                "requires_sandbox": decision.requires_sandbox,
                "reason": decision.reason,
            }
        )
        return 0

    raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
