"""PersonalTaskStore — user-facing task list (v0.32).

**Not the worker TaskQueue.**  These are todo-style items the user
writes for themselves ("watch ACE paper", "送修车", "周五前看完 X 论文"),
with optional due date + priority + category.  Lives at
``.omni/personal_tasks.sqlite3`` (separate from ``queue.sqlite3``).

Lifecycle: ``open → in_progress → done`` (or → ``cancelled``).
Multi-user: per-user_id rows; status transitions are append-only via
audit columns.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


TASKS_DB_REL = ".omni/personal_tasks.sqlite3"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _new_task_id() -> str:
    return f"pt_{secrets.token_hex(6)}"


class TaskStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskCategory(str, Enum):
    """Loose grouping; users may extend via metadata.category_extra."""

    WORK = "work"
    RESEARCH = "research"
    PERSONAL = "personal"
    HEALTH = "health"
    FINANCE = "finance"
    LEARNING = "learning"
    OTHER = "other"


@dataclass(slots=True)
class PersonalTask:
    task_id: str
    user_id: str
    title: str
    description: str = ""
    category: TaskCategory = TaskCategory.OTHER
    status: TaskStatus = TaskStatus.OPEN
    priority: int = 3                       # 1 = highest, 5 = lowest
    estimated_minutes: int = 30
    due_at: str = ""                        # ISO 8601 UTC; empty = no due
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        data["status"] = self.status.value
        return data

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PersonalTask":
        return cls(
            task_id=row["task_id"],
            user_id=row["user_id"],
            title=row["title"],
            description=row["description"] or "",
            category=TaskCategory(row["category"]),
            status=TaskStatus(row["status"]),
            priority=int(row["priority"]),
            estimated_minutes=int(row["estimated_minutes"]),
            due_at=row["due_at"] or "",
            metadata=json.loads(row["metadata"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"] or "",
        )


class PersonalTaskStore:
    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace).resolve()
        self.db_path = self.workspace / TASKS_DB_REL
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ---- CRUD --------------------------------------------------

    def add(
        self,
        *,
        user_id: str,
        title: str,
        description: str = "",
        category: TaskCategory = TaskCategory.OTHER,
        priority: int = 3,
        estimated_minutes: int = 30,
        due_at: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> PersonalTask:
        if not title.strip():
            raise ValueError("title is required")
        if priority < 1 or priority > 5:
            raise ValueError("priority must be in [1, 5]")
        task = PersonalTask(
            task_id=_new_task_id(),
            user_id=user_id,
            title=title.strip(),
            description=description,
            category=category,
            priority=priority,
            estimated_minutes=max(1, int(estimated_minutes)),
            due_at=due_at,
            metadata=dict(metadata or {}),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO personal_tasks "
                "(task_id, user_id, title, description, category, status, "
                " priority, estimated_minutes, due_at, metadata, "
                " created_at, updated_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task.task_id, task.user_id, task.title, task.description,
                 task.category.value, task.status.value, task.priority,
                 task.estimated_minutes, task.due_at,
                 json.dumps(task.metadata, ensure_ascii=False),
                 task.created_at, task.updated_at, task.completed_at),
            )
            conn.commit()
        return task

    def update_status(
        self, task_id: str, status: TaskStatus,
    ) -> PersonalTask:
        now = _utcnow()
        completed = now if status is TaskStatus.DONE else ""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE personal_tasks "
                "SET status = ?, updated_at = ?, completed_at = ? "
                "WHERE task_id = ?",
                (status.value, now, completed, task_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise KeyError(f"no task_id {task_id!r}")
        return self.get(task_id)  # type: ignore[return-value]

    def get(self, task_id: str) -> PersonalTask | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM personal_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return PersonalTask.from_row(row) if row else None

    def list(
        self,
        *,
        user_id: str | None = None,
        status: TaskStatus | None = None,
        category: TaskCategory | None = None,
        limit: int = 100,
    ) -> list[PersonalTask]:
        clauses: list[str] = []
        params: list[Any] = []
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if category:
            clauses.append("category = ?")
            params.append(category.value)
        sql = "SELECT * FROM personal_tasks"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY priority, due_at, created_at LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [PersonalTask.from_row(r) for r in rows]

    def stats(self, *, user_id: str | None = None) -> dict[str, Any]:
        sql = ("SELECT status, COUNT(*) AS n FROM personal_tasks "
               + ("WHERE user_id = ? GROUP BY status" if user_id else "GROUP BY status"))
        params = (user_id,) if user_id else ()
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        tally = {s.value: 0 for s in TaskStatus}
        for row in rows:
            tally[row["status"]] = int(row["n"])
        return {"user_id": user_id, "tally": tally, "total": sum(tally.values())}

    # ---- internals -------------------------------------------

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA busy_timeout = 30000;

                CREATE TABLE IF NOT EXISTS personal_tasks (
                    task_id           TEXT PRIMARY KEY,
                    user_id           TEXT NOT NULL,
                    title             TEXT NOT NULL,
                    description       TEXT DEFAULT '',
                    category          TEXT NOT NULL,
                    status            TEXT NOT NULL,
                    priority          INTEGER NOT NULL DEFAULT 3,
                    estimated_minutes INTEGER NOT NULL DEFAULT 30,
                    due_at            TEXT DEFAULT '',
                    metadata          TEXT DEFAULT '{}',
                    created_at        TEXT NOT NULL,
                    updated_at        TEXT NOT NULL,
                    completed_at      TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_user_status
                    ON personal_tasks(user_id, status, priority, due_at);
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


__all__ = ["PersonalTask", "PersonalTaskStore", "TaskCategory", "TaskStatus"]
