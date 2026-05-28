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

from ..models import OperationSpec, OperationStatus, RiskLevel
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
        risk_value = packet.get("risk_level", RiskLevel.LOCAL_WRITE.code)
        spec = OperationSpec(
            name=str(operation),
            action=str(packet.get("action", "run")),
            connector=str(packet.get("connector", "local")),
            payload=payload,
            risk_level=RiskLevel.parse(risk_value),
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

    runner = OperationRunner(build_default_registry(workspace))
    return BuiltinAdapter(runner, worker_id=worker_id)
