from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .provider_router import (
    ProviderRouterStore,
    RouteDecision,
    RouteRequest,
    normalize_capabilities,
)


@dataclass(slots=True)
class AgentTaskRequest:
    project_id: str = ""
    task_preview: str = ""
    task_chars: int = 0
    capabilities: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    max_cost_usd: float | None = None
    require_batch: bool = False
    preferred_providers: list[str] = field(default_factory=list)
    preferred_accounts: list[str] = field(default_factory=list)
    limit: int = 5

    def __post_init__(self) -> None:
        self.project_id = self.project_id.strip()
        self.task_preview = self.task_preview.strip()
        self.task_chars = max(int(self.task_chars), len(self.task_preview))
        self.capabilities = normalize_capabilities(self.capabilities)
        self.input_tokens = max(int(self.input_tokens), 0)
        self.output_tokens = max(int(self.output_tokens), 0)
        if self.max_cost_usd is not None:
            self.max_cost_usd = max(float(self.max_cost_usd), 0.0)
        self.require_batch = bool(self.require_batch)
        self.preferred_providers = normalize_capabilities(self.preferred_providers)
        self.preferred_accounts = [
            item.strip() for item in self.preferred_accounts if item.strip()
        ]
        self.limit = max(int(self.limit), 1)

    def to_route_request(self) -> RouteRequest:
        return RouteRequest(
            project_id=self.project_id,
            capabilities=self.capabilities,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            max_cost_usd=self.max_cost_usd,
            require_batch=self.require_batch,
            preferred_providers=self.preferred_providers,
            preferred_accounts=self.preferred_accounts,
            limit=self.limit,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AgentPlan:
    status: str
    request: AgentTaskRequest
    route_decision: RouteDecision
    invocation: dict[str, Any] | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "request": self.request.to_dict(),
            "invocation": self.invocation,
            "route_decision": self.route_decision.to_dict(),
            "error": self.error,
        }


class AgentPlanner:
    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace).resolve()

    def plan(self, request: AgentTaskRequest) -> AgentPlan:
        decision = ProviderRouterStore(self.workspace, create=False).route(
            request.to_route_request()
        )
        selected = decision.selected
        if selected is None:
            return AgentPlan(
                status="blocked",
                request=request,
                route_decision=decision,
                invocation=None,
                error="no route candidate matched the agent request",
            )

        provider_model_id = selected.ability.model_mapping or selected.model.model_id
        invocation = {
            "provider": selected.account.provider,
            "account_id": selected.account.account_id,
            "account_name": selected.account.name,
            "base_url": selected.account.base_url,
            "secret_ref": selected.account.secret_ref,
            "model_id": selected.model.model_id,
            "provider_model_id": provider_model_id,
            "project_id": decision.request.project_id,
            "capabilities": decision.request.capabilities,
            "estimated_cost_usd": round(selected.estimated_cost_usd, 8),
            "route_score": round(selected.score, 4),
            "reasons": list(selected.reasons),
            "warnings": list(selected.warnings),
        }
        return AgentPlan(
            status="planned",
            request=request,
            route_decision=decision,
            invocation=invocation,
        )


def estimate_input_tokens(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    return max(1, (len(stripped) + 3) // 4)


def task_preview(text: str, *, max_chars: int = 240) -> str:
    stripped = " ".join(text.strip().split())
    if len(stripped) <= max_chars:
        return stripped
    return stripped[: max_chars - 3].rstrip() + "..."
