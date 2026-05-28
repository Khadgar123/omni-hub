"""memory-* commands."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    tool = subparsers.add_parser(
        "memory-tool",
        help=(
            "Anthropic Memory Tool surface (memory_20250818) against "
            "vault/memory/.  Six commands: view / create / str_replace / "
            "insert / delete / rename."
        ),
    )
    tool.add_argument("--command", required=True,
                       choices=["view", "create", "str_replace",
                                "insert", "delete", "rename"])
    tool.add_argument("--path", required=True,
                       help="Path under /memories (e.g. /memories/notes/foo.md)")
    tool.add_argument("--file-text", default="",
                       help="Body content (create command)")
    tool.add_argument("--old-str", default="",
                       help="Exact string to replace (str_replace)")
    tool.add_argument("--new-str", default="",
                       help="Replacement string (str_replace)")
    tool.add_argument("--insert-line", type=int, default=0,
                       help="0-indexed insertion point (insert)")
    tool.add_argument("--insert-text", default="",
                       help="Text to insert (insert)")
    tool.add_argument("--new-path", default="",
                       help="Destination for rename")
    tool.add_argument("--view-start", type=int, default=0)
    tool.add_argument("--view-end", type=int, default=0,
                       help="View line range end; -1 means EOF")

    digest = subparsers.add_parser("memory-digest-proposal")
    digest.add_argument("--proposal", required=True)

    memory_search = subparsers.add_parser("memory-search")
    memory_search.add_argument("--query", required=True)
    memory_search.add_argument("--limit", type=int, default=10)

    subparsers.add_parser("memory-stats")

    remember = subparsers.add_parser(
        "memory-remember",
        help="Pin a fact in core memory (Letta-style).",
    )
    remember.add_argument("--key", required=True)
    remember.add_argument("--value", required=True)
    remember.add_argument("--confidence", type=float, default=1.0)

    forget = subparsers.add_parser(
        "memory-forget",
        help="Remove a fact from core memory.",
    )
    forget.add_argument("--key", required=True)

    recall = subparsers.add_parser(
        "memory-recall",
        help="Read from a memory tier (core list / recall search / archival search).",
    )
    recall.add_argument("--tier", default="recall",
                         choices=["core", "recall", "archival"])
    recall.add_argument("--query", default="",
                         help="Search query (required for recall + archival).")
    recall.add_argument("--limit", type=int, default=20)

    promote = subparsers.add_parser(
        "memory-promote-recall",
        help="Append a session summary to recall memory (preference flywheel hook).",
    )
    promote.add_argument("--content", required=True)
    promote.add_argument("--source-kind", default="preference")
    promote.add_argument("--source-id", default="")
    promote.add_argument("--score", type=float, default=0.0)


def _memory_tool(args, *, runner, workspace) -> int:
    payload = {
        "command": args.command,
        "path": args.path,
    }
    if args.command == "create":
        payload["file_text"] = args.file_text
    elif args.command == "str_replace":
        payload["old_str"] = args.old_str
        payload["new_str"] = args.new_str
    elif args.command == "insert":
        payload["insert_line"] = args.insert_line
        payload["insert_text"] = args.insert_text
    elif args.command == "rename":
        if not args.new_path:
            raise SystemExit("--new-path required for rename")
        payload["new_path"] = args.new_path
    elif args.command == "view" and (args.view_start or args.view_end):
        payload["view_range"] = [args.view_start, args.view_end]

    risk = RiskLevel.READ_ONLY if args.command == "view" else RiskLevel.LOCAL_WRITE
    return run_and_print(
        runner,
        OperationSpec(
            name="memory_tool",
            action="dispatch",
            payload=payload,
            risk_level=risk,
        ),
    )


def _digest(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="digest_proposal",
            action="digest_proposal",
            payload={"proposal": args.proposal},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _search(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="search_memory",
            action="search",
            payload={"query": args.query, "limit": args.limit},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _stats(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="memory_stats",
            action="stats",
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _remember(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="memory_remember_core",
            action="remember",
            payload={
                "key": args.key,
                "value": args.value,
                "confidence": args.confidence,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _forget(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="memory_forget_core",
            action="forget",
            payload={"key": args.key},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _recall(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="memory_recall",
            action="recall",
            payload={"tier": args.tier, "query": args.query, "limit": args.limit},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _promote_recall(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="memory_promote_recall",
            action="promote_recall",
            payload={
                "content": args.content,
                "source_kind": args.source_kind,
                "source_id": args.source_id,
                "score": args.score,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


COMMANDS = {
    "memory-tool": _memory_tool,
    "memory-digest-proposal": _digest,
    "memory-search": _search,
    "memory-stats": _stats,
    "memory-remember": _remember,
    "memory-forget": _forget,
    "memory-recall": _recall,
    "memory-promote-recall": _promote_recall,
}
