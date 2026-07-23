"""Built-in (deterministic Python) worker adapter.

Pulls a Task from the queue, dispatches its packet through the existing
``OperationRunner`` so policy + audit fire as for any other write, and
wraps the result as an ``Artifact``.

A built-in TaskPacket looks like::

    {
        "operation": "harness_redundancy_scan",
        "payload":   {...},                  # operation-specific
        "kind":      "scan_result",          # artifact kind
        "risk_level": "L1"                   # optional, defaults LOCAL_WRITE
    }
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..audit import AuditLogger
from ..models import OperationSpec, OperationStatus, RiskLevel
from ..operation_receipts import OperationReceiptStore, WORKER_CONTEXT_KEY
from ..runner import OperationRunner
from ..queue import Task
from .base import Artifact, TEXT, WorkerError, new_artifact_id


class BuiltinAdapter:
    """Runs deterministic Python operations registered in ``builtins.py``."""

    name = "builtin"
    lane = "python"

    def __init__(
        self,
        runner: OperationRunner,
        *,
        worker_id: str = "builtin-worker",
    ) -> None:
        self.runner = runner
        self.worker_id = worker_id

    def run(self, task: Task, *, timeout_sec: int = 300) -> Artifact:
        packet = task.packet or {}
        operation = packet.get("operation")
        if not operation:
            raise WorkerError(
                f"task {task.id}: builtin lane requires 'operation' in packet"
            )

        payload: dict[str, Any] = dict(packet.get("payload", {}))
        if WORKER_CONTEXT_KEY in payload:
            raise WorkerError(
                f"task {task.id}: payload contains reserved worker execution context"
            )
        if task.claimed_by is not None and task.claimed_by != self.worker_id:
            raise WorkerError(
                f"task {task.id}: claimed worker does not match adapter worker"
            )
        payload[WORKER_CONTEXT_KEY] = {
            "trace_id": task.trace_id,
            "idempotency_key": task.idempotency_key,
            "task_id": task.id,
            "worker_id": self.worker_id,
            "lease_epoch": task.lease_epoch,
            "fencing_suffix": task.fencing_suffix(),
        }
        risk_value = packet.get("risk_level", RiskLevel.LOCAL_WRITE.code)
        operation_idempotency_key = task.idempotency_key or None
        spec = OperationSpec(
            name=str(operation),
            action=str(packet.get("action", "run")),
            connector=str(packet.get("connector", "local")),
            payload=payload,
            risk_level=RiskLevel.parse(risk_value),
            idempotency_key=operation_idempotency_key,
            trace_id=task.trace_id,
        )

        start = time.time()
        result = self.runner.run(spec, approved=bool(packet.get("approved", False)))
        elapsed_ms = int((time.time() - start) * 1000)

        if result.status is not OperationStatus.SUCCEEDED:
            return Artifact(
                artifact_id=new_artifact_id(),
                kind=str(packet.get("kind", TEXT)),
                data={"operation_id": result.operation_id, "policy_reason": result.policy_reason},
                task_id=task.id,
                worker_lane=self.lane,
                worker_id=self.worker_id,
                duration_ms=elapsed_ms,
                error=result.error or f"operation did not succeed: {result.status.value}",
            )

        return Artifact(
            artifact_id=new_artifact_id(),
            kind=str(packet.get("kind", TEXT)),
            data=dict(result.output) if isinstance(result.output, dict) else {"value": result.output},
            task_id=task.id,
            worker_lane=self.lane,
            worker_id=self.worker_id,
            duration_ms=elapsed_ms,
        )


def make_builtin_adapter(
    workspace: Path | str = ".",
    *,
    worker_id: str = "builtin-worker",
) -> BuiltinAdapter:
    """Convenience constructor — builds an OperationRunner with the
    default builtin registry, suitable for `omni-hub worker --lane python`.
    """

    from ..builtins import build_default_registry

    workspace_path = Path(workspace).resolve()
    runner = OperationRunner(
        build_default_registry(workspace_path),
        audit=AuditLogger(workspace_path / ".omni" / "audit" / "events.jsonl"),
        receipts=OperationReceiptStore(
            workspace_path / ".omni" / "operation-receipts.sqlite3"
        ),
    )
    return BuiltinAdapter(runner, worker_id=worker_id)
