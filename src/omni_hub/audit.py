from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import OperationResult, OperationSpec
from .policy import PolicyDecision


@dataclass(slots=True)
class AuditEvent:
    event_type: str
    operation_id: str
    data: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data


class AuditLogger:
    def __init__(self, path: Path | str = ".omni/audit/events.jsonl") -> None:
        self.path = Path(path)

    def record(
        self,
        event_type: str,
        operation: OperationSpec,
        *,
        decision: PolicyDecision | None = None,
        result: OperationResult | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AuditEvent:
        data: dict[str, Any] = {"operation": operation.to_dict()}
        if decision is not None:
            data["decision"] = asdict(decision)
        if result is not None:
            data["result"] = result.to_dict()
        if extra:
            data["extra"] = extra

        event = AuditEvent(
            event_type=event_type,
            operation_id=operation.operation_id,
            data=data,
        )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        return event
