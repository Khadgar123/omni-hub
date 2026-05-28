"""Tests for the shared storage primitives (P1-1).

`_storage.safe_workspace_path` + `connect_sqlite_store` replace the 6
duplicated `_safe_path` helpers and 3 duplicated `_connect` helpers that
used to live across MemoryStore / ProposalStore / TaskQueue / VaultReader /
SkillRegistry / ContentStore.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub._storage import connect_sqlite_store, safe_workspace_path


class SafeWorkspacePathTests(unittest.TestCase):
    def test_returns_absolute_path_under_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = safe_workspace_path(tmp, "subdir/file.txt")
            self.assertTrue(target.is_absolute())
            self.assertTrue(str(target).startswith(str(Path(tmp).resolve())))
            self.assertTrue(str(target).endswith("subdir/file.txt"))

    def test_rejects_traversal_via_double_dot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PermissionError):
                safe_workspace_path(tmp, "../outside.txt")

    def test_rejects_absolute_path_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PermissionError):
                # Absolute path that isn't a child of workspace.
                safe_workspace_path(tmp, "/etc/passwd")

    def test_accepts_subpath_with_normalisation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = safe_workspace_path(tmp, "a/./b/../c.txt")
            # Should normalise to <tmp>/a/c.txt
            self.assertTrue(str(target).endswith("a/c.txt"))


class ConnectSqliteStoreTests(unittest.TestCase):
    def test_default_applies_wal_and_busy_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "x.sqlite3"
            with connect_sqlite_store(db) as conn:
                journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
                self.assertEqual(journal_mode.lower(), "wal")
                self.assertEqual(int(busy_timeout), 30000)

    def test_row_factory_is_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "x.sqlite3"
            with connect_sqlite_store(db) as conn:
                conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
                conn.execute("INSERT INTO t VALUES (1, 'a')")
                row = conn.execute("SELECT * FROM t").fetchone()
                # sqlite3.Row supports keyed access
                self.assertEqual(row["id"], 1)
                self.assertEqual(row["name"], "a")

    def test_can_disable_wal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "x.sqlite3"
            with connect_sqlite_store(db, wal=False) as conn:
                # Should be the default (delete) when wal=False
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                self.assertNotEqual(mode.lower(), "wal")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
