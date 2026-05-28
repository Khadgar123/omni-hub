"""Tests for the SQLite-backed AgentJob queue (Φ1-T2)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.cli import main
from omni_hub.queue import (
    CLAIMED,
    DEAD,
    DONE,
    LeaseLost,
    PENDING,
    TaskQueue,
    _now_ms,
)


def _run_cli(workspace: Path, argv: list[str]) -> dict:
    buffer = StringIO()
    original = Path(__file__).resolve().parents[1]      # repo root, always safe
    try:
        os.chdir(workspace)
        with redirect_stdout(buffer):
            exit_code = main(argv)
    finally:
        os.chdir(original)
    payload = json.loads(buffer.getvalue())
    payload["__exit"] = exit_code
    return payload


class TaskQueueTests(unittest.TestCase):
    def test_enqueue_returns_pending_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            q = TaskQueue(tmp)
            task = q.enqueue(lane="python", packet={"x": 1})
            self.assertEqual(task.state, PENDING)
            self.assertEqual(task.lane, "python")
            self.assertEqual(task.packet, {"x": 1})
            self.assertEqual(task.attempts, 0)
            self.assertTrue(task.idempotency_key)

    def test_idempotency_key_collision_returns_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            q = TaskQueue(tmp)
            a = q.enqueue(lane="python", packet={"v": 1}, idempotency_key="dup")
            b = q.enqueue(lane="python", packet={"v": 2}, idempotency_key="dup")
            self.assertEqual(a.id, b.id)
            # second enqueue must not overwrite payload
            self.assertEqual(b.packet, {"v": 1})

    def test_claim_transitions_state_and_increments_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            q = TaskQueue(tmp)
            q.enqueue(lane="python", packet={"a": 1})
            claimed = q.claim(lane="python", claimed_by="w1")
            assert claimed is not None
            self.assertEqual(claimed.state, CLAIMED)
            self.assertEqual(claimed.attempts, 1)
            self.assertEqual(claimed.claimed_by, "w1")

    def test_claim_other_lane_does_not_pick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            q = TaskQueue(tmp)
            q.enqueue(lane="claude", packet={})
            self.assertIsNone(q.claim(lane="codex"))
            self.assertIsNotNone(q.claim(lane="claude"))

    def test_two_workers_never_double_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            q = TaskQueue(tmp)
            for _ in range(20):
                q.enqueue(lane="python", packet={})

            seen: list[int] = []
            lock = threading.Lock()

            def drain():
                worker_q = TaskQueue(tmp, create=False)
                for _ in range(15):
                    t = worker_q.claim(lane="python", claimed_by="w")
                    if t is None:
                        break
                    with lock:
                        seen.append(t.id)

            t1 = threading.Thread(target=drain)
            t2 = threading.Thread(target=drain)
            t1.start(); t2.start(); t1.join(); t2.join()

            # No id may appear twice across workers.
            self.assertEqual(len(seen), len(set(seen)))
            self.assertEqual(len(seen), 20)

    def test_visibility_timeout_reclaims_stale_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            q = TaskQueue(tmp)
            q.enqueue(lane="python", packet={})
            first = q.claim(lane="python", claimed_by="w1")
            assert first is not None
            # Immediately, no second claim is possible.
            self.assertIsNone(q.claim(lane="python", claimed_by="w2"))
            # Force-rewind claimed_at by 1 hour by direct UPDATE, then a
            # 1-second visibility timeout should make the task reclaimable.
            with q._connect() as conn:
                conn.execute(
                    "UPDATE tasks SET claimed_at = ? WHERE id = ?",
                    (_now_ms() - 3_600_000, first.id),
                )
                conn.commit()
            reclaimed = q.claim(
                lane="python", claimed_by="w2", visibility_timeout_sec=1,
            )
            assert reclaimed is not None
            self.assertEqual(reclaimed.id, first.id)
            self.assertEqual(reclaimed.attempts, 2)

    def test_complete_marks_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            q = TaskQueue(tmp)
            q.enqueue(lane="python", packet={})
            claimed = q.claim(lane="python")
            assert claimed is not None
            done = q.complete(claimed.id, output={"answer": 42})
            self.assertEqual(done.state, DONE)
            self.assertEqual(done.output, {"answer": 42})

    def test_fail_reschedules_until_max_then_dead(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            q = TaskQueue(tmp)
            q.enqueue(lane="python", packet={}, max_attempts=2)

            first = q.claim(lane="python")
            assert first is not None
            after_first_fail = q.fail(first.id, error="boom", backoff_base_sec=0)
            self.assertEqual(after_first_fail.state, PENDING)
            self.assertEqual(after_first_fail.last_error, "boom")

            second = q.claim(lane="python")
            assert second is not None
            after_second_fail = q.fail(second.id, error="boom2", backoff_base_sec=0)
            self.assertEqual(after_second_fail.state, DEAD)
            self.assertEqual(after_second_fail.last_error, "boom2")

    def test_list_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            q = TaskQueue(tmp)
            q.enqueue(lane="python", packet={})
            q.enqueue(lane="claude", packet={})
            self.assertEqual(len(q.list(lane="python")), 1)
            self.assertEqual(len(q.list(lane="claude")), 1)
            self.assertEqual(len(q.list()), 2)
            self.assertEqual(q.counts_by_state()["pending"], 2)


class LeaseFencingTests(unittest.TestCase):
    """Regression for F3 — a stale worker MUST NOT overwrite a successor's progress."""

    def test_stale_worker_cannot_complete_after_reclaim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            q = TaskQueue(tmp)
            q.enqueue(lane="claude", packet={"goal": "x"})

            w1 = q.claim(lane="claude", claimed_by="w1")
            assert w1 is not None

            # Force-rewind claimed_at so w2 can reclaim with a 1s timeout.
            with q._connect() as conn:
                conn.execute(
                    "UPDATE tasks SET claimed_at = ? WHERE id = ?",
                    (_now_ms() - 3_600_000, w1.id),
                )
                conn.commit()

            w2 = q.claim(
                lane="claude", claimed_by="w2", visibility_timeout_sec=1,
            )
            assert w2 is not None
            self.assertEqual(w2.id, w1.id)

            # The stale w1 must not be allowed to finish the task.
            with self.assertRaises(LeaseLost):
                q.complete(w1.id, output={"text": "stale"}, claimed_by="w1")

            # And the task remains claimed by w2 (still in flight).
            current = q.get(w1.id)
            self.assertEqual(current.state, CLAIMED)
            self.assertEqual(current.claimed_by, "w2")

    def test_stale_worker_cannot_fail_after_reclaim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            q = TaskQueue(tmp)
            q.enqueue(lane="claude", packet={"goal": "x"})

            w1 = q.claim(lane="claude", claimed_by="w1")
            assert w1 is not None
            with q._connect() as conn:
                conn.execute(
                    "UPDATE tasks SET claimed_at = ? WHERE id = ?",
                    (_now_ms() - 3_600_000, w1.id),
                )
                conn.commit()
            q.claim(lane="claude", claimed_by="w2", visibility_timeout_sec=1)

            with self.assertRaises(LeaseLost):
                q.fail(w1.id, error="stale", claimed_by="w1")

    def test_holder_can_still_complete(self) -> None:
        """Sanity: fencing does not block the legitimate holder."""
        with tempfile.TemporaryDirectory() as tmp:
            q = TaskQueue(tmp)
            q.enqueue(lane="python", packet={})
            t = q.claim(lane="python", claimed_by="w1")
            assert t is not None
            done = q.complete(t.id, output={"ok": True}, claimed_by="w1")
            self.assertEqual(done.state, DONE)

    def test_unfenced_callers_still_work_for_backward_compat(self) -> None:
        """Legacy callers that pass no claimed_by behave as before."""
        with tempfile.TemporaryDirectory() as tmp:
            q = TaskQueue(tmp)
            q.enqueue(lane="python", packet={})
            t = q.claim(lane="python", claimed_by="w1")
            assert t is not None
            done = q.complete(t.id, output={"legacy": True})
            self.assertEqual(done.state, DONE)


class TaskCliTests(unittest.TestCase):
    def test_enqueue_claim_complete_flow_via_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            enq = _run_cli(workspace, [
                "task-enqueue", "--lane", "python",
                "--packet-json", '{"goal":"smoke"}',
                "--idempotency-key", "smoke-1",
            ])
            self.assertEqual(enq["status"], "succeeded")
            task_id = enq["output"]["id"]

            claim = _run_cli(workspace, ["task-claim", "--lane", "python"])
            self.assertEqual(claim["status"], "succeeded")
            self.assertEqual(claim["output"]["task"]["id"], task_id)

            done = _run_cli(workspace, [
                "task-complete", "--id", str(task_id),
                "--output-json", '{"ok":true}',
            ])
            self.assertEqual(done["status"], "succeeded")
            self.assertEqual(done["output"]["state"], "done")

            listed = _run_cli(workspace, ["task-list", "--state", "done"])
            self.assertEqual(listed["output"]["count"], 1)

    def test_idempotent_enqueue_via_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            a = _run_cli(workspace, [
                "task-enqueue", "--lane", "claude",
                "--packet-json", '{"goal":"a"}', "--idempotency-key", "k1",
            ])
            b = _run_cli(workspace, [
                "task-enqueue", "--lane", "claude",
                "--packet-json", '{"goal":"b"}', "--idempotency-key", "k1",
            ])
            self.assertEqual(a["output"]["id"], b["output"]["id"])

    def test_claim_empty_lane_returns_null(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            res = _run_cli(Path(tmp), ["task-claim", "--lane", "codex"])
            self.assertEqual(res["status"], "succeeded")
            self.assertIsNone(res["output"]["task"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
