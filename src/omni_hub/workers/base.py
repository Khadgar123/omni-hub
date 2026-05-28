"""Artifact + WorkerAdapter contracts.

The Artifact is the *output* contract: anything a worker produces gets
wrapped into one of these so the queue / audit / preference layers can
treat python ops, headless Claude runs and Codex runs uniformly.

The WorkerAdapter is a tiny duck-typed protocol — adapters can also be
plain callables.  Subclassing isn't required; matching the ``run`` method
signature is.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4


GENERATION = "generation"
REPORT = "report"
PATCH = "patch"
SCAN_RESULT = "scan_result"
TEXT = "text"

ArtifactKind = str  # one of the constants above (or future kinds)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def new_artifact_id() -> str:
    return str(uuid4())


@dataclass(slots=True)
class Artifact:
    """Wrapper around any worker output.

    ``data`` carries the kind-specific payload — a GenerationRecord dict, a
    rendered markdown report, a git patch, a redundancy scan result, …
    Wrapping it gives audit / preference / propose-list a single shape to
    consume regardless of which lane produced it.
    """

    artifact_id: str = field(default_factory=new_artifact_id)
    kind: ArtifactKind = TEXT
    data: dict[str, Any] = field(default_factory=dict)
    task_id: int | None = None
    worker_lane: str = ""
    worker_id: str = ""
    duration_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Artifact":
        return cls(
            artifact_id=str(data.get("artifact_id") or new_artifact_id()),
            kind=str(data.get("kind", TEXT)),
            data=dict(data.get("data", {})),
            task_id=data.get("task_id"),
            worker_lane=str(data.get("worker_lane", "")),
            worker_id=str(data.get("worker_id", "")),
            duration_ms=int(data.get("duration_ms", 0)),
            tokens_in=int(data.get("tokens_in", 0)),
            tokens_out=int(data.get("tokens_out", 0)),
            cost_usd=float(data.get("cost_usd", 0.0)),
            error=data.get("error"),
            created_at=str(data.get("created_at") or _utcnow()),
        )


class WorkerError(RuntimeError):
    """Raised by an adapter when the underlying process fails non-recoverably."""


class WorkerTimeout(WorkerError):
    """Raised when a worker exceeds the configured wall-clock timeout."""


class WorkerAdapter(Protocol):
    """Anything with a ``run(task) -> Artifact`` and a ``name`` is an adapter."""

    name: str
    lane: str

    def run(self, task: "Task", *, timeout_sec: int = 300) -> Artifact:  # noqa: F821
        ...
