"""Tests for schedule-tick + worker CLI + launchd renderer (Φ1-T5)."""

from __future__ import annotations

import json
import os
import subprocess
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

from omni_hub.queue import TaskQueue
from omni_hub.testing import cli_runner as _run_cli


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
    def test_daily_tick_then_worker_produces_real_report_file(self) -> None:
        """F4 regression: schedule-tick enqueues build_daily_report (not memory_stats),
        and after a worker pass the markdown file must exist on disk."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _run_cli(workspace, [
                "schedule-tick", "--period", "daily", "--anchor", "2026-05-28",
            ])

            queue = TaskQueue(workspace, create=False)
            ops = {t.packet["operation"] for t in queue.list()}
            self.assertIn("build_daily_report", ops)
            self.assertNotIn("memory_stats", ops)            # the old placeholder

            _run_cli(workspace, [
                "worker", "--lane", "python",
                "--idle-exit-after-sec", "1",
                "--poll-interval-sec", "0.05",
            ])

            report_path = workspace / "vault" / "40_Reports" / "daily" / "2026-05-28.md"
            self.assertTrue(report_path.exists())
            body = report_path.read_text(encoding="utf-8")
            self.assertIn("Daily Brief", body)
            self.assertIn("Preference flywheel", body)

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


class LaunchdPythonGuardTests(unittest.TestCase):
    """Regression for F1: the installer must refuse Python < 3.12 and must
    bake the *caller's* interpreter into the plist (not a stale `python3`
    that happens to be earlier on PATH)."""

    def test_check_python_rejects_old_python(self) -> None:
        import install_launchd                                # type: ignore

        # Use the system /usr/bin/python3 (or conda anaconda3/bin/python3)
        # which on this machine is 3.9.  If the user runs the tests on a
        # box where every python3 is >= 3.12 the test will still pass via
        # a SystemExit when we point at /usr/bin/false-ish path below.
        old = "/Users/hzh/opt/anaconda3/bin/python3"
        if not Path(old).exists():
            self.skipTest("no Python 3.9 interpreter available for negative test")
        with self.assertRaises(SystemExit) as cm:
            install_launchd._check_python(old)
        self.assertIn("3.12", str(cm.exception))

    def test_check_python_accepts_current_interpreter(self) -> None:
        import install_launchd                                # type: ignore

        # The interpreter running this test IS the one we want plists to
        # use, so _check_python must accept it without raising.
        install_launchd._check_python(sys.executable)

    def test_render_picks_explicit_python_path(self) -> None:
        import install_launchd                                # type: ignore

        rendered = install_launchd.render(
            "omni-hub.daily",
            workspace=Path("/tmp/example"),
            python_bin="/opt/python/3.12.13/bin/python3",
        )
        self.assertIn("/opt/python/3.12.13/bin/python3", rendered)
        # No stale fallback should leak in.
        self.assertNotIn("/usr/bin/python3", rendered)

    def test_cli_default_is_sys_executable_not_which(self) -> None:
        """Without --python, the script must bake in the running interpreter."""

        import install_launchd                                # type: ignore
        # Replicate the argparse default — confirm it's sys.executable
        # and *not* shutil.which("python3").
        parser = install_launchd                              # module
        # Run main() with --dry-run and no --python; capture rendered output.
        buf = StringIO()
        original_stdout = sys.stdout
        sys.stdout = buf
        try:
            install_launchd.main(["--dry-run", "--only", "worker"])
        finally:
            sys.stdout = original_stdout
        self.assertIn(sys.executable, buf.getvalue())

    def test_make_check_python_rejects_old_python(self) -> None:
        """The real Makefile guard must fail closed for Python < 3.12."""

        old = "/Users/hzh/opt/anaconda3/bin/python3"
        if not Path(old).exists():
            self.skipTest("no Python 3.9 interpreter available for negative test")
        result = subprocess.run(
            ["make", "check-python", f"PYTHON={old}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("need >= 3.12", result.stderr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
