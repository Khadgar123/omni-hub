"""capture / vault / propose-note commands."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
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

    vault_list = subparsers.add_parser("vault-list")
    vault_list.add_argument("--limit", type=int, default=100)
    vault_list.add_argument("--vault-dir", default="vault")

    vault_read = subparsers.add_parser("vault-read")
    vault_read.add_argument("--path", required=True)
    vault_read.add_argument("--max-body-chars", type=int, default=4000)

    propose = subparsers.add_parser("propose-note")
    propose.add_argument("--path", required=True)


def _summarize_text(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="summarize_text",
            action="summarize",
            payload={"text": args.text, "max_chars": args.max_chars},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _write_markdown(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="write_markdown",
            action="write",
            payload={"path": args.path, "title": args.title, "body": args.body},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
        approved=args.approve,
    )


def _capture_url(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
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
        ),
    )


def _vault_list(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="list_vault_notes",
            action="list",
            payload={"limit": args.limit, "vault_dir": args.vault_dir},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _vault_read(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="read_vault_note",
            action="read",
            payload={"path": args.path, "max_body_chars": args.max_body_chars},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _propose_note(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="propose_knowledge",
            action="write_proposal",
            payload={"path": args.path},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


COMMANDS = {
    "summarize-text": _summarize_text,
    "write-markdown": _write_markdown,
    "capture-url": _capture_url,
    "vault-list": _vault_list,
    "vault-read": _vault_read,
    "propose-note": _propose_note,
}
