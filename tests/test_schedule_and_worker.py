"""Tests for schedule-tick + worker CLI + launchd renderer (Φ1-T5)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from omni_hub.cli import main
from omni_hub.queue import TaskQueue


def _run_cli(workspace: Path, argv: list[str]) -> dict:
    buffer = StringIO()
    original = REPO_ROOT
    try:
        os.chdir(workspace)
        with redirect_stdout(buffer):
            exit_code = main(argv)
    finally:
        os.chdir(original)
    payload = json.loads(buffer.getvalue())
    payload["__exit"] = exit_code
    return payload


class ScheduleTickTests(unittest.TestCase):
    def test_daily_tick_enqueues_expected_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            tick = _run_cli(workspace, [
                "schedule-tick", "--period", "daily", "--anchor", "2026-05-28",
            ])
            self.assertEqual(tick["status"], "succeeded")
            self.assertEqual(tick["output"]["period"], "daily")
            self.assertEqual(len(tick["output"]["enqueued"]), 2)

            queue = TaskQueue(workspace, create=False)
            self.assertEqual(queue.counts_by_state()["pending"], 2)

    def test_daily_tick_is_idempotent_for_same_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _run_cli(workspace, [
                "schedule-tick", "--period", "daily", "--anchor", "2026-05-28",
            ])
            _run_cli(workspace, [
                "schedule-tick", "--period", "daily", "--anchor", "2026-05-28",
            ])
            queue = TaskQueue(workspace, create=False)
            # Same idempotency key → no duplicates.
            self.assertEqual(queue.counts_by_state()["pending"], 2)

    def test_weekly_and_monthly_emit_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            w = _run_cli(workspace, [
                "schedule-tick", "--period", "weekly", "--anchor", "2026-05-25",
            ])
            m = _run_cli(workspace, [
                "schedule-tick", "--period", "monthly", "--anchor", "2026-05-01",
            ])
            self.assertGreaterEqual(len(w["output"]["enqueued"]), 1)
            self.assertGreaterEqual(len(m["output"]["enqueued"]), 1)


class WorkerLoopTests(unittest.TestCase):
    def test_worker_drains_python_lane_and_exits_when_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _run_cli(workspace, [
                "schedule-tick", "--period", "daily", "--anchor", "2026-05-28",
            ])
            # Idle exit very fast so the test is hermetic.
            result = _run_cli(workspace, [
                "worker", "--lane", "python",
                "--idle-exit-after-sec", "1",
                "--poll-interval-sec", "0.05",
            ])
            # Worker prints its own summary, not the OperationResult wrapper:
            # it is a daemon-style command, not a single audited op.
            self.assertEqual(result["__exit"], 0)
            self.assertGreaterEqual(result["processed"], 1)
            self.assertEqual(
                result["counts_by_state"].get("pending", 0), 0,
            )


class LaunchdRendererTests(unittest.TestCase):
    def test_dry_run_renders_valid_xml_with_substitutions(self) -> None:
        import install_launchd                                # type: ignore

        rendered = install_launchd.render(
            "omni-hub.daily",
            workspace=Path("/tmp/example"),
            python_bin="/usr/bin/python3.12",
        )
        # Must parse as valid XML
        root = ET.fromstring(rendered)
        self.assertEqual(root.tag, "plist")
        # No leftover placeholders
        self.assertNotIn("{{", rendered)
        self.assertNotIn("}}", rendered)
        # Critical config keys present
        self.assertIn("StartCalendarInterval", rendered)
        self.assertIn("WakeSystem", rendered)
        self.assertIn("/tmp/example", rendered)
        self.assertIn("/usr/bin/python3.12", rendered)

    def test_worker_plist_uses_keepalive(self) -> None:
        import install_launchd                                # type: ignore

        rendered = install_launchd.render(
            "omni-hub.worker", workspace=Path("/tmp/example"),
            python_bin="/usr/bin/python3.12",
        )
        ET.fromstring(rendered)                               # validates XML
        self.assertIn("KeepAlive", rendered)
        self.assertIn("ThrottleInterval", rendered)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
