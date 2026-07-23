"""Regression for the app_report_build --narrate path.

The narrate branch enqueues a claude-lane task and previously did
``narrative_task_id = task.task_id`` — but Task has only ``.id`` (an int),
so every ``omni-hub app-report-build --narrate`` raised AttributeError.
The non-narrate path never touches it, which is why it went unnoticed.

These tests run the operation handler against a temp workspace and assert:
  * narrate=True returns a non-empty string ``narrative_task_id``;
  * a real task landed on the ``claude`` lane carrying the report packet
    and the trace_id (HR #4);
  * narrate=False enqueues nothing and omits the field.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omni_hub.builtins import make_app_report_build
from omni_hub.models import OperationSpec
from omni_hub.queue import TaskQueue


class AppReportNarrateTests(unittest.TestCase):
    def _spec(self, **payload: object) -> OperationSpec:
        return OperationSpec(
            name="app_report_build",
            action="app_report_build",
            payload=dict(payload),
            trace_id="trace-report-xyz",
        )

    def test_narrate_returns_task_id_and_enqueues_claude_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_app_report_build(Path(tmp))
            out = handler(self._spec(period="daily", narrate=True))

            # The regression: this used to raise AttributeError on task.task_id.
            task_id = out.get("narrative_task_id", "")
            self.assertTrue(task_id, "narrate must return a non-empty narrative_task_id")
            self.assertIsInstance(task_id, str)

            # A real task landed on the claude lane, with packet + trace_id.
            claimed = TaskQueue(tmp).claim(lane="claude", claimed_by="test")
            self.assertIsNotNone(claimed, "a claude-lane task must be enqueued")
            assert claimed is not None  # narrow for type-checkers
            self.assertEqual(str(claimed.id), task_id)
            self.assertEqual(claimed.packet.get("task_type"), "report_narrate")
            self.assertEqual(claimed.trace_id, "trace-report-xyz")

    def test_non_narrate_enqueues_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_app_report_build(Path(tmp))
            out = handler(self._spec(period="daily", narrate=False))
            # summary.to_dict() always carries the field; non-narrate leaves it empty.
            self.assertFalse(out.get("narrative_task_id"), "non-narrate must not produce a task id")
            self.assertIsNone(TaskQueue(tmp).claim(lane="claude", claimed_by="test"))


if __name__ == "__main__":
    unittest.main()
