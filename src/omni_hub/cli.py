from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .agent import estimate_input_tokens, task_preview
from .builtins import build_default_registry
from .models import OperationSpec, RiskLevel
from .runner import OperationRunner


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _agent_task_payload(args: argparse.Namespace) -> dict[str, Any]:
    preview = task_preview(args.task)
    input_tokens = args.input_tokens or estimate_input_tokens(args.task)
    return {
        "project_id": args.project,
        "task_preview": preview,
        "task_chars": len(args.task),
        "capabilities": args.capability,
        "input_tokens": input_tokens,
        "output_tokens": args.output_tokens,
        "max_cost_usd": args.max_cost,
        "require_batch": args.require_batch,
        "preferred_providers": args.prefer_provider,
        "preferred_accounts": args.prefer_account,
        "limit": args.limit,
    }


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

    provider_add = subparsers.add_parser("provider-add")
    provider_add.add_argument("--id", required=True)
    provider_add.add_argument("--provider", required=True)
    provider_add.add_argument("--name", required=True)
    provider_add.add_argument("--base-url", required=True)
    provider_add.add_argument("--secret-ref", default="")
    provider_add.add_argument("--status", default="active")
    provider_add.add_argument("--group", default="")
    provider_add.add_argument("--notes", default="")

    provider_list = subparsers.add_parser("provider-list")
    provider_list.add_argument("--provider")
    provider_list.add_argument("--status")

    provider_disable = subparsers.add_parser("provider-disable")
    provider_disable.add_argument("--id", required=True)
    provider_disable.add_argument("--auto", action="store_true")
    provider_disable.add_argument("--reason", default="")

    model_add = subparsers.add_parser("model-add")
    model_add.add_argument("--id", required=True)
    model_add.add_argument("--name", default="")
    model_add.add_argument("--status", default="active")
    model_add.add_argument("--capability", action="append", default=[])
    model_add.add_argument("--context-window", type=int, default=0)
    model_add.add_argument("--input-cost", type=float, default=0.0)
    model_add.add_argument("--output-cost", type=float, default=0.0)
    model_add.add_argument("--cache-read-cost", type=float, default=0.0)
    model_add.add_argument("--cache-write-cost", type=float, default=0.0)
    model_add.add_argument("--supports-batch", action="store_true")
    model_add.add_argument("--notes", default="")

    model_list = subparsers.add_parser("model-list")
    model_list.add_argument("--status")
    model_list.add_argument("--capability")

    route_ability = subparsers.add_parser("route-ability-set")
    route_ability.add_argument("--account", required=True)
    route_ability.add_argument("--model", required=True)
    route_ability.add_argument("--priority", type=int, default=0)
    route_ability.add_argument("--weight", type=float, default=1.0)
    route_ability.add_argument("--mapping", default="")
    route_ability.add_argument("--disable", action="store_true")
    route_ability.add_argument("--notes", default="")

    route_profile = subparsers.add_parser("route-profile-set")
    route_profile.add_argument("--project", required=True)
    route_profile.add_argument("--capability", action="append", default=[])
    route_profile.add_argument("--max-cost", type=float)
    route_profile.add_argument("--require-batch", action="store_true")
    route_profile.add_argument("--prefer-provider", action="append", default=[])
    route_profile.add_argument("--prefer-account", action="append", default=[])
    route_profile.add_argument("--notes", default="")

    subparsers.add_parser("route-profile-list")

    project_route = subparsers.add_parser("project-route-set")
    project_route.add_argument("--project", required=True)
    project_route.add_argument("--account", required=True)
    project_route.add_argument("--model", required=True)
    project_route.add_argument("--priority", type=int)
    project_route.add_argument("--weight", type=float)
    project_route.add_argument("--disable", action="store_true")
    project_route.add_argument("--notes", default="")

    project_route_list = subparsers.add_parser("project-route-list")
    project_route_list.add_argument("--project")

    provider_health = subparsers.add_parser("provider-health-set")
    provider_health.add_argument("--account", required=True)
    provider_health.add_argument("--model", default="")
    provider_health.add_argument("--status", default="unknown")
    provider_health.add_argument("--latency-ms", type=int)
    provider_health.add_argument("--failures", type=int, default=0)
    provider_health.add_argument("--error", default="")

    route_simulate = subparsers.add_parser("route-simulate")
    route_simulate.add_argument("--project", default="")
    route_simulate.add_argument("--capability", action="append", default=[])
    route_simulate.add_argument("--input-tokens", type=int, default=0)
    route_simulate.add_argument("--output-tokens", type=int, default=0)
    route_simulate.add_argument("--max-cost", type=float)
    route_simulate.add_argument("--require-batch", action="store_true")
    route_simulate.add_argument("--prefer-provider", action="append", default=[])
    route_simulate.add_argument("--prefer-account", action="append", default=[])
    route_simulate.add_argument("--limit", type=int, default=10)

    agent_plan = subparsers.add_parser("agent-plan")
    agent_plan.add_argument("--project", default="")
    agent_plan.add_argument("--task", default="")
    agent_plan.add_argument("--capability", action="append", default=[])
    agent_plan.add_argument("--input-tokens", type=int, default=0)
    agent_plan.add_argument("--output-tokens", type=int, default=0)
    agent_plan.add_argument("--max-cost", type=float)
    agent_plan.add_argument("--require-batch", action="store_true")
    agent_plan.add_argument("--prefer-provider", action="append", default=[])
    agent_plan.add_argument("--prefer-account", action="append", default=[])
    agent_plan.add_argument("--limit", type=int, default=5)

    subparsers.add_parser("provider-router-stats")

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

    if args.command == "provider-add":
        spec = OperationSpec(
            name="add_provider_account",
            action="register_provider",
            payload={
                "account_id": args.id,
                "provider": args.provider,
                "name": args.name,
                "base_url": args.base_url,
                "secret_ref": args.secret_ref,
                "status": args.status,
                "account_group": args.group,
                "notes": args.notes,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "provider-list":
        spec = OperationSpec(
            name="list_provider_accounts",
            action="list_providers",
            payload={"provider": args.provider, "status": args.status},
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "provider-disable":
        spec = OperationSpec(
            name="disable_provider_account",
            action="disable_provider",
            payload={
                "account_id": args.id,
                "auto": args.auto,
                "reason": args.reason,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "model-add":
        spec = OperationSpec(
            name="add_model",
            action="register_model",
            payload={
                "model_id": args.id,
                "display_name": args.name,
                "status": args.status,
                "capabilities": args.capability,
                "context_window": args.context_window,
                "input_usd_per_million": args.input_cost,
                "output_usd_per_million": args.output_cost,
                "cache_read_usd_per_million": args.cache_read_cost,
                "cache_write_usd_per_million": args.cache_write_cost,
                "supports_batch": args.supports_batch,
                "notes": args.notes,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "model-list":
        spec = OperationSpec(
            name="list_models",
            action="list_models",
            payload={"status": args.status, "capability": args.capability},
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "route-ability-set":
        spec = OperationSpec(
            name="set_route_ability",
            action="set_route_ability",
            payload={
                "account_id": args.account,
                "model_id": args.model,
                "priority": args.priority,
                "weight": args.weight,
                "model_mapping": args.mapping,
                "enabled": not args.disable,
                "notes": args.notes,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "route-profile-set":
        spec = OperationSpec(
            name="set_route_profile",
            action="set_route_profile",
            payload={
                "project_id": args.project,
                "default_capabilities": args.capability,
                "max_cost_usd": args.max_cost,
                "require_batch": args.require_batch,
                "preferred_providers": args.prefer_provider,
                "preferred_accounts": args.prefer_account,
                "notes": args.notes,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "route-profile-list":
        spec = OperationSpec(
            name="list_route_profiles",
            action="list_route_profiles",
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "project-route-set":
        spec = OperationSpec(
            name="set_project_route_override",
            action="set_project_route_override",
            payload={
                "project_id": args.project,
                "account_id": args.account,
                "model_id": args.model,
                "priority": args.priority,
                "weight": args.weight,
                "enabled": not args.disable,
                "notes": args.notes,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "project-route-list":
        spec = OperationSpec(
            name="list_project_route_overrides",
            action="list_project_route_overrides",
            payload={"project_id": args.project},
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "provider-health-set":
        spec = OperationSpec(
            name="set_provider_health",
            action="set_provider_health",
            payload={
                "account_id": args.account,
                "model_id": args.model,
                "status": args.status,
                "latency_ms": args.latency_ms,
                "consecutive_failures": args.failures,
                "last_error": args.error,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "route-simulate":
        spec = OperationSpec(
            name="route_simulate",
            action="route_simulate",
            payload={
                "project_id": args.project,
                "capabilities": args.capability,
                "input_tokens": args.input_tokens,
                "output_tokens": args.output_tokens,
                "max_cost_usd": args.max_cost,
                "require_batch": args.require_batch,
                "preferred_providers": args.prefer_provider,
                "preferred_accounts": args.prefer_account,
                "limit": args.limit,
            },
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "provider-router-stats":
        spec = OperationSpec(
            name="provider_router_stats",
            action="stats",
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        _print_json(result.to_dict())
        return 0 if result.error is None else 1

    if args.command == "agent-plan":
        spec = OperationSpec(
            name="plan_agent_task",
            action="plan_agent_task",
            payload=_agent_task_payload(args),
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
