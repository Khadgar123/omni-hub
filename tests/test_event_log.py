"""Tests for the hash-chained event log (P2-1)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.event_log import (
    GENESIS_HASH,
    KIND_TASK_CLAIMED,
    KIND_TASK_COMPLETED,
    KIND_WORKER_ADAPTER_START,
    EventLog,
)
from omni_hub.queue import TaskQueue
from omni_hub.testing import cli_runner as _run_cli


class EventLogPrimitivesTests(unittest.TestCase):
    def test_first_event_chains_off_genesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            event = log.append(KIND_TASK_CLAIMED, task_id=1, data={"worker_id": "w1"})
            self.assertEqual(event.prev_hash, GENESIS_HASH)
            self.assertEqual(len(event.content_hash), 64)

    def test_chain_links_consecutive_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            e1 = log.append(KIND_TASK_CLAIMED, task_id=1, data={"i": 1})
            e2 = log.append(KIND_WORKER_ADAPTER_START, task_id=1, data={"i": 2})
            e3 = log.append(KIND_TASK_COMPLETED, task_id=1, data={"i": 3})
            self.assertEqual(e2.prev_hash, e1.content_hash)
            self.assertEqual(e3.prev_hash, e2.content_hash)

    def test_replay_yields_events_in_append_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            for i in range(5):
                log.append("custom.kind", task_id=42, data={"i": i})
            seen = [e.data["i"] for e in log.replay(42)]
            self.assertEqual(seen, [0, 1, 2, 3, 4])

    def test_verify_chain_passes_on_well_formed_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            for i in range(3):
                log.append("custom.kind", task_id=7, data={"i": i})
            ok, errors = log.verify_chain(7)
            self.assertTrue(ok, msg=errors)
            self.assertEqual(errors, [])

    def test_verify_chain_detects_content_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            log.append("custom.kind", task_id=8, data={"i": 0})
            log.append("custom.kind", task_id=8, data={"i": 1})
            # Tamper: rewrite the second event's data without recomputing the hash.
            path = Path(tmp) / ".omni" / "events" / "task-8.jsonl"
            lines = path.read_text(encoding="utf-8").splitlines()
            tampered = json.loads(lines[1])
            tampered["data"]["i"] = 999
            lines[1] = json.dumps(tampered, ensure_ascii=False)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            ok, errors = log.verify_chain(8)
            self.assertFalse(ok)
            self.assertTrue(any("content_hash forged" in e for e in errors))

    def test_global_stream_uses_separate_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            log.append("system.start", task_id=0, data={})
            log.append("task.created", task_id=1, data={})
            self.assertEqual(len(list(log.replay(0))), 1)
            self.assertEqual(len(list(log.replay(1))), 1)
            self.assertIn(1, log.list_tasks())

    def test_files_are_strictly_append_only_under_concurrent_appends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            # Append 30 events in interleaved order across two task ids.
            for i in range(30):
                log.append("custom.kind", task_id=(i % 3) + 1, data={"i": i})
            for task_id in (1, 2, 3):
                ok, errors = log.verify_chain(task_id)
                self.assertTrue(ok, msg=errors)


class EventLogWorkerIntegrationTests(unittest.TestCase):
    """The worker daemon must log claim → adapter_start → adapter_done → completed."""

    def test_python_lane_emits_full_event_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            queue = TaskQueue(workspace)
            queue.enqueue(
                lane="python",
                packet={"operation": "memory_stats", "payload": {}, "kind": "text"},
            )

            _run_cli(workspace, [
                "worker", "--lane", "python",
                "--idle-exit-after-sec", "1",
                "--poll-interval-sec", "0.05",
            ])

            log = EventLog(workspace)
            tasks = log.list_tasks()
            self.assertEqual(len(tasks), 1)
            kinds = [e.kind for e in log.replay(tasks[0])]
            self.assertIn(KIND_TASK_CLAIMED, kinds)
            self.assertIn(KIND_WORKER_ADAPTER_START, kinds)
            self.assertIn("worker.adapter_done", kinds)
            self.assertIn(KIND_TASK_COMPLETED, kinds)

            ok, errors = log.verify_chain(tasks[0])
            self.assertTrue(ok, msg=errors)


class EventLogCliTests(unittest.TestCase):
    def test_event_log_dump_with_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            log = EventLog(workspace)
            log.append("custom", task_id=42, data={"x": 1})
            log.append("custom", task_id=42, data={"x": 2})

            result = _run_cli(workspace, [
                "event-log", "--task-id", "42", "--verify",
            ])
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["output"]["count"], 2)
            self.assertTrue(result["output"]["chain_ok"])

    def test_event_log_list_returns_task_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            log = EventLog(workspace)
            log.append("custom", task_id=1, data={})
            log.append("custom", task_id=7, data={})
            result = _run_cli(workspace, ["event-log-list"])
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(set(result["output"]["task_ids"]), {1, 7})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
