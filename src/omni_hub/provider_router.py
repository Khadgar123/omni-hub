from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ProviderAccountStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    AUTO_DISABLED = "auto_disabled"


class ModelStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"


class HealthStatus(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    LIMITED = "limited"
    DOWN = "down"


@dataclass(slots=True)
class ProviderAccount:
    account_id: str
    provider: str
    name: str
    base_url: str
    secret_ref: str = ""
    proxy_url: str = ""
    status: ProviderAccountStatus = ProviderAccountStatus.ACTIVE
    account_group: str = ""
    notes: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        validate_account_id(self.account_id)
        validate_provider_id(self.provider)
        if not self.name.strip():
            raise ValueError("provider account name is required")
        if not self.base_url.strip():
            raise ValueError("provider account base_url is required")
        validate_secret_ref(self.secret_ref)
        validate_proxy_url(self.proxy_url)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ProviderAccount":
        return cls(
            account_id=row["account_id"],
            provider=row["provider"],
            name=row["name"],
            base_url=row["base_url"],
            secret_ref=row["secret_ref"],
            proxy_url=row["proxy_url"] if "proxy_url" in row.keys() else "",
            status=ProviderAccountStatus(row["status"]),
            account_group=row["account_group"],
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(slots=True)
class ModelSpec:
    model_id: str
    display_name: str = ""
    status: ModelStatus = ModelStatus.ACTIVE
    capabilities: list[str] = field(default_factory=list)
    context_window: int = 0
    input_usd_per_million: float = 0.0
    output_usd_per_million: float = 0.0
    cache_read_usd_per_million: float = 0.0
    cache_write_usd_per_million: float = 0.0
    supports_batch: bool = False
    notes: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        validate_model_id(self.model_id)
        self.display_name = self.display_name.strip() or self.model_id
        self.capabilities = normalize_capabilities(self.capabilities)
        self.context_window = max(int(self.context_window), 0)
        self.input_usd_per_million = max(float(self.input_usd_per_million), 0.0)
        self.output_usd_per_million = max(float(self.output_usd_per_million), 0.0)
        self.cache_read_usd_per_million = max(
            float(self.cache_read_usd_per_million),
            0.0,
        )
        self.cache_write_usd_per_million = max(
            float(self.cache_write_usd_per_million),
            0.0,
        )

    def estimate_cost(self, *, input_tokens: int, output_tokens: int) -> float:
        return (
            max(input_tokens, 0) * self.input_usd_per_million
            + max(output_tokens, 0) * self.output_usd_per_million
        ) / 1_000_000

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ModelSpec":
        return cls(
            model_id=row["model_id"],
            display_name=row["display_name"],
            status=ModelStatus(row["status"]),
            capabilities=json.loads(row["capabilities"]),
            context_window=row["context_window"],
            input_usd_per_million=row["input_usd_per_million"],
            output_usd_per_million=row["output_usd_per_million"],
            cache_read_usd_per_million=row["cache_read_usd_per_million"],
            cache_write_usd_per_million=row["cache_write_usd_per_million"],
            supports_batch=bool(row["supports_batch"]),
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(slots=True)
class RouteAbility:
    account_id: str
    model_id: str
    enabled: bool = True
    priority: int = 0
    weight: float = 1.0
    model_mapping: str = ""
    notes: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        validate_account_id(self.account_id)
        validate_model_id(self.model_id)
        self.priority = int(self.priority)
        self.weight = max(float(self.weight), 0.0)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "RouteAbility":
        return cls(
            account_id=row["account_id"],
            model_id=row["model_id"],
            enabled=bool(row["enabled"]),
            priority=row["priority"],
            weight=row["weight"],
            model_mapping=row["model_mapping"],
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(slots=True)
class ProjectRouteProfile:
    project_id: str
    default_capabilities: list[str] = field(default_factory=list)
    max_cost_usd: float | None = None
    require_batch: bool = False
    preferred_providers: list[str] = field(default_factory=list)
    preferred_accounts: list[str] = field(default_factory=list)
    notes: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        validate_project_id(self.project_id)
        self.default_capabilities = normalize_capabilities(self.default_capabilities)
        self.preferred_providers = normalize_capabilities(self.preferred_providers)
        self.preferred_accounts = [
            item.strip() for item in self.preferred_accounts if item.strip()
        ]
        if self.max_cost_usd is not None:
            self.max_cost_usd = max(float(self.max_cost_usd), 0.0)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ProjectRouteProfile":
        max_cost = row["max_cost_usd"]
        return cls(
            project_id=row["project_id"],
            default_capabilities=json.loads(row["default_capabilities"]),
            max_cost_usd=float(max_cost) if max_cost is not None else None,
            require_batch=bool(row["require_batch"]),
            preferred_providers=json.loads(row["preferred_providers"]),
            preferred_accounts=json.loads(row["preferred_accounts"]),
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(slots=True)
class ProjectRouteOverride:
    project_id: str
    account_id: str
    model_id: str
    priority: int | None = None
    weight: float | None = None
    enabled: bool = True
    notes: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        validate_project_id(self.project_id)
        validate_account_id(self.account_id)
        validate_model_id(self.model_id)
        if self.priority is not None:
            self.priority = int(self.priority)
        if self.weight is not None:
            self.weight = max(float(self.weight), 0.0)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ProjectRouteOverride":
        return cls(
            project_id=row["project_id"],
            account_id=row["account_id"],
            model_id=row["model_id"],
            priority=row["priority"],
            weight=row["weight"],
            enabled=bool(row["enabled"]),
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(slots=True)
class ProviderHealth:
    account_id: str
    model_id: str = ""
    status: HealthStatus = HealthStatus.UNKNOWN
    latency_ms: int | None = None
    consecutive_failures: int = 0
    last_error: str = ""
    checked_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        validate_account_id(self.account_id)
        if self.model_id:
            validate_model_id(self.model_id)
        if self.latency_ms is not None:
            self.latency_ms = max(int(self.latency_ms), 0)
        self.consecutive_failures = max(int(self.consecutive_failures), 0)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def unknown(cls, account_id: str, model_id: str = "") -> "ProviderHealth":
        return cls(account_id=account_id, model_id=model_id)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ProviderHealth":
        return cls(
            account_id=row["account_id"],
            model_id=row["model_id"],
            status=HealthStatus(row["status"]),
            latency_ms=row["latency_ms"],
            consecutive_failures=row["consecutive_failures"],
            last_error=row["last_error"],
            checked_at=row["checked_at"],
        )


@dataclass(slots=True)
class RouteRequest:
    project_id: str = ""
    capabilities: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    max_cost_usd: float | None = None
    require_batch: bool = False
    preferred_providers: list[str] = field(default_factory=list)
    preferred_accounts: list[str] = field(default_factory=list)
    limit: int = 10

    def __post_init__(self) -> None:
        self.project_id = self.project_id.strip()
        if self.project_id:
            validate_project_id(self.project_id)
        self.capabilities = normalize_capabilities(self.capabilities)
        self.input_tokens = max(int(self.input_tokens), 0)
        self.output_tokens = max(int(self.output_tokens), 0)
        if self.max_cost_usd is not None:
            self.max_cost_usd = max(float(self.max_cost_usd), 0.0)
        self.preferred_providers = normalize_capabilities(self.preferred_providers)
        self.preferred_accounts = [
            item.strip() for item in self.preferred_accounts if item.strip()
        ]
        self.limit = max(int(self.limit), 1)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RouteCandidate:
    account: ProviderAccount
    model: ModelSpec
    ability: RouteAbility
    health: ProviderHealth
    score: float
    estimated_cost_usd: float
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account.to_dict(),
            "model": self.model.to_dict(),
            "ability": self.ability.to_dict(),
            "health": self.health.to_dict(),
            "score": round(self.score, 4),
            "estimated_cost_usd": round(self.estimated_cost_usd, 8),
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class RouteDecision:
    selected: RouteCandidate | None
    candidates: list[RouteCandidate]
    rejected: list[dict[str, Any]]
    request: RouteRequest

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "selected": self.selected.to_dict() if self.selected else None,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "rejected": list(self.rejected),
        }


class ProviderRouterStore:
    def __init__(
        self,
        workspace: Path | str = ".",
        db_path: str = ".omni/provider-router.sqlite3",
        *,
        create: bool = True,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.db_path = self._safe_path(db_path)
        if create:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()

    def upsert_account(self, account: ProviderAccount) -> ProviderAccount:
        now = _now()
        with self._connect() as conn:
            existing = self._get_account(conn, account.account_id)
            if existing is not None:
                account.created_at = existing.created_at
            account.updated_at = now
            conn.execute(
                """
                INSERT INTO provider_accounts (
                    account_id, provider, name, base_url, secret_ref, proxy_url,
                    status, account_group, notes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    provider = excluded.provider,
                    name = excluded.name,
                    base_url = excluded.base_url,
                    secret_ref = excluded.secret_ref,
                    proxy_url = excluded.proxy_url,
                    status = excluded.status,
                    account_group = excluded.account_group,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    account.account_id,
                    account.provider,
                    account.name,
                    account.base_url,
                    account.secret_ref,
                    account.proxy_url,
                    account.status.value,
                    account.account_group,
                    account.notes,
                    account.created_at,
                    account.updated_at,
                ),
            )
            conn.commit()
        return account

    def list_accounts(
        self,
        *,
        status: str | None = None,
        provider: str | None = None,
    ) -> list[ProviderAccount]:
        if not self.db_path.exists():
            return []
        clauses: list[str] = []
        params: list[str] = []
        if status:
            clauses.append("status = ?")
            params.append(ProviderAccountStatus(status).value)
        if provider:
            clauses.append("provider = ?")
            params.append(provider)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM provider_accounts
                {where}
                ORDER BY provider, account_id
                """,
                params,
            ).fetchall()
        return [ProviderAccount.from_row(row) for row in rows]

    def get_account(self, account_id: str) -> ProviderAccount:
        validate_account_id(account_id)
        if not self.db_path.exists():
            raise KeyError(f"provider account does not exist: {account_id}")
        with self._connect() as conn:
            account = self._get_account(conn, account_id)
        if account is None:
            raise KeyError(f"provider account does not exist: {account_id}")
        return account

    def disable_account(
        self,
        account_id: str,
        *,
        auto: bool = False,
        reason: str = "",
    ) -> ProviderAccount:
        account = self.get_account(account_id)
        account.status = (
            ProviderAccountStatus.AUTO_DISABLED
            if auto
            else ProviderAccountStatus.DISABLED
        )
        account.notes = _append_note(account.notes, reason)
        return self.upsert_account(account)

    def upsert_model(self, model: ModelSpec) -> ModelSpec:
        now = _now()
        with self._connect() as conn:
            existing = self._get_model(conn, model.model_id)
            if existing is not None:
                model.created_at = existing.created_at
            model.updated_at = now
            conn.execute(
                """
                INSERT INTO model_catalog (
                    model_id, display_name, status, capabilities, context_window,
                    input_usd_per_million, output_usd_per_million,
                    cache_read_usd_per_million, cache_write_usd_per_million,
                    supports_batch, notes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(model_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    status = excluded.status,
                    capabilities = excluded.capabilities,
                    context_window = excluded.context_window,
                    input_usd_per_million = excluded.input_usd_per_million,
                    output_usd_per_million = excluded.output_usd_per_million,
                    cache_read_usd_per_million = excluded.cache_read_usd_per_million,
                    cache_write_usd_per_million = excluded.cache_write_usd_per_million,
                    supports_batch = excluded.supports_batch,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    model.model_id,
                    model.display_name,
                    model.status.value,
                    json.dumps(model.capabilities, ensure_ascii=False),
                    model.context_window,
                    model.input_usd_per_million,
                    model.output_usd_per_million,
                    model.cache_read_usd_per_million,
                    model.cache_write_usd_per_million,
                    int(model.supports_batch),
                    model.notes,
                    model.created_at,
                    model.updated_at,
                ),
            )
            conn.commit()
        return model

    def list_models(
        self,
        *,
        status: str | None = None,
        capability: str | None = None,
    ) -> list[ModelSpec]:
        if not self.db_path.exists():
            return []
        clauses: list[str] = []
        params: list[str] = []
        if status:
            clauses.append("status = ?")
            params.append(ModelStatus(status).value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM model_catalog
                {where}
                ORDER BY model_id
                """,
                params,
            ).fetchall()
        models = [ModelSpec.from_row(row) for row in rows]
        normalized_capability = normalize_capabilities([capability or ""])
        if normalized_capability:
            wanted = normalized_capability[0]
            models = [model for model in models if wanted in model.capabilities]
        return models

    def get_model(self, model_id: str) -> ModelSpec:
        validate_model_id(model_id)
        if not self.db_path.exists():
            raise KeyError(f"model does not exist: {model_id}")
        with self._connect() as conn:
            model = self._get_model(conn, model_id)
        if model is None:
            raise KeyError(f"model does not exist: {model_id}")
        return model

    def upsert_ability(self, ability: RouteAbility) -> RouteAbility:
        now = _now()
        with self._connect() as conn:
            if self._get_account(conn, ability.account_id) is None:
                raise KeyError(f"provider account does not exist: {ability.account_id}")
            if self._get_model(conn, ability.model_id) is None:
                raise KeyError(f"model does not exist: {ability.model_id}")

            existing = self._get_ability(conn, ability.account_id, ability.model_id)
            if existing is not None:
                ability.created_at = existing.created_at
            ability.updated_at = now
            conn.execute(
                """
                INSERT INTO route_abilities (
                    account_id, model_id, enabled, priority, weight,
                    model_mapping, notes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, model_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    priority = excluded.priority,
                    weight = excluded.weight,
                    model_mapping = excluded.model_mapping,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    ability.account_id,
                    ability.model_id,
                    int(ability.enabled),
                    ability.priority,
                    ability.weight,
                    ability.model_mapping,
                    ability.notes,
                    ability.created_at,
                    ability.updated_at,
                ),
            )
            conn.commit()
        return ability

    def list_abilities(
        self,
        *,
        account_id: str | None = None,
        model_id: str | None = None,
        enabled: bool | None = None,
    ) -> list[RouteAbility]:
        if not self.db_path.exists():
            return []
        clauses: list[str] = []
        params: list[Any] = []
        if account_id:
            clauses.append("account_id = ?")
            params.append(account_id)
        if model_id:
            clauses.append("model_id = ?")
            params.append(model_id)
        if enabled is not None:
            clauses.append("enabled = ?")
            params.append(int(enabled))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM route_abilities
                {where}
                ORDER BY priority DESC, account_id, model_id
                """,
                params,
            ).fetchall()
        return [RouteAbility.from_row(row) for row in rows]

    def upsert_project_profile(
        self,
        profile: ProjectRouteProfile,
    ) -> ProjectRouteProfile:
        now = _now()
        with self._connect() as conn:
            existing = self._get_project_profile(conn, profile.project_id)
            if existing is not None:
                profile.created_at = existing.created_at
            profile.updated_at = now
            conn.execute(
                """
                INSERT INTO project_route_profiles (
                    project_id, default_capabilities, max_cost_usd, require_batch,
                    preferred_providers, preferred_accounts, notes,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    default_capabilities = excluded.default_capabilities,
                    max_cost_usd = excluded.max_cost_usd,
                    require_batch = excluded.require_batch,
                    preferred_providers = excluded.preferred_providers,
                    preferred_accounts = excluded.preferred_accounts,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    profile.project_id,
                    json.dumps(profile.default_capabilities, ensure_ascii=False),
                    profile.max_cost_usd,
                    int(profile.require_batch),
                    json.dumps(profile.preferred_providers, ensure_ascii=False),
                    json.dumps(profile.preferred_accounts, ensure_ascii=False),
                    profile.notes,
                    profile.created_at,
                    profile.updated_at,
                ),
            )
            conn.commit()
        return profile

    def list_project_profiles(self) -> list[ProjectRouteProfile]:
        if not self.db_path.exists():
            return []
        with self._connect() as conn:
            if not self._table_exists(conn, "project_route_profiles"):
                return []
            rows = conn.execute(
                """
                SELECT * FROM project_route_profiles
                ORDER BY project_id
                """
            ).fetchall()
        return [ProjectRouteProfile.from_row(row) for row in rows]

    def get_project_profile(self, project_id: str) -> ProjectRouteProfile:
        validate_project_id(project_id)
        if not self.db_path.exists():
            raise KeyError(f"project route profile does not exist: {project_id}")
        with self._connect() as conn:
            profile = self._get_project_profile(conn, project_id)
        if profile is None:
            raise KeyError(f"project route profile does not exist: {project_id}")
        return profile

    def upsert_project_override(
        self,
        override: ProjectRouteOverride,
    ) -> ProjectRouteOverride:
        now = _now()
        with self._connect() as conn:
            if self._get_account(conn, override.account_id) is None:
                raise KeyError(f"provider account does not exist: {override.account_id}")
            if self._get_model(conn, override.model_id) is None:
                raise KeyError(f"model does not exist: {override.model_id}")
            existing = self._get_project_override(
                conn,
                override.project_id,
                override.account_id,
                override.model_id,
            )
            if existing is not None:
                override.created_at = existing.created_at
            override.updated_at = now
            conn.execute(
                """
                INSERT INTO project_route_overrides (
                    project_id, account_id, model_id, priority, weight,
                    enabled, notes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, account_id, model_id) DO UPDATE SET
                    priority = excluded.priority,
                    weight = excluded.weight,
                    enabled = excluded.enabled,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    override.project_id,
                    override.account_id,
                    override.model_id,
                    override.priority,
                    override.weight,
                    int(override.enabled),
                    override.notes,
                    override.created_at,
                    override.updated_at,
                ),
            )
            conn.commit()
        return override

    def list_project_overrides(
        self,
        *,
        project_id: str | None = None,
    ) -> list[ProjectRouteOverride]:
        if not self.db_path.exists():
            return []
        clauses: list[str] = []
        params: list[str] = []
        if project_id:
            validate_project_id(project_id)
            clauses.append("project_id = ?")
            params.append(project_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            if not self._table_exists(conn, "project_route_overrides"):
                return []
            rows = conn.execute(
                f"""
                SELECT * FROM project_route_overrides
                {where}
                ORDER BY project_id, account_id, model_id
                """,
                params,
            ).fetchall()
        return [ProjectRouteOverride.from_row(row) for row in rows]

    def set_health(self, health: ProviderHealth) -> ProviderHealth:
        with self._connect() as conn:
            if self._get_account(conn, health.account_id) is None:
                raise KeyError(f"provider account does not exist: {health.account_id}")
            if health.model_id and self._get_model(conn, health.model_id) is None:
                raise KeyError(f"model does not exist: {health.model_id}")
            conn.execute(
                """
                INSERT INTO provider_health (
                    account_id, model_id, status, latency_ms, consecutive_failures,
                    last_error, checked_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, model_id) DO UPDATE SET
                    status = excluded.status,
                    latency_ms = excluded.latency_ms,
                    consecutive_failures = excluded.consecutive_failures,
                    last_error = excluded.last_error,
                    checked_at = excluded.checked_at
                """,
                (
                    health.account_id,
                    health.model_id,
                    health.status.value,
                    health.latency_ms,
                    health.consecutive_failures,
                    health.last_error,
                    health.checked_at,
                ),
            )
            conn.commit()
        return health

    def get_health(self, account_id: str, model_id: str = "") -> ProviderHealth:
        validate_account_id(account_id)
        if model_id:
            validate_model_id(model_id)
        if not self.db_path.exists():
            return ProviderHealth.unknown(account_id, model_id)
        with self._connect() as conn:
            health = self._get_health(conn, account_id, model_id)
            if health is None and model_id:
                health = self._get_health(conn, account_id, "")
        return health or ProviderHealth.unknown(account_id, model_id)

    def list_health(self) -> list[ProviderHealth]:
        if not self.db_path.exists():
            return []
        with self._connect() as conn:
            if not self._table_exists(conn, "provider_health"):
                return []
            rows = conn.execute(
                """
                SELECT * FROM provider_health
                ORDER BY account_id, model_id
                """
            ).fetchall()
        return [ProviderHealth.from_row(row) for row in rows]

    def route(self, request: RouteRequest) -> RouteDecision:
        if not self.db_path.exists():
            return RouteDecision(
                selected=None,
                candidates=[],
                rejected=[],
                request=request,
            )

        candidates: list[RouteCandidate] = []
        rejected: list[dict[str, Any]] = []

        with self._connect() as conn:
            effective_request = self._apply_project_profile(conn, request)
            project_overrides = self._project_overrides_by_key(
                conn,
                effective_request.project_id,
            )
            rows = conn.execute(
                """
                SELECT
                    a.account_id AS ability_account_id,
                    a.model_id AS ability_model_id,
                    a.enabled,
                    a.priority,
                    a.weight,
                    a.model_mapping,
                    a.notes AS ability_notes,
                    a.created_at AS ability_created_at,
                    a.updated_at AS ability_updated_at,
                    p.*,
                    m.model_id AS model_model_id,
                    m.display_name,
                    m.status AS model_status,
                    m.capabilities,
                    m.context_window,
                    m.input_usd_per_million,
                    m.output_usd_per_million,
                    m.cache_read_usd_per_million,
                    m.cache_write_usd_per_million,
                    m.supports_batch,
                    m.notes AS model_notes,
                    m.created_at AS model_created_at,
                    m.updated_at AS model_updated_at
                FROM route_abilities a
                JOIN provider_accounts p ON p.account_id = a.account_id
                JOIN model_catalog m ON m.model_id = a.model_id
                ORDER BY a.priority DESC, a.weight DESC, p.account_id, m.model_id
                """
            ).fetchall()

            for row in rows:
                account = ProviderAccount.from_row(row)
                model = _model_from_join_row(row)
                ability = _ability_from_join_row(row)
                override = project_overrides.get((account.account_id, model.model_id))
                if override is not None:
                    ability = _apply_project_override(ability, override)
                reject_reason = _reject_reason(
                    account,
                    model,
                    ability,
                    effective_request,
                )
                cost = model.estimate_cost(
                    input_tokens=effective_request.input_tokens,
                    output_tokens=effective_request.output_tokens,
                )
                if reject_reason is None and effective_request.max_cost_usd is not None:
                    if cost > effective_request.max_cost_usd:
                        reject_reason = (
                            f"estimated cost {cost:.8f} exceeds max_cost_usd "
                            f"{effective_request.max_cost_usd:.8f}"
                        )

                health = self._get_health(conn, account.account_id, model.model_id)
                if health is None:
                    health = self._get_health(conn, account.account_id, "")
                health = health or ProviderHealth.unknown(
                    account.account_id,
                    model.model_id,
                )
                if reject_reason is None and health.status == HealthStatus.DOWN:
                    reject_reason = f"health is {health.status.value}"

                if reject_reason:
                    rejected.append(
                        {
                            "account_id": account.account_id,
                            "model_id": model.model_id,
                            "reason": reject_reason,
                        }
                    )
                    continue

                score, reasons, warnings = _score_candidate(
                    account,
                    model,
                    ability,
                    health,
                    effective_request,
                    cost,
                )
                if override is not None:
                    reasons.append(f"project_override={override.project_id}")
                candidates.append(
                    RouteCandidate(
                        account=account,
                        model=model,
                        ability=ability,
                        health=health,
                        score=score,
                        estimated_cost_usd=cost,
                        reasons=reasons,
                        warnings=warnings,
                    )
                )

        candidates.sort(
            key=lambda item: (
                -item.ability.priority,
                -item.score,
                item.estimated_cost_usd,
                item.account.account_id,
                item.model.model_id,
            )
        )
        limited_candidates = candidates[: effective_request.limit]
        return RouteDecision(
            selected=limited_candidates[0] if limited_candidates else None,
            candidates=limited_candidates,
            rejected=rejected,
            request=effective_request,
        )

    def stats(self) -> dict[str, int]:
        if not self.db_path.exists():
            return {
                "provider_accounts": 0,
                "model_catalog": 0,
                "route_abilities": 0,
                "project_route_profiles": 0,
                "project_route_overrides": 0,
                "provider_health": 0,
                "usage_request_logs": 0,
            }

        with self._connect() as conn:
            return {
                table: self._count_table(conn, table)
                for table in [
                    "provider_accounts",
                    "model_catalog",
                    "route_abilities",
                    "project_route_profiles",
                    "project_route_overrides",
                    "provider_health",
                    "usage_request_logs",
                ]
            }

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA foreign_keys = ON;
                PRAGMA user_version = 1;

                CREATE TABLE IF NOT EXISTS provider_accounts (
                    account_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    secret_ref TEXT NOT NULL,
                    proxy_url TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    account_group TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_catalog (
                    model_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    capabilities TEXT NOT NULL,
                    context_window INTEGER NOT NULL,
                    input_usd_per_million REAL NOT NULL,
                    output_usd_per_million REAL NOT NULL,
                    cache_read_usd_per_million REAL NOT NULL,
                    cache_write_usd_per_million REAL NOT NULL,
                    supports_batch INTEGER NOT NULL,
                    notes TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS route_abilities (
                    account_id TEXT NOT NULL REFERENCES provider_accounts(account_id) ON DELETE CASCADE,
                    model_id TEXT NOT NULL REFERENCES model_catalog(model_id) ON DELETE CASCADE,
                    enabled INTEGER NOT NULL,
                    priority INTEGER NOT NULL,
                    weight REAL NOT NULL,
                    model_mapping TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, model_id)
                );

                CREATE TABLE IF NOT EXISTS provider_health (
                    account_id TEXT NOT NULL REFERENCES provider_accounts(account_id) ON DELETE CASCADE,
                    model_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency_ms INTEGER,
                    consecutive_failures INTEGER NOT NULL,
                    last_error TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, model_id)
                );

                CREATE TABLE IF NOT EXISTS project_route_profiles (
                    project_id TEXT PRIMARY KEY,
                    default_capabilities TEXT NOT NULL,
                    max_cost_usd REAL,
                    require_batch INTEGER NOT NULL,
                    preferred_providers TEXT NOT NULL,
                    preferred_accounts TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS project_route_overrides (
                    project_id TEXT NOT NULL,
                    account_id TEXT NOT NULL REFERENCES provider_accounts(account_id) ON DELETE CASCADE,
                    model_id TEXT NOT NULL REFERENCES model_catalog(model_id) ON DELETE CASCADE,
                    priority INTEGER,
                    weight REAL,
                    enabled INTEGER NOT NULL,
                    notes TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, account_id, model_id)
                );

                CREATE TABLE IF NOT EXISTS usage_request_logs (
                    request_id TEXT PRIMARY KEY,
                    task_name TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    estimated_cost_usd REAL NOT NULL,
                    actual_cost_usd REAL,
                    latency_ms INTEGER,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL,
                    route_reasons TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS usage_daily_rollups (
                    day TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    request_count INTEGER NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    estimated_cost_usd REAL NOT NULL,
                    actual_cost_usd REAL,
                    PRIMARY KEY(day, account_id, model_id)
                );

                CREATE INDEX IF NOT EXISTS idx_route_abilities_model
                    ON route_abilities(model_id);
                CREATE INDEX IF NOT EXISTS idx_provider_health_status
                    ON provider_health(status);
                CREATE INDEX IF NOT EXISTS idx_project_route_overrides_project
                    ON project_route_overrides(project_id);
                CREATE INDEX IF NOT EXISTS idx_usage_request_logs_created
                    ON usage_request_logs(created_at);
                """
            )
            self._ensure_column(
                conn,
                "provider_accounts",
                "proxy_url",
                "TEXT NOT NULL DEFAULT ''",
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _safe_path(self, relative_path: str) -> Path:
        target = (self.workspace / relative_path).resolve()
        try:
            target.relative_to(self.workspace)
        except ValueError as exc:
            raise PermissionError("target path is outside the workspace") from exc
        return target

    def _get_account(
        self,
        conn: sqlite3.Connection,
        account_id: str,
    ) -> ProviderAccount | None:
        row = conn.execute(
            "SELECT * FROM provider_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        return ProviderAccount.from_row(row) if row is not None else None

    def _get_model(
        self,
        conn: sqlite3.Connection,
        model_id: str,
    ) -> ModelSpec | None:
        row = conn.execute(
            "SELECT * FROM model_catalog WHERE model_id = ?",
            (model_id,),
        ).fetchone()
        return ModelSpec.from_row(row) if row is not None else None

    def _get_ability(
        self,
        conn: sqlite3.Connection,
        account_id: str,
        model_id: str,
    ) -> RouteAbility | None:
        row = conn.execute(
            """
            SELECT * FROM route_abilities
            WHERE account_id = ? AND model_id = ?
            """,
            (account_id, model_id),
        ).fetchone()
        return RouteAbility.from_row(row) if row is not None else None

    def _get_health(
        self,
        conn: sqlite3.Connection,
        account_id: str,
        model_id: str,
    ) -> ProviderHealth | None:
        row = conn.execute(
            """
            SELECT * FROM provider_health
            WHERE account_id = ? AND model_id = ?
            """,
            (account_id, model_id),
        ).fetchone()
        return ProviderHealth.from_row(row) if row is not None else None

    def _get_project_profile(
        self,
        conn: sqlite3.Connection,
        project_id: str,
    ) -> ProjectRouteProfile | None:
        if not self._table_exists(conn, "project_route_profiles"):
            return None
        row = conn.execute(
            "SELECT * FROM project_route_profiles WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        return ProjectRouteProfile.from_row(row) if row is not None else None

    def _get_project_override(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        account_id: str,
        model_id: str,
    ) -> ProjectRouteOverride | None:
        if not self._table_exists(conn, "project_route_overrides"):
            return None
        row = conn.execute(
            """
            SELECT * FROM project_route_overrides
            WHERE project_id = ? AND account_id = ? AND model_id = ?
            """,
            (project_id, account_id, model_id),
        ).fetchone()
        return ProjectRouteOverride.from_row(row) if row is not None else None

    def _apply_project_profile(
        self,
        conn: sqlite3.Connection,
        request: RouteRequest,
    ) -> RouteRequest:
        if not request.project_id:
            return request
        profile = self._get_project_profile(conn, request.project_id)
        if profile is None:
            return request

        max_cost_usd = request.max_cost_usd
        if profile.max_cost_usd is not None:
            max_cost_usd = (
                profile.max_cost_usd
                if max_cost_usd is None
                else min(max_cost_usd, profile.max_cost_usd)
            )

        return RouteRequest(
            project_id=request.project_id,
            capabilities=_union_sorted(profile.default_capabilities, request.capabilities),
            input_tokens=request.input_tokens,
            output_tokens=request.output_tokens,
            max_cost_usd=max_cost_usd,
            require_batch=profile.require_batch or request.require_batch,
            preferred_providers=_union_sorted(
                profile.preferred_providers,
                request.preferred_providers,
            ),
            preferred_accounts=_union_sorted(
                profile.preferred_accounts,
                request.preferred_accounts,
            ),
            limit=request.limit,
        )

    def _project_overrides_by_key(
        self,
        conn: sqlite3.Connection,
        project_id: str,
    ) -> dict[tuple[str, str], ProjectRouteOverride]:
        if not project_id:
            return {}
        if not self._table_exists(conn, "project_route_overrides"):
            return {}
        rows = conn.execute(
            """
            SELECT * FROM project_route_overrides
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchall()
        return {
            (override.account_id, override.model_id): override
            for override in (ProjectRouteOverride.from_row(row) for row in rows)
        }

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (table,),
        ).fetchone()
        return row is not None

    def _count_table(self, conn: sqlite3.Connection, table: str) -> int:
        if not self._table_exists(conn, table):
            return 0
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if column not in {row["name"] for row in rows}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def normalize_capabilities(capabilities: list[str]) -> list[str]:
    return sorted(
        {
            capability.strip().lower()
            for capability in capabilities
            if capability and capability.strip()
        }
    )


def validate_provider_id(provider_id: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,63}", provider_id):
        raise ValueError(
            "provider id must be 2-64 lowercase letters, numbers, dots, dashes, or underscores"
        )


def validate_account_id(account_id: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,63}", account_id):
        raise ValueError(
            "account id must be 2-64 lowercase letters, numbers, dots, dashes, or underscores"
        )


def validate_model_id(model_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,127}", model_id):
        raise ValueError(
            "model id must be 1-128 letters, numbers, dots, colons, slashes, pluses, or dashes"
        )


def validate_project_id(project_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,127}", project_id):
        raise ValueError(
            "project id must be 1-128 letters, numbers, dots, colons, slashes, pluses, or dashes"
        )


def validate_secret_ref(secret_ref: str) -> None:
    if not secret_ref:
        return
    if secret_ref.startswith(("env:", "keychain:", "local:", "runtime:")):
        if len(secret_ref.split(":", 1)[1].strip()) < 2:
            raise ValueError("secret_ref target is required")
        return
    if _looks_like_raw_secret(secret_ref):
        raise ValueError(
            "secret_ref must reference env:, keychain:, local:, or runtime:, not a raw secret"
        )
    raise ValueError("secret_ref must start with env:, keychain:, local:, or runtime:")


def validate_proxy_url(proxy_url: str) -> None:
    if not proxy_url:
        return
    stripped = proxy_url.strip()
    if stripped.lower() == "unset":
        raise ValueError("proxy_url should be empty instead of unset")
    if stripped.startswith("env:"):
        if len(stripped.split(":", 1)[1].strip()) < 2:
            raise ValueError("proxy_url env target is required")
        return
    if stripped.startswith(("http://", "https://", "socks5://", "socks5h://")):
        return
    raise ValueError(
        "proxy_url must be empty, env:, http://, https://, socks5://, or socks5h://"
    )


def _reject_reason(
    account: ProviderAccount,
    model: ModelSpec,
    ability: RouteAbility,
    request: RouteRequest,
) -> str | None:
    if account.status != ProviderAccountStatus.ACTIVE:
        return f"account status is {account.status.value}"
    if model.status != ModelStatus.ACTIVE:
        return f"model status is {model.status.value}"
    if not ability.enabled:
        return "route ability is disabled"

    missing = sorted(set(request.capabilities) - set(model.capabilities))
    if missing:
        return f"missing capabilities: {', '.join(missing)}"
    if request.require_batch and not model.supports_batch:
        return "batch is required but model does not support batch"
    return None


def _apply_project_override(
    ability: RouteAbility,
    override: ProjectRouteOverride,
) -> RouteAbility:
    return RouteAbility(
        account_id=ability.account_id,
        model_id=ability.model_id,
        enabled=ability.enabled and override.enabled,
        priority=override.priority if override.priority is not None else ability.priority,
        weight=override.weight if override.weight is not None else ability.weight,
        model_mapping=ability.model_mapping,
        notes=_append_note(ability.notes, override.notes),
        created_at=ability.created_at,
        updated_at=ability.updated_at,
    )


def _score_candidate(
    account: ProviderAccount,
    model: ModelSpec,
    ability: RouteAbility,
    health: ProviderHealth,
    request: RouteRequest,
    cost: float,
) -> tuple[float, list[str], list[str]]:
    reasons = [
        f"priority={ability.priority}",
        f"weight={ability.weight:g}",
        f"capabilities={','.join(model.capabilities) or 'none'}",
    ]
    warnings: list[str] = []

    score = float(ability.priority) * 100.0 + ability.weight * 10.0
    score += len(request.capabilities) * 2.0

    if account.provider in request.preferred_providers:
        score += 20.0
        reasons.append(f"preferred provider {account.provider}")
    if account.account_id in request.preferred_accounts:
        score += 20.0
        reasons.append(f"preferred account {account.account_id}")

    health_bonus = {
        HealthStatus.HEALTHY: 12.0,
        HealthStatus.UNKNOWN: 2.0,
        HealthStatus.DEGRADED: -8.0,
        HealthStatus.LIMITED: -12.0,
        HealthStatus.DOWN: -1000.0,
    }[health.status]
    score += health_bonus
    if health.status == HealthStatus.UNKNOWN:
        warnings.append("health is unknown")
    elif health.status in {HealthStatus.DEGRADED, HealthStatus.LIMITED}:
        warnings.append(f"health is {health.status.value}")
    else:
        reasons.append(f"health={health.status.value}")

    if health.latency_ms is not None:
        latency_bonus = max(0.0, 10.0 - health.latency_ms / 500.0)
        score += latency_bonus
        reasons.append(f"latency_ms={health.latency_ms}")

    if cost > 0:
        score -= min(cost * 100.0, 20.0)
        reasons.append(f"estimated_cost_usd={cost:.8f}")
    else:
        score += 2.0
        reasons.append("estimated_cost_usd=0")

    return score, reasons, warnings


def _model_from_join_row(row: sqlite3.Row) -> ModelSpec:
    return ModelSpec(
        model_id=row["model_model_id"],
        display_name=row["display_name"],
        status=ModelStatus(row["model_status"]),
        capabilities=json.loads(row["capabilities"]),
        context_window=row["context_window"],
        input_usd_per_million=row["input_usd_per_million"],
        output_usd_per_million=row["output_usd_per_million"],
        cache_read_usd_per_million=row["cache_read_usd_per_million"],
        cache_write_usd_per_million=row["cache_write_usd_per_million"],
        supports_batch=bool(row["supports_batch"]),
        notes=row["model_notes"],
        created_at=row["model_created_at"],
        updated_at=row["model_updated_at"],
    )


def _ability_from_join_row(row: sqlite3.Row) -> RouteAbility:
    return RouteAbility(
        account_id=row["ability_account_id"],
        model_id=row["ability_model_id"],
        enabled=bool(row["enabled"]),
        priority=row["priority"],
        weight=row["weight"],
        model_mapping=row["model_mapping"],
        notes=row["ability_notes"],
        created_at=row["ability_created_at"],
        updated_at=row["ability_updated_at"],
    )


def _looks_like_raw_secret(value: str) -> bool:
    stripped = value.strip()
    if stripped.startswith(("sk-", "sk_", "sk-ant-", "AIza", "xai-", "Bearer ")):
        return True
    return len(stripped) >= 32 and ":" not in stripped and " " not in stripped


def _append_note(notes: str, reason: str) -> str:
    reason = reason.strip()
    if not reason:
        return notes
    if not notes:
        return reason
    return f"{notes}\n{reason}"


def _union_sorted(first: list[str], second: list[str]) -> list[str]:
    return sorted({item.strip() for item in [*first, *second] if item.strip()})
