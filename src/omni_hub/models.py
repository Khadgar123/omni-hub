from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum, IntEnum
from typing import Any
from uuid import uuid4


class RiskLevel(IntEnum):
    READ_ONLY = 0
    LOCAL_WRITE = 1
    EXTERNAL_SEND = 2
    EXTERNAL_PUBLISH = 3
    SANDBOX_EXECUTION = 4

    @property
    def code(self) -> str:
        return f"L{int(self)}"

    @classmethod
    def parse(cls, value: str | int | "RiskLevel") -> "RiskLevel":
        if isinstance(value, RiskLevel):
            return value
        if isinstance(value, int):
            return cls(value)

        normalized = value.strip().upper()
        if normalized.startswith("L") and normalized[1:].isdigit():
            return cls(int(normalized[1:]))
        return cls[normalized]


class OperationStatus(str, Enum):
    PENDING = "pending"
    WAITING_APPROVAL = "waiting_approval"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(slots=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_seconds: float = 0.0


@dataclass(slots=True)
class OperationSpec:
    name: str
    action: str
    connector: str = "local"
    payload: dict[str, Any] = field(default_factory=dict)
    actor: str = "local-user"
    source: str = "cli"
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    required_permissions: list[str] = field(default_factory=list)
    approval_required: bool | None = None
    sandbox_required: bool | None = None
    idempotency_key: str | None = None
    timeout_seconds: int = 60
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    operation_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk_level"] = self.risk_level.code
        data["created_at"] = self.created_at.isoformat()
        return data


@dataclass(slots=True)
class OperationResult:
    operation_id: str
    status: OperationStatus
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    policy_reason: str | None = None
    audit_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data
