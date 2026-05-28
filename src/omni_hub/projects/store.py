"""ProjectStore — SQLite-backed project lifecycle (v0.34)."""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any


PROJECTS_DB_REL = ".omni/projects.sqlite3"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _new_project_id() -> str:
    return f"prj_{secrets.token_hex(6)}"


def _new_subtask_id() -> str:
    return f"st_{secrets.token_hex(6)}"


class ProjectStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class SubTask:
    subtask_id: str
    project_id: str
    title: str
    status: str = "open"                      # open | in_progress | done | cancelled
    worker_task_id: str = ""                  # link to queue.sqlite3 row
    depends_on: list[str] = field(default_factory=list)
    estimated_minutes: int = 60
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SubTask":
        return cls(
            subtask_id=row["subtask_id"],
            project_id=row["project_id"],
            title=row["title"],
            status=row["status"],
            worker_task_id=row["worker_task_id"] or "",
            depends_on=json.loads(row["depends_on"] or "[]"),
            estimated_minutes=int(row["estimated_minutes"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(slots=True)
class Project:
    project_id: str
    user_id: str
    title: str
    description: str = ""
    status: ProjectStatus = ProjectStatus.PENDING
    plan_markdown: str = ""                   # set by planner agent
    plan_proposal_id: str = ""                # Proposal(kind=project_plan) for human review
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Project":
        return cls(
            project_id=row["project_id"],
            user_id=row["user_id"],
            title=row["title"],
            description=row["description"] or "",
            status=ProjectStatus(row["status"]),
            plan_markdown=row["plan_markdown"] or "",
            plan_proposal_id=row["plan_proposal_id"] or "",
            metadata=json.loads(row["metadata"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class ProjectStore:
    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace).resolve()
        self.db_path = self.workspace / PROJECTS_DB_REL
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ---- project CRUD ------------------------------------------

    def create(
        self,
        *,
        user_id: str,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Project:
        if not title.strip():
            raise ValueError("title is required")
        project = Project(
            project_id=_new_project_id(),
            user_id=user_id,
            title=title.strip(),
            description=description,
            metadata=dict(metadata or {}),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO projects "
                "(project_id, user_id, title, description, status, "
                " plan_markdown, plan_proposal_id, metadata, "
                " created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (project.project_id, project.user_id, project.title,
                 project.description, project.status.value,
                 project.plan_markdown, project.plan_proposal_id,
                 json.dumps(project.metadata, ensure_ascii=False),
                 project.created_at, project.updated_at),
            )
            conn.commit()
        return project

    def get(self, project_id: str) -> Project | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,),
            ).fetchone()
        return Project.from_row(row) if row else None

    def list(
        self, *,
        user_id: str | None = None,
        status: ProjectStatus | None = None,
        limit: int = 100,
    ) -> list[Project]:
        clauses: list[str] = []
        params: list[Any] = []
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        sql = "SELECT * FROM projects"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [Project.from_row(r) for r in rows]

    def advance_state(
        self,
        project_id: str,
        new_state: ProjectStatus,
    ) -> Project:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE projects SET status = ?, updated_at = ? WHERE project_id = ?",
                (new_state.value, _utcnow(), project_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise KeyError(f"no project_id {project_id!r}")
        return self.get(project_id)  # type: ignore[return-value]

    def attach_plan(
        self,
        project_id: str,
        *,
        plan_markdown: str,
        plan_proposal_id: str = "",
    ) -> Project:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE projects "
                "SET plan_markdown = ?, plan_proposal_id = ?, status = ?, "
                "    updated_at = ? "
                "WHERE project_id = ?",
                (plan_markdown, plan_proposal_id,
                 ProjectStatus.PLANNING.value, _utcnow(), project_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise KeyError(f"no project_id {project_id!r}")
        return self.get(project_id)  # type: ignore[return-value]

    # ---- subtask CRUD -----------------------------------------

    def add_subtask(
        self,
        *,
        project_id: str,
        title: str,
        depends_on: list[str] | None = None,
        estimated_minutes: int = 60,
    ) -> SubTask:
        subtask = SubTask(
            subtask_id=_new_subtask_id(),
            project_id=project_id,
            title=title.strip(),
            depends_on=list(depends_on or []),
            estimated_minutes=max(1, int(estimated_minutes)),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO subtasks "
                "(subtask_id, project_id, title, status, worker_task_id, "
                " depends_on, estimated_minutes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (subtask.subtask_id, subtask.project_id, subtask.title,
                 subtask.status, subtask.worker_task_id,
                 json.dumps(subtask.depends_on),
                 subtask.estimated_minutes,
                 subtask.created_at, subtask.updated_at),
            )
            conn.commit()
        return subtask

    def list_subtasks(self, project_id: str) -> list[SubTask]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM subtasks WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
        return [SubTask.from_row(r) for r in rows]

    def link_worker_task(
        self, subtask_id: str, worker_task_id: str,
    ) -> SubTask:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE subtasks SET worker_task_id = ?, status = ?, updated_at = ? "
                "WHERE subtask_id = ?",
                (worker_task_id, "in_progress", _utcnow(), subtask_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise KeyError(f"no subtask_id {subtask_id!r}")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM subtasks WHERE subtask_id = ?",
                (subtask_id,),
            ).fetchone()
        return SubTask.from_row(row)

    # ---- helpers ---------------------------------------------

    def overview(self, *, user_id: str | None = None) -> dict[str, Any]:
        projects = self.list(user_id=user_id)
        tally = {s.value: 0 for s in ProjectStatus}
        for p in projects:
            tally[p.status.value] += 1
        return {
            "user_id": user_id,
            "tally": tally,
            "total": sum(tally.values()),
        }

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA busy_timeout = 30000;

                CREATE TABLE IF NOT EXISTS projects (
                    project_id       TEXT PRIMARY KEY,
                    user_id          TEXT NOT NULL,
                    title            TEXT NOT NULL,
                    description      TEXT DEFAULT '',
                    status           TEXT NOT NULL,
                    plan_markdown    TEXT DEFAULT '',
                    plan_proposal_id TEXT DEFAULT '',
                    metadata         TEXT DEFAULT '{}',
                    created_at       TEXT NOT NULL,
                    updated_at       TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_proj_user_status
                    ON projects(user_id, status, created_at DESC);

                CREATE TABLE IF NOT EXISTS subtasks (
                    subtask_id        TEXT PRIMARY KEY,
                    project_id        TEXT NOT NULL,
                    title             TEXT NOT NULL,
                    status            TEXT NOT NULL DEFAULT 'open',
                    worker_task_id    TEXT DEFAULT '',
                    depends_on        TEXT DEFAULT '[]',
                    estimated_minutes INTEGER NOT NULL DEFAULT 60,
                    created_at        TEXT NOT NULL,
                    updated_at        TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id)
                );
                CREATE INDEX IF NOT EXISTS idx_subtask_project
                    ON subtasks(project_id, status);
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


__all__ = ["Project", "ProjectStatus", "ProjectStore", "SubTask"]
