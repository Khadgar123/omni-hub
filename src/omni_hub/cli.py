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

    vault_list = subparsers.add_parser("vault-list")
    vault_list.add_argument("--limit", type=int, default=100)
    vault_list.add_argument("--vault-dir", default="vault")

    vault_read = subparsers.add_parser("vault-read")
    vault_read.add_argument("--path", required=True)
    vault_read.add_argument("--max-body-chars", type=int, default=4000)

    propose = subparsers.add_parser("propose-note")
    propose.add_argument("--path", required=True)

    digest = subparsers.add_parser("memory-digest-proposal")
    digest.add_argument("--proposal", required=True)

    memory_search = subparsers.add_parser("memory-search")
    memory_search.add_argument("--query", required=True)
    memory_search.add_argument("--limit", type=int, default=10)

    subparsers.add_parser("memory-stats")

    skill_register = subparsers.add_parser("skill-register")
    skill_register.add_argument("--id", required=True)
    skill_register.add_argument("--name", required=True)
    skill_register.add_argument("--kind", required=True)
    skill_register.add_argument("--description", required=True)
    skill_register.add_argument("--version", default="0.1.0")
    skill_register.add_argument("--status", default="draft")
    skill_register.add_argument("--entrypoint", default="")
    skill_register.add_argument("--risk", default="L0")
    skill_register.add_argument("--permission", action="append", default=[])
    skill_register.add_argument("--connector", action="append", default=[])
    skill_register.add_argument("--tag", action="append", default=[])
    skill_register.add_argument("--source-path", default="")
    skill_register.add_argument("--no-card", action="store_true")

    skill_list = subparsers.add_parser("skill-list")
    skill_list.add_argument("--kind")
    skill_list.add_argument("--status")
    skill_list.add_argument("--tag")

    skill_get = subparsers.add_parser("skill-get")
    skill_get.add_argument("--id", required=True)

    skill_disable = subparsers.add_parser("skill-disable")
    skill_disable.add_argument("--id", required=True)

    skill_recommend = subparsers.add_parser("skill-recommend")
    skill_recommend.add_argument("--query", required=True)
    skill_recommend.add_argument("--limit", type=int, default=10)
    skill_recommend.add_argument("--max-risk")
    skill_recommend.add_argument("--include-disabled", action="store_true")

    skill_analyze = subparsers.add_parser("skill-analyze")
    skill_analyze.add_argument("--id", action="append", required=True)

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

    if args.command == "vault-list":
        spec = OperationSpec(
            name="list_vault_notes",
            action="list",
            payload={"limit": args.limit, "vault_dir": args.vault_dir},
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "vault-read":
        spec = OperationSpec(
            name="read_vault_note",
            action="read",
            payload={"path": args.path, "max_body_chars": args.max_body_chars},
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "propose-note":
        spec = OperationSpec(
            name="propose_knowledge",
            action="write_proposal",
            payload={"path": args.path},
            risk_level=RiskLevel.LOCAL_WRITE,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "memory-digest-proposal":
        spec = OperationSpec(
            name="digest_proposal",
            action="digest_proposal",
            payload={"proposal": args.proposal},
            risk_level=RiskLevel.LOCAL_WRITE,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "memory-search":
        spec = OperationSpec(
            name="search_memory",
            action="search",
            payload={"query": args.query, "limit": args.limit},
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "memory-stats":
        spec = OperationSpec(
            name="memory_stats",
            action="stats",
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "skill-register":
        spec = OperationSpec(
            name="register_skill",
            action="register",
            payload={
                "skill_id": args.id,
                "name": args.name,
                "kind": args.kind,
                "description": args.description,
                "version": args.version,
                "status": args.status,
                "entrypoint": args.entrypoint,
                "risk_level": args.risk,
                "required_permissions": args.permission,
                "connectors": args.connector,
                "tags": args.tag,
                "source_path": args.source_path,
                "write_card": not args.no_card,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "skill-list":
        spec = OperationSpec(
            name="list_skills",
            action="list",
            payload={
                "kind": args.kind,
                "status": args.status,
                "tag": args.tag,
            },
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "skill-get":
        spec = OperationSpec(
            name="get_skill",
            action="read",
            payload={"skill_id": args.id},
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "skill-disable":
        spec = OperationSpec(
            name="disable_skill",
            action="disable",
            payload={"skill_id": args.id},
            risk_level=RiskLevel.LOCAL_WRITE,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "skill-recommend":
        spec = OperationSpec(
            name="recommend_skills",
            action="recommend",
            payload={
                "query": args.query,
                "limit": args.limit,
                "max_risk": args.max_risk,
                "include_disabled": args.include_disabled,
            },
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "skill-analyze":
        spec = OperationSpec(
            name="analyze_skills",
            action="analyze",
            payload={"skill_ids": args.id},
            risk_level=RiskLevel.READ_ONLY,
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
