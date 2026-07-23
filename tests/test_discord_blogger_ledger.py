from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from omni_hub.discord_blogger_ledger import BloggerLedger
from omni_hub.models import ConcurrentModificationError
from omni_hub.queue import LeaseLost, TaskQueue, _now_ms


class BloggerLedgerTests(unittest.TestCase):
    def _claimed(self, root: Path, worker: str = "worker-a"):
        queue = TaskQueue(root)
        queued = queue.enqueue(
            lane="python",
            packet={"operation": "classify", "payload": {}},
            idempotency_key="task-1",
        )
        task = queue.claim(lane="python", claimed_by=worker, visibility_timeout_sec=60)
        assert task is not None
        self.assertEqual(task.id, queued.id)
        return queue, task

    def test_crash_replay_and_duplicate_revision_are_noops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue, task = self._claimed(Path(tmp))
            ledger = BloggerLedger(queue)
            attempt_a = ledger.begin_attempt(
                task_id=str(task.id), claimed_by="worker-a", lease_epoch=task.lease_epoch
            )
            attempt_b = ledger.begin_attempt(
                task_id=str(task.id), claimed_by="worker-a", lease_epoch=task.lease_epoch
            )
            self.assertEqual(attempt_a, attempt_b)

            revision_a = ledger.commit_message_revision(
                message_id="m-1",
                revision={"decision": "signal", "schema": 1},
                expected_revision=None,
                task_id=str(task.id),
                claimed_by="worker-a",
                lease_epoch=task.lease_epoch,
            )
            revision_b = ledger.commit_message_revision(
                message_id="m-1",
                revision={"schema": 1, "decision": "signal"},
                expected_revision=None,
                task_id=str(task.id),
                claimed_by="worker-a",
                lease_epoch=task.lease_epoch,
            )
            self.assertEqual(revision_a, revision_b)
            rows = list(ledger.current_rows("message"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["revision_id"], revision_a)

    def test_commit_then_ack_crash_replays_as_noop_under_new_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue, first_task = self._claimed(root)
            ledger = BloggerLedger(queue)
            ledger.begin_attempt(
                task_id=str(first_task.id),
                claimed_by="worker-a",
                lease_epoch=first_task.lease_epoch,
            )
            first_revision = ledger.commit_message_revision(
                message_id="m-ack-crash",
                revision={"decision": "signal"},
                expected_revision=None,
                task_id=str(first_task.id),
                claimed_by="worker-a",
                lease_epoch=first_task.lease_epoch,
            )
            # Simulate crash before queue.complete(), then successor reclaim.
            with queue._connect() as conn:
                conn.execute(
                    "UPDATE tasks SET claimed_at = ?, lease_deadline = ? WHERE id = ?",
                    (_now_ms() - 120_000, _now_ms() - 1, first_task.id),
                )
                conn.commit()
            second_task = queue.claim(
                lane="python", claimed_by="worker-b", visibility_timeout_sec=60
            )
            assert second_task is not None
            ledger.begin_attempt(
                task_id=str(second_task.id),
                claimed_by="worker-b",
                lease_epoch=second_task.lease_epoch,
            )
            replay_revision = ledger.commit_message_revision(
                message_id="m-ack-crash",
                revision={"decision": "signal"},
                expected_revision=None,
                task_id=str(second_task.id),
                claimed_by="worker-b",
                lease_epoch=second_task.lease_epoch,
            )
            self.assertEqual(replay_revision, first_revision)
            self.assertEqual(len(list(ledger.current_rows("message"))), 1)

    def test_predecessor_compare_and_swap_and_unique_current_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue, task = self._claimed(Path(tmp))
            ledger = BloggerLedger(queue)
            ledger.begin_attempt(
                task_id=str(task.id), claimed_by="worker-a", lease_epoch=task.lease_epoch
            )
            first = ledger.commit_event_revision(
                event_id="event-1",
                revision={"side": "long"},
                expected_revision=None,
                task_id=str(task.id),
                claimed_by="worker-a",
                lease_epoch=task.lease_epoch,
            )
            with self.assertRaises(ConcurrentModificationError):
                ledger.commit_event_revision(
                    event_id="event-1",
                    revision={"side": "short"},
                    expected_revision=None,
                    task_id=str(task.id),
                    claimed_by="worker-a",
                    lease_epoch=task.lease_epoch,
                )
            second = ledger.commit_event_revision(
                event_id="event-1",
                revision={"side": "short"},
                expected_revision=first,
                task_id=str(task.id),
                claimed_by="worker-a",
                lease_epoch=task.lease_epoch,
            )
            self.assertNotEqual(first, second)
            with queue._connect() as conn:
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO blogger_current_revisions "
                        "(entity_kind, entity_id, revision_id) VALUES (?, ?, ?)",
                        ("event", "event-1", first),
                    )
            rows = list(ledger.current_rows("event"))
            self.assertEqual([row["revision_id"] for row in rows], [second])

    def test_expired_or_replaced_lease_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue, task = self._claimed(Path(tmp))
            ledger = BloggerLedger(queue)
            with queue._connect() as conn:
                conn.execute(
                    "UPDATE tasks SET lease_deadline = ? WHERE id = ?",
                    (_now_ms() - 1, task.id),
                )
                conn.commit()
            with self.assertRaises(LeaseLost):
                ledger.begin_attempt(
                    task_id=str(task.id),
                    claimed_by="worker-a",
                    lease_epoch=task.lease_epoch,
                )

    def test_lock_wait_crossing_deadline_rejects_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = TaskQueue(root)
            queue.enqueue(lane="python", packet={})
            task = queue.claim(
                lane="python", claimed_by="worker-a", visibility_timeout_sec=1
            )
            assert task is not None and task.lease_deadline is not None
            ledger = BloggerLedger(queue)
            blocker = queue._connect()
            blocker.execute("BEGIN IMMEDIATE")
            outcome: list[BaseException | str] = []
            started = threading.Event()

            def attempt_after_lock() -> None:
                started.set()
                try:
                    ledger.begin_attempt(
                        task_id=str(task.id),
                        claimed_by="worker-a",
                        lease_epoch=task.lease_epoch,
                    )
                    outcome.append("committed")
                except BaseException as exc:  # captured for the main test thread
                    outcome.append(exc)

            thread = threading.Thread(target=attempt_after_lock)
            thread.start()
            self.assertTrue(started.wait(timeout=1))
            remaining = max(0.0, (task.lease_deadline - _now_ms()) / 1000)
            time.sleep(remaining + 0.1)
            blocker.commit()
            blocker.close()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(outcome), 1)
            self.assertIsInstance(outcome[0], LeaseLost)

    def test_two_connection_reclaim_race_rejects_a_and_commits_one_b_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_a, task_a = self._claimed(root)
            ledger_a = BloggerLedger(queue_a)
            # A has read and retained epoch 1. Its lease then expires.
            with queue_a._connect() as conn:
                conn.execute(
                    "UPDATE tasks SET claimed_at = ?, lease_deadline = ? WHERE id = ?",
                    (_now_ms() - 120_000, _now_ms() - 1, task_a.id),
                )
                conn.commit()
            queue_b = TaskQueue(root, create=False)
            task_b = queue_b.claim(
                lane="python", claimed_by="worker-b", visibility_timeout_sec=1
            )
            assert task_b is not None
            ledger_b = BloggerLedger(queue_b)

            with self.assertRaises(LeaseLost):
                ledger_a.begin_attempt(
                    task_id=str(task_a.id),
                    claimed_by="worker-a",
                    lease_epoch=task_a.lease_epoch,
                )
            ledger_b.begin_attempt(
                task_id=str(task_b.id),
                claimed_by="worker-b",
                lease_epoch=task_b.lease_epoch,
            )
            ledger_b.replace_lifecycle_revision(
                lifecycle_id="life-1",
                revision={"state": "open"},
                expected_revision=None,
                task_id=str(task_b.id),
                claimed_by="worker-b",
                lease_epoch=task_b.lease_epoch,
            )
            self.assertEqual(len(list(ledger_b.current_rows("lifecycle"))), 1)


if __name__ == "__main__":
    unittest.main()
