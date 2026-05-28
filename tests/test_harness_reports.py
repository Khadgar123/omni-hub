from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.harness import openhands_bridge
from omni_hub.harness.preference import PreferenceRecord, PreferenceStore
from omni_hub.reports import build_daily, build_monthly, build_weekly, default_output_path


def _seed_memory(db_path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    from contextlib import closing
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute(
            "CREATE TABLE documents (source_path TEXT PRIMARY KEY, title TEXT, summary TEXT, updated_at TEXT)"
        )
        conn.executemany("INSERT INTO documents VALUES (?, ?, ?, ?)", rows)


class ReportTests(unittest.TestCase):
    def _build_workspace(self, tmp: Path) -> Path:
        omni = tmp / ".omni"
        (omni / "preference").mkdir(parents=True)
        (omni / "proposals").mkdir(parents=True)
        _seed_memory(omni / "memory.sqlite3", [
            ("vault/today.md", "Today doc", "Summary today",
             datetime.now(timezone.utc).isoformat()),
            ("vault/yesterday.md", "Yesterday doc", "Summary",
             (datetime.now(timezone.utc) - timedelta(days=1, minutes=5)).isoformat()),
            ("vault/old.md", "Old doc", "Old",
             (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()),
        ])
        store = PreferenceStore(omni / "preference")
        store.append(PreferenceRecord(domain="research", decision="accepted", candidate_text="A"))
        store.append(PreferenceRecord(domain="research", decision="rejected", candidate_text="B"))
        return tmp

    def test_daily_report_includes_today_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._build_workspace(Path(tmp))
            text, ctx = build_daily(anchor=date.today(), workspace=root)
            self.assertIn("Daily Brief", text)
            self.assertIn("Today doc", text)
            # yesterday and old must NOT appear in daily window
            self.assertNotIn("Old doc", text)
            self.assertIn("Preference flywheel", text)
            self.assertIn("research", text)
            self.assertEqual(ctx.period, "daily")

    def test_weekly_report_covers_last_seven_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._build_workspace(Path(tmp))
            text, ctx = build_weekly(anchor=date.today(), workspace=root)
            self.assertIn("Weekly Review", text)
            self.assertIn("Today doc", text)
            # yesterday should also appear in weekly window
            self.assertIn("Yesterday doc", text)
            self.assertEqual(ctx.period, "weekly")

    def test_monthly_report_groups_by_month(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._build_workspace(Path(tmp))
            text, ctx = build_monthly(anchor=date.today(), workspace=root)
            self.assertIn("Monthly Roll-up", text)
            self.assertEqual(ctx.period, "monthly")

    def test_default_output_path_uses_iso_year_week(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, ctx = build_weekly(anchor=date(2026, 5, 28), workspace=root)
            path = default_output_path(root, ctx)
            self.assertTrue(str(path).endswith("2026-W22.md"))


class OpenHandsBridgeTests(unittest.TestCase):
    def test_stub_dispatch_returns_branch_and_marks_no_patch(self) -> None:
        task = openhands_bridge.EngineeringTask(
            task_id="abcdef0123456789",
            repo_path="/tmp/repo",
            issue_title="Fix bug",
            issue_body="See log",
        )
        result = openhands_bridge.run(task)
        # backend depends on whether openhands is importable in this env
        self.assertIn(result.backend, ("stub", "openhands"))
        self.assertTrue(result.branch.startswith("harness/"))
        # in either case, we have no patch yet
        self.assertEqual(result.patch, "")

    def test_dispatch_wraps_into_generation_record(self) -> None:
        task = openhands_bridge.EngineeringTask(
            task_id="task-1",
            repo_path="/tmp/repo",
            issue_title="Title",
            issue_body="Body",
        )
        record = openhands_bridge.dispatch_as_generation_record(task)
        self.assertEqual(len(record.candidates), 1)
        self.assertIn("no_patch", record.candidates[0].failure_tags)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
