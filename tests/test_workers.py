"""Tests for the Artifact contract + BuiltinAdapter (Φ1-T3)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.queue import Task, TaskQueue
from omni_hub.workers import (
    Artifact,
    BuiltinAdapter,
    WorkerError,
    new_artifact_id,
)
from omni_hub.workers.builtin import make_builtin_adapter


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


class QueueWorkerIntegrationTests(unittest.TestCase):
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
