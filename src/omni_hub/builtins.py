from __future__ import annotations

from pathlib import Path

from .connectors.web import build_resource_from_body, fetch_url
from .content_store import ContentStore
from .memory import MemoryStore
from .models import OperationSpec, RiskLevel
from .proposals import ProposalStore, build_knowledge_proposal
from .provider_router import (
    HealthStatus,
    ModelSpec,
    ModelStatus,
    ProviderAccount,
    ProviderAccountStatus,
    ProviderHealth,
    ProviderRouterStore,
    RouteAbility,
    RouteRequest,
)
from .registry import OperationRegistry
from .skill_intel import analyze_skill_set, recommend_skills
from .skills import SkillKind, SkillRegistry, SkillSpec, SkillStatus
from .vault import VaultReader


def summarize_text(spec: OperationSpec) -> dict[str, str | int]:
    text = str(spec.payload.get("text", "")).strip()
    max_chars = int(spec.payload.get("max_chars", 800))
    summary = text[:max_chars].strip()
    if len(text) > max_chars:
        summary += "..."
    return {
        "summary": summary,
        "input_chars": len(text),
        "summary_chars": len(summary),
    }


def make_write_markdown(workspace: Path):
    workspace_root = workspace.resolve()

    def write_markdown(spec: OperationSpec) -> dict[str, str | int]:
        relative_path = str(spec.payload["path"])
        title = str(spec.payload.get("title", "")).strip()
        body = str(spec.payload.get("body", "")).strip()

        target = (workspace_root / relative_path).resolve()
        try:
            target.relative_to(workspace_root)
        except ValueError as exc:
            raise PermissionError("target path is outside the workspace")

        target.parent.mkdir(parents=True, exist_ok=True)
        content = f"# {title}\n\n{body}\n" if title else f"{body}\n"
        target.write_text(content, encoding="utf-8")

        return {
            "path": str(target.relative_to(workspace_root)),
            "bytes": target.stat().st_size,
        }

    return write_markdown


def make_capture_url(workspace: Path):
    workspace_root = workspace.resolve()

    def capture_url(spec: OperationSpec) -> dict[str, str]:
        url = str(spec.payload["url"]).strip()
        if not url:
            raise ValueError("url is required")

        fetch_enabled = bool(spec.payload.get("fetch", True))
        timeout_seconds = int(spec.payload.get("timeout_seconds", 20))
        max_bytes = int(spec.payload.get("max_bytes", 2_000_000))
        note = str(spec.payload.get("note", ""))

        if "html" in spec.payload:
            resource = build_resource_from_body(
                url,
                str(spec.payload["html"]),
                content_type="text/html",
            )
        elif "text" in spec.payload:
            resource = build_resource_from_body(
                url,
                str(spec.payload["text"]),
                content_type="text/plain",
            )
        elif fetch_enabled:
            resource = fetch_url(
                url,
                timeout_seconds=timeout_seconds,
                max_bytes=max_bytes,
            )
        else:
            resource = build_resource_from_body(
                url,
                "",
                content_type="text/plain",
            )

        stored = ContentStore(workspace_root).store(resource, note=note)
        return stored.to_dict()

    return capture_url


def make_list_vault_notes(workspace: Path):
    workspace_root = workspace.resolve()

    def list_vault_notes(spec: OperationSpec) -> dict[str, object]:
        limit = int(spec.payload.get("limit", 100))
        vault_dir = str(spec.payload.get("vault_dir", "vault"))
        notes = VaultReader(workspace_root, vault_dir=vault_dir).list_notes(limit=limit)
        return {
            "count": len(notes),
            "notes": [note.to_dict() for note in notes],
        }

    return list_vault_notes


def make_read_vault_note(workspace: Path):
    workspace_root = workspace.resolve()

    def read_vault_note(spec: OperationSpec) -> dict[str, object]:
        note_path = str(spec.payload["path"])
        document = VaultReader(workspace_root).read_note(note_path)
        data = document.to_dict()
        max_body_chars = int(spec.payload.get("max_body_chars", 4000))
        data["body"] = document.body[:max_body_chars]
        data["body_chars"] = len(document.body)
        return data

    return read_vault_note


def make_propose_knowledge(workspace: Path):
    workspace_root = workspace.resolve()

    def propose_knowledge(spec: OperationSpec) -> dict[str, object]:
        note_path = str(spec.payload["path"])
        document = VaultReader(workspace_root).read_note(note_path)
        proposal = build_knowledge_proposal(document)
        stored_paths = ProposalStore(workspace_root).store(proposal)
        output = proposal.to_dict()
        output.update(stored_paths)
        return output

    return propose_knowledge


def make_digest_proposal(workspace: Path):
    workspace_root = workspace.resolve()

    def digest_proposal(spec: OperationSpec) -> dict[str, object]:
        proposal_ref = str(spec.payload["proposal"])
        proposal = ProposalStore(workspace_root).load(proposal_ref)
        result = MemoryStore(workspace_root).digest_proposal(proposal)
        return result.to_dict()

    return digest_proposal


def make_search_memory(workspace: Path):
    workspace_root = workspace.resolve()

    def search_memory(spec: OperationSpec) -> dict[str, object]:
        query = str(spec.payload["query"])
        limit = int(spec.payload.get("limit", 10))
        results = MemoryStore(workspace_root, create=False).search(query, limit=limit)
        return {
            "query": query,
            "count": len(results),
            "results": [result.to_dict() for result in results],
        }

    return search_memory


def make_memory_stats(workspace: Path):
    workspace_root = workspace.resolve()

    def memory_stats(spec: OperationSpec) -> dict[str, int]:
        return MemoryStore(workspace_root, create=False).stats()

    return memory_stats


def make_register_skill(workspace: Path):
    workspace_root = workspace.resolve()

    def register_skill(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        skill = SkillSpec(
            skill_id=str(payload["skill_id"]),
            name=str(payload["name"]),
            kind=SkillKind(str(payload["kind"])),
            description=str(payload["description"]),
            version=str(payload.get("version", "0.1.0")),
            status=SkillStatus(str(payload.get("status", SkillStatus.DRAFT.value))),
            entrypoint=str(payload.get("entrypoint", "")),
            risk_level=RiskLevel.parse(payload.get("risk_level", "L0")),
            required_permissions=list(payload.get("required_permissions", [])),
            connectors=list(payload.get("connectors", [])),
            tags=list(payload.get("tags", [])),
            inputs=dict(payload.get("inputs", {})),
            outputs=dict(payload.get("outputs", {})),
            source_path=str(payload.get("source_path", "")),
        )
        write_card = bool(payload.get("write_card", True))
        return SkillRegistry(workspace_root).upsert(skill, write_card=write_card)

    return register_skill


def make_list_skills(workspace: Path):
    workspace_root = workspace.resolve()

    def list_skills(spec: OperationSpec) -> dict[str, object]:
        skills = SkillRegistry(workspace_root).list(
            kind=spec.payload.get("kind"),
            status=spec.payload.get("status"),
            tag=spec.payload.get("tag"),
        )
        return {
            "count": len(skills),
            "skills": [skill.to_dict() for skill in skills],
        }

    return list_skills


def make_get_skill(workspace: Path):
    workspace_root = workspace.resolve()

    def get_skill(spec: OperationSpec) -> dict[str, object]:
        skill = SkillRegistry(workspace_root).get(str(spec.payload["skill_id"]))
        return {"skill": skill.to_dict()}

    return get_skill


def make_disable_skill(workspace: Path):
    workspace_root = workspace.resolve()

    def disable_skill(spec: OperationSpec) -> dict[str, object]:
        skill = SkillRegistry(workspace_root).disable(str(spec.payload["skill_id"]))
        return {"skill": skill.to_dict()}

    return disable_skill


def make_recommend_skills(workspace: Path):
    workspace_root = workspace.resolve()

    def recommend(spec: OperationSpec) -> dict[str, object]:
        query = str(spec.payload.get("query", ""))
        limit = int(spec.payload.get("limit", 10))
        include_disabled = bool(spec.payload.get("include_disabled", False))
        max_risk_value = spec.payload.get("max_risk")
        max_risk = RiskLevel.parse(max_risk_value) if max_risk_value else None
        recommendations = recommend_skills(
            SkillRegistry(workspace_root).list(),
            query,
            limit=limit,
            max_risk=max_risk,
            include_disabled=include_disabled,
        )
        return {
            "query": query,
            "count": len(recommendations),
            "recommendations": [item.to_dict() for item in recommendations],
        }

    return recommend


def make_analyze_skills(workspace: Path):
    workspace_root = workspace.resolve()

    def analyze(spec: OperationSpec) -> dict[str, object]:
        registry = SkillRegistry(workspace_root)
        skill_ids = list(spec.payload.get("skill_ids", []))
        skills = [registry.get(str(skill_id)) for skill_id in skill_ids]
        return analyze_skill_set(skills).to_dict()

    return analyze


def make_add_provider_account(workspace: Path):
    workspace_root = workspace.resolve()

    def add_provider_account(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        account = ProviderAccount(
            account_id=str(payload["account_id"]),
            provider=str(payload["provider"]),
            name=str(payload["name"]),
            base_url=str(payload["base_url"]),
            secret_ref=str(payload.get("secret_ref", "")),
            status=ProviderAccountStatus(
                str(payload.get("status", ProviderAccountStatus.ACTIVE.value))
            ),
            account_group=str(payload.get("account_group", "")),
            notes=str(payload.get("notes", "")),
        )
        stored = ProviderRouterStore(workspace_root).upsert_account(account)
        return {"account": stored.to_dict()}

    return add_provider_account


def make_list_provider_accounts(workspace: Path):
    workspace_root = workspace.resolve()

    def list_provider_accounts(spec: OperationSpec) -> dict[str, object]:
        accounts = ProviderRouterStore(workspace_root, create=False).list_accounts(
            status=spec.payload.get("status"),
            provider=spec.payload.get("provider"),
        )
        return {
            "count": len(accounts),
            "accounts": [account.to_dict() for account in accounts],
        }

    return list_provider_accounts


def make_disable_provider_account(workspace: Path):
    workspace_root = workspace.resolve()

    def disable_provider_account(spec: OperationSpec) -> dict[str, object]:
        store = ProviderRouterStore(workspace_root)
        account = store.disable_account(
            str(spec.payload["account_id"]),
            auto=bool(spec.payload.get("auto", False)),
            reason=str(spec.payload.get("reason", "")),
        )
        return {"account": account.to_dict()}

    return disable_provider_account


def make_add_model(workspace: Path):
    workspace_root = workspace.resolve()

    def add_model(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        model = ModelSpec(
            model_id=str(payload["model_id"]),
            display_name=str(payload.get("display_name", "")),
            status=ModelStatus(str(payload.get("status", ModelStatus.ACTIVE.value))),
            capabilities=list(payload.get("capabilities", [])),
            context_window=int(payload.get("context_window", 0)),
            input_usd_per_million=float(payload.get("input_usd_per_million", 0.0)),
            output_usd_per_million=float(payload.get("output_usd_per_million", 0.0)),
            cache_read_usd_per_million=float(
                payload.get("cache_read_usd_per_million", 0.0)
            ),
            cache_write_usd_per_million=float(
                payload.get("cache_write_usd_per_million", 0.0)
            ),
            supports_batch=bool(payload.get("supports_batch", False)),
            notes=str(payload.get("notes", "")),
        )
        stored = ProviderRouterStore(workspace_root).upsert_model(model)
        return {"model": stored.to_dict()}

    return add_model


def make_list_models(workspace: Path):
    workspace_root = workspace.resolve()

    def list_models(spec: OperationSpec) -> dict[str, object]:
        models = ProviderRouterStore(workspace_root, create=False).list_models(
            status=spec.payload.get("status"),
            capability=spec.payload.get("capability"),
        )
        return {
            "count": len(models),
            "models": [model.to_dict() for model in models],
        }

    return list_models


def make_set_route_ability(workspace: Path):
    workspace_root = workspace.resolve()

    def set_route_ability(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        ability = RouteAbility(
            account_id=str(payload["account_id"]),
            model_id=str(payload["model_id"]),
            enabled=bool(payload.get("enabled", True)),
            priority=int(payload.get("priority", 0)),
            weight=float(payload.get("weight", 1.0)),
            model_mapping=str(payload.get("model_mapping", "")),
            notes=str(payload.get("notes", "")),
        )
        stored = ProviderRouterStore(workspace_root).upsert_ability(ability)
        return {"ability": stored.to_dict()}

    return set_route_ability


def make_set_provider_health(workspace: Path):
    workspace_root = workspace.resolve()

    def set_provider_health(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        health = ProviderHealth(
            account_id=str(payload["account_id"]),
            model_id=str(payload.get("model_id", "")),
            status=HealthStatus(str(payload.get("status", HealthStatus.UNKNOWN.value))),
            latency_ms=payload.get("latency_ms"),
            consecutive_failures=int(payload.get("consecutive_failures", 0)),
            last_error=str(payload.get("last_error", "")),
        )
        stored = ProviderRouterStore(workspace_root).set_health(health)
        return {"health": stored.to_dict()}

    return set_provider_health


def make_route_simulate(workspace: Path):
    workspace_root = workspace.resolve()

    def route_simulate(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        request = RouteRequest(
            capabilities=list(payload.get("capabilities", [])),
            input_tokens=int(payload.get("input_tokens", 0)),
            output_tokens=int(payload.get("output_tokens", 0)),
            max_cost_usd=payload.get("max_cost_usd"),
            require_batch=bool(payload.get("require_batch", False)),
            preferred_providers=list(payload.get("preferred_providers", [])),
            preferred_accounts=list(payload.get("preferred_accounts", [])),
            limit=int(payload.get("limit", 10)),
        )
        return ProviderRouterStore(workspace_root, create=False).route(request).to_dict()

    return route_simulate


def make_provider_router_stats(workspace: Path):
    workspace_root = workspace.resolve()

    def provider_router_stats(spec: OperationSpec) -> dict[str, int]:
        return ProviderRouterStore(workspace_root, create=False).stats()

    return provider_router_stats


def build_default_registry(workspace: Path | str = ".") -> OperationRegistry:
    workspace_path = Path(workspace)
    registry = OperationRegistry()
    registry.register("summarize_text", summarize_text)
    registry.register("write_markdown", make_write_markdown(workspace_path))
    registry.register("capture_url", make_capture_url(workspace_path))
    registry.register("list_vault_notes", make_list_vault_notes(workspace_path))
    registry.register("read_vault_note", make_read_vault_note(workspace_path))
    registry.register("propose_knowledge", make_propose_knowledge(workspace_path))
    registry.register("digest_proposal", make_digest_proposal(workspace_path))
    registry.register("search_memory", make_search_memory(workspace_path))
    registry.register("memory_stats", make_memory_stats(workspace_path))
    registry.register("register_skill", make_register_skill(workspace_path))
    registry.register("list_skills", make_list_skills(workspace_path))
    registry.register("get_skill", make_get_skill(workspace_path))
    registry.register("disable_skill", make_disable_skill(workspace_path))
    registry.register("recommend_skills", make_recommend_skills(workspace_path))
    registry.register("analyze_skills", make_analyze_skills(workspace_path))
    registry.register("add_provider_account", make_add_provider_account(workspace_path))
    registry.register("list_provider_accounts", make_list_provider_accounts(workspace_path))
    registry.register(
        "disable_provider_account",
        make_disable_provider_account(workspace_path),
    )
    registry.register("add_model", make_add_model(workspace_path))
    registry.register("list_models", make_list_models(workspace_path))
    registry.register("set_route_ability", make_set_route_ability(workspace_path))
    registry.register("set_provider_health", make_set_provider_health(workspace_path))
    registry.register("route_simulate", make_route_simulate(workspace_path))
    registry.register("provider_router_stats", make_provider_router_stats(workspace_path))
    return registry
