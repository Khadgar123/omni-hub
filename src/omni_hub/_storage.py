"""Shared storage primitives — workspace-relative path resolution + SQLite setup.

Six store classes (MemoryStore, ProposalStore, TaskQueue, VaultReader,
SkillRegistry, ContentStore) used to ship identical 7-line ``_safe_path``
helpers, and three of them duplicated ``_connect`` boilerplate.  This
module is the single source of truth for both.

Design rules:
* ``safe_workspace_path`` rejects any target that resolves outside the
  workspace root.  This is the boundary check that keeps user-supplied
  paths from escaping into the rest of the filesystem.
* ``connect_sqlite_store`` defaults to WAL + 30 s busy timeout.  Pragmas
  applied per-connection — reader-only paths must also wait through
  writer locks (the v0.8 P0-3 lesson).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def safe_workspace_path(
    workspace: Path | str,
    relative_path: str,
    *,
    permission_error_msg: str = "target path is outside the workspace",
) -> Path:
    """Resolve ``relative_path`` under ``workspace`` and refuse traversal.

    The result is always absolute; the relative-to check uses already-
    resolved paths so symlinks pointing outside the workspace are caught.
    """

    workspace_root = Path(workspace).resolve()
    target = (workspace_root / relative_path).resolve()
    try:
        target.relative_to(workspace_root)
    except ValueError as exc:
        raise PermissionError(permission_error_msg) from exc
    return target


def connect_sqlite_store(
    db_path: Path | str,
    *,
    wal: bool = True,
    busy_timeout_ms: int = 30_000,
) -> sqlite3.Connection:
    """Open a SQLite connection with our store defaults.

    Apply pragmas per connection (not just at schema init) so readers and
    writers share the same lock-wait policy.  See `PRAGMA busy_timeout`
    docs — it applies to *the connection*, not the database file.
    """

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if busy_timeout_ms > 0:
        conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
    if wal:
        conn.execute("PRAGMA journal_mode = WAL")
    return conn
