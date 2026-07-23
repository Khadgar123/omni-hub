"""Tests for the Artifact contract + BuiltinAdapter (Φ1-T3)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.queue import Task, TaskQueue
from omni_hub.audit import AuditLogger
from omni_hub.builtins import build_default_registry
from omni_hub.registry import OperationRegistry
from omni_hub.runner import OperationRunner
from omni_hub.policy import PolicyConfig, PolicyEngine
from omni_hub.models import OperationSpec, OperationStatus, RiskLevel
from omni_hub.workers import (
    Artifact,
    BuiltinAdapter,
    WorkerError,
    new_artifact_id,
)
from omni_hub.workers.builtin import (
    WORKER_CONTEXT_KEY,
    BuiltinAdapter,
    make_builtin_adapter,
)


class ArtifactRoundTripTests(unittest.TestCase):
    def test_to_dict_then_from_dict(self) -> None:
        original = Artifact(
            kind="report",
            data={"body": "# Daily"},
            task_id=42,
            worker_lane="python",
            worker_id="w1",
            duration_ms=120,
            tokens_in=10,
            tokens_out=20,
            cost_usd=0.0005,
        )
        roundtrip = Artifact.from_dict(original.to_dict())
        self.assertEqual(roundtrip.kind, "report")
        self.assertEqual(roundtrip.task_id, 42)
        self.assertEqual(roundtrip.data, {"body": "# Daily"})
        self.assertEqual(roundtrip.cost_usd, 0.0005)

    def test_artifact_id_is_unique(self) -> None:
        a, b = new_artifact_id(), new_artifact_id()
        self.assertNotEqual(a, b)


class BuiltinAdapterTests(unittest.TestCase):
    def _make_task(self, **packet) -> Task:
        return Task(id=1, lane="python", packet=packet)

    def test_runs_memory_stats_operation_and_returns_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = make_builtin_adapter(tmp)
            task = self._make_task(
                operation="memory_stats",
                payload={},
                kind="text",
                risk_level="L0",
            )
            artifact = adapter.run(task)

            self.assertIsNone(artifact.error)
            self.assertEqual(artifact.worker_lane, "python")
            self.assertEqual(artifact.kind, "text")
            self.assertEqual(artifact.data["documents"], 0)
            self.assertGreaterEqual(artifact.duration_ms, 0)

    def test_runs_redundancy_scan_and_returns_scan_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = make_builtin_adapter(tmp)
            task = self._make_task(
                operation="harness_redundancy_scan",
                payload={
                    "db_path": ".omni/memory.sqlite3",
                    "prefer_backend": "local",
                    "no_write": True,
                },
                kind="scan_result",
            )
            artifact = adapter.run(task)

            self.assertIsNone(artifact.error)
            self.assertEqual(artifact.kind, "scan_result")
            self.assertEqual(artifact.data["documents_scanned"], 0)

    def test_missing_operation_raises_worker_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = make_builtin_adapter(tmp)
            task = self._make_task(payload={})
            with self.assertRaises(WorkerError):
                adapter.run(task)

    def test_failed_operation_surfaces_error_on_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = make_builtin_adapter(tmp)
            # Call digest_proposal with a bogus proposal ref → should fail.
            task = self._make_task(
                operation="digest_proposal",
                payload={"proposal": "definitely-not-a-real-id"},
                kind="generation",
            )
            artifact = adapter.run(task)
            self.assertIsNotNone(artifact.error)
            self.assertEqual(artifact.kind, "generation")

    def test_reserved_execution_context_is_authoritative_and_propagated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = OperationRegistry()
            registry.register("inspect", lambda spec: {
                "payload": spec.payload,
                "trace_id": spec.trace_id,
                "idempotency_key": spec.idempotency_key,
            })
            adapter = BuiltinAdapter(
                OperationRunner(
                    registry, audit=AuditLogger(Path(tmp) / "audit.jsonl")
                ),
                worker_id="worker-7",
            )
            task = Task(
                id=42,
                idempotency_key="packet-key",
                trace_id="trace-42",
                lane="python",
                packet={"operation": "inspect", "payload": {"safe": True}},
                claimed_by="worker-7",
                lease_epoch=3,
            )
            artifact = adapter.run(task)
            context = artifact.data["payload"][WORKER_CONTEXT_KEY]
            self.assertEqual(
                context,
                {
                    "trace_id": "trace-42",
                    "idempotency_key": "packet-key",
                    "task_id": 42,
                    "worker_id": "worker-7",
                    "lease_epoch": 3,
                    "fencing_suffix": "t42:e3",
                },
            )
            self.assertEqual(artifact.data["trace_id"], "trace-42")
            self.assertEqual(artifact.data["idempotency_key"], "packet-key")

    def test_receipt_replays_across_reclaimed_lease_without_rerunning_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            registry = OperationRegistry()

            def inspect(spec):
                calls.append(spec.payload[WORKER_CONTEXT_KEY]["lease_epoch"])
                return {"committed_by_epoch": calls[-1]}

            registry.register("inspect", inspect)
            adapter = BuiltinAdapter(
                OperationRunner(
                    registry, audit=AuditLogger(Path(tmp) / "audit.jsonl")
                ),
                worker_id="worker-7",
            )
            base = {
                "id": 42,
                "idempotency_key": "packet-key",
                "trace_id": "trace-42",
                "lane": "python",
                "packet": {"operation": "inspect", "payload": {"safe": True}},
                "claimed_by": "worker-7",
            }
            first = adapter.run(Task(**base, lease_epoch=3))
            replay = adapter.run(Task(**base, lease_epoch=4))
            self.assertEqual(first.data, replay.data)
            self.assertEqual(calls, [3])

    def test_user_payload_cannot_forge_reserved_execution_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = OperationRegistry()
            registry.register("inspect", lambda spec: {"unexpected": True})
            adapter = BuiltinAdapter(
                OperationRunner(
                    registry, audit=AuditLogger(Path(tmp) / "audit.jsonl")
                ),
                worker_id="worker-7",
            )
            task = Task(
                id=42,
                lane="python",
                packet={
                    "operation": "inspect",
                    "payload": {WORKER_CONTEXT_KEY: {"worker_id": "forged"}},
                },
                claimed_by="worker-7",
                lease_epoch=3,
            )
            with self.assertRaises(WorkerError):
                adapter.run(task)


class QueueWorkerIntegrationTests(unittest.TestCase):
    def test_default_runner_and_worker_share_external_send_receipt_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = {
                "trace_id": "trace-cross-entry",
                "idempotency_key": "cross-entry",
                "task_id": 42,
                "worker_id": "cli-worker",
                "lease_epoch": 1,
                "fencing_suffix": "t42:e1",
            }
            direct = OperationRunner(
                build_default_registry(workspace),
                policy=PolicyEngine(
                    PolicyConfig(external_write_allowlist={"remote:send"})
                ),
                audit=AuditLogger(
                    workspace / ".omni" / "audit" / "events.jsonl"
                ),
            )
            first = direct.run(
                OperationSpec(
                    name="summarize_text",
                    connector="remote",
                    action="send",
                    payload={
                        "text": "hello world",
                        "max_chars": 5,
                        WORKER_CONTEXT_KEY: context,
                    },
                    risk_level=RiskLevel.EXTERNAL_SEND,
                    idempotency_key="cross-entry",
                    trace_id="trace-cross-entry",
                )
            )
            self.assertEqual(first.status, OperationStatus.SUCCEEDED)

            adapter = make_builtin_adapter(workspace, worker_id="worker-2")
            replay = adapter.run(
                Task(
                    id=42,
                    idempotency_key="cross-entry",
                    trace_id="trace-cross-entry",
                    lane="python",
                    packet={
                        "operation": "summarize_text",
                        "connector": "remote",
                        "action": "send",
                        "payload": {"text": "hello world", "max_chars": 5},
                        "risk_level": "L2",
                    },
                    claimed_by="worker-2",
                    lease_epoch=2,
                )
            )
            self.assertIsNone(replay.error)
            self.assertEqual(replay.data, first.output)
            self.assertTrue(
                (workspace / ".omni" / "operation-receipts.sqlite3").is_file()
            )
            self.assertFalse(
                (
                    workspace
                    / ".omni"
                    / "audit"
                    / "operation-receipts.sqlite3"
                ).exists()
            )

    def test_enqueue_claim_run_complete_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            queue = TaskQueue(workspace)
            adapter = make_builtin_adapter(workspace)

            queue.enqueue(
                lane="python",
                packet={"operation": "memory_stats", "payload": {}, "kind": "text"},
                idempotency_key="memstats-1",
            )

            claimed = queue.claim(lane="python", claimed_by="builtin-worker")
            assert claimed is not None
            artifact = adapter.run(claimed)
            self.assertIsNone(artifact.error)

            done = queue.complete(claimed.id, output=artifact.to_dict())
            self.assertEqual(done.state, "done")
            self.assertIn("kind", done.output)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
