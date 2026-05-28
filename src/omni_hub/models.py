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
    # v0.18: trace_id propagates across Command → Policy → Step → Artifact
    # → Proposal → Claim → AuditEvent.  Auto-filled by Runner when empty so
    # callers don't have to remember it.
    trace_id: str = ""
    # v0.18-A: when True, the handler returns a ProjectionDiff and writes
    # NOTHING — Pulumi/Terraform plan/apply.  Policy still evaluates so
    # the preview itself is policy-checked (READ_ONLY in practice).
    dry_run: bool = False

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
    # v0.18-C: every result carries the trace_id so CLI / MCP / preference
    # store / claim ledger / preference jsonl can all stitch back.
    trace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


# ---------------------------------------------------------------------------
# v0.18-A: ProjectionDiff — what `Command.preview` returns.
#
# Pulumi/Terraform pattern: plan and apply use the SAME code path, the
# handler just sees `spec.dry_run=True` and emits a diff instead of
# materialising it.  The diff is typed so policy / human review / MCP
# clients can inspect concrete impact ("approving this proposal will add 2
# wiki pages, 4 claims, 1 graph edge, 6 FTS rows").
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ProjectionChange:
    """One projection-level change in a ProjectionDiff."""

    projection_name: str          # "wiki" | "fts5" | "claims" | "graph" | "preference" | "skill" | "raw" | "evidence" | "index_md" | "log_md"
    op: str                       # "add" | "remove" | "modify"
    target: str                   # path / claim_id / row key
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProjectionDiff:
    """Aggregate of all projection-level changes a Command would cause.

    Returned by handlers when ``OperationSpec.dry_run=True``.  Empty diff
    means "no projection writes" — useful for READ_ONLY commands where the
    preview just confirms safety.
    """

    command_name: str
    trace_id: str
    changes: list[ProjectionChange] = field(default_factory=list)
    counts_by_projection: dict[str, int] = field(default_factory=dict)
    counts_by_op: dict[str, int] = field(default_factory=dict)
    affected_size_bytes: int = 0
    schema_version: str = "v0.18"

    def add(self, change: ProjectionChange) -> None:
        self.changes.append(change)
        self.counts_by_projection[change.projection_name] = (
            self.counts_by_projection.get(change.projection_name, 0) + 1
        )
        self.counts_by_op[change.op] = self.counts_by_op.get(change.op, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_name": self.command_name,
            "trace_id": self.trace_id,
            "schema_version": self.schema_version,
            "total_changes": len(self.changes),
            "counts_by_projection": dict(self.counts_by_projection),
            "counts_by_op": dict(self.counts_by_op),
            "affected_size_bytes": self.affected_size_bytes,
            "changes": [asdict(c) for c in self.changes],
        }


# ---------------------------------------------------------------------------
# v0.18-E: optimistic-concurrency exception for ClaimLedger writes
# ---------------------------------------------------------------------------


class ConcurrentModificationError(RuntimeError):
    """Raised when ClaimLedger.append sees expected_version mismatch.

    Single-user still hits this when an interactive Claude Code session
    and a headless `worker --lane claude` race against the same ledger.
    """
