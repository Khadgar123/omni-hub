"""Append-only Discord blogger revision ledger with TaskQueue fencing.

The ledger accepts a :class:`TaskQueue` rather than a free-standing database
path.  This makes it impossible to accidentally separate authoritative lease
state from domain revisions: both are checked and committed in one SQLite
``BEGIN IMMEDIATE`` transaction.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Iterator, Mapping

from .discord_blogger_contract import canonical_json_bytes, deterministic_entity_id
from .models import ConcurrentModificationError
from .queue import LeaseLost, Task, TaskQueue, _now_ms


_ENTITY_KINDS = frozenset({"message", "event", "lifecycle"})


class BloggerLedger:
    def __init__(self, queue: TaskQueue) -> None:
        if not isinstance(queue, TaskQueue):
            raise TypeError("BloggerLedger requires the authoritative TaskQueue")
        self.queue = queue
        self.db_path = queue.db_path
        self._init_schema()

    def begin_attempt(
        self,
        *,
        task_id: str,
        claimed_by: str,
        lease_epoch: int,
    ) -> str:
        task_number = _task_number(task_id)
        attempt_id = _attempt_id(task_number, claimed_by, lease_epoch)
        now = _now_ms()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._validate_lease(
                conn,
                task_id=task_number,
                claimed_by=claimed_by,
                lease_epoch=lease_epoch,
                now=now,
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO blogger_attempts (
                    attempt_id, task_id, claimed_by, lease_epoch,
                    fencing_suffix, started_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    task_number,
                    claimed_by,
                    lease_epoch,
                    Task(id=task_number, lease_epoch=lease_epoch).fencing_suffix(),
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT task_id, claimed_by, lease_epoch
                FROM blogger_attempts WHERE attempt_id = ?
                """,
                (attempt_id,),
            ).fetchone()
            if (
                row is None
                or int(row["task_id"]) != task_number
                or row["claimed_by"] != claimed_by
                or int(row["lease_epoch"]) != lease_epoch
            ):
                conn.rollback()
                raise RuntimeError("attempt identity collision")
            conn.commit()
        return attempt_id

    def commit_message_revision(
        self,
        *,
        message_id: str,
        revision: Mapping[str, object],
        expected_revision: str | None,
        task_id: str,
        claimed_by: str,
        lease_epoch: int,
    ) -> str:
        return self._commit_revision(
            entity_kind="message",
            entity_id=message_id,
            revision=revision,
            expected_revision=expected_revision,
            task_id=task_id,
            claimed_by=claimed_by,
            lease_epoch=lease_epoch,
        )

    def commit_event_revision(
        self,
        *,
        event_id: str,
        revision: Mapping[str, object],
        expected_revision: str | None,
        task_id: str,
        claimed_by: str,
        lease_epoch: int,
    ) -> str:
        return self._commit_revision(
            entity_kind="event",
            entity_id=event_id,
            revision=revision,
            expected_revision=expected_revision,
            task_id=task_id,
            claimed_by=claimed_by,
            lease_epoch=lease_epoch,
        )

    def replace_lifecycle_revision(
        self,
        *,
        lifecycle_id: str,
        revision: Mapping[str, object],
        expected_revision: str | None,
        task_id: str,
        claimed_by: str,
        lease_epoch: int,
    ) -> str:
        return self._commit_revision(
            entity_kind="lifecycle",
            entity_id=lifecycle_id,
            revision=revision,
            expected_revision=expected_revision,
            task_id=task_id,
            claimed_by=claimed_by,
            lease_epoch=lease_epoch,
        )

    def current_rows(self, entity_kind: str) -> Iterator[dict[str, object]]:
        if entity_kind not in _ENTITY_KINDS:
            raise ValueError(f"unknown blogger ledger entity kind: {entity_kind!r}")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.entity_kind, r.entity_id, r.revision_id,
                       r.predecessor_revision_id, r.payload_json,
                       r.task_id, r.claimed_by, r.lease_epoch, r.created_at
                FROM blogger_current_revisions AS c
                JOIN blogger_revisions AS r
                  ON r.revision_id = c.revision_id
                WHERE c.entity_kind = ?
                ORDER BY r.entity_id
                """,
                (entity_kind,),
            ).fetchall()
        for row in rows:
            yield {
                "entity_kind": row["entity_kind"],
                "entity_id": row["entity_id"],
                "revision_id": row["revision_id"],
                "predecessor_revision_id": row["predecessor_revision_id"],
                "revision": json.loads(row["payload_json"]),
                "task_id": str(row["task_id"]),
                "claimed_by": row["claimed_by"],
                "lease_epoch": int(row["lease_epoch"]),
                "created_at": int(row["created_at"]),
            }

    def _commit_revision(
        self,
        *,
        entity_kind: str,
        entity_id: str,
        revision: Mapping[str, object],
        expected_revision: str | None,
        task_id: str,
        claimed_by: str,
        lease_epoch: int,
    ) -> str:
        if entity_kind not in _ENTITY_KINDS:
            raise ValueError(f"unknown blogger ledger entity kind: {entity_kind!r}")
        if not isinstance(entity_id, str) or not entity_id:
            raise ValueError("ledger entity ID must be a non-empty string")
        if not isinstance(revision, Mapping):
            raise TypeError("ledger revision must be a mapping")
        payload_json = canonical_json_bytes(dict(revision)).decode("utf-8")
        revision_id = deterministic_entity_id(
            f"{entity_kind}_revision",
            entity_id,
            expected_revision,
            json.loads(payload_json),
        )
        task_number = _task_number(task_id)
        attempt_id = _attempt_id(task_number, claimed_by, lease_epoch)
        now = _now_ms()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._validate_lease(
                conn,
                task_id=task_number,
                claimed_by=claimed_by,
                lease_epoch=lease_epoch,
                now=now,
            )
            attempt = conn.execute(
                "SELECT 1 FROM blogger_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if attempt is None:
                conn.rollback()
                raise RuntimeError("begin_attempt must succeed before a ledger commit")

            duplicate = conn.execute(
                """
                SELECT entity_kind, entity_id, predecessor_revision_id,
                       payload_json, task_id, claimed_by, lease_epoch
                FROM blogger_revisions WHERE revision_id = ?
                """,
                (revision_id,),
            ).fetchone()
            if duplicate is not None:
                expected_values = (
                    entity_kind,
                    entity_id,
                    expected_revision,
                    payload_json,
                )
                actual_values = (
                    duplicate["entity_kind"],
                    duplicate["entity_id"],
                    duplicate["predecessor_revision_id"],
                    duplicate["payload_json"],
                )
                if actual_values != expected_values:
                    conn.rollback()
                    raise RuntimeError("deterministic revision identity collision")
                conn.commit()
                return revision_id

            current = conn.execute(
                """
                SELECT revision_id FROM blogger_current_revisions
                WHERE entity_kind = ? AND entity_id = ?
                """,
                (entity_kind, entity_id),
            ).fetchone()
            current_id = None if current is None else str(current["revision_id"])
            if current_id != expected_revision:
                conn.rollback()
                raise ConcurrentModificationError(
                    f"{entity_kind} {entity_id!r}: expected predecessor "
                    f"{expected_revision!r}, current is {current_id!r}"
                )

            conn.execute(
                """
                INSERT INTO blogger_revisions (
                    revision_id, entity_kind, entity_id,
                    predecessor_revision_id, payload_json, attempt_id,
                    task_id, claimed_by, lease_epoch, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    entity_kind,
                    entity_id,
                    expected_revision,
                    payload_json,
                    attempt_id,
                    task_number,
                    claimed_by,
                    lease_epoch,
                    now,
                ),
            )
            if expected_revision is None:
                conn.execute(
                    """
                    INSERT INTO blogger_current_revisions (
                        entity_kind, entity_id, revision_id
                    ) VALUES (?, ?, ?)
                    """,
                    (entity_kind, entity_id, revision_id),
                )
            else:
                changed = conn.execute(
                    """
                    UPDATE blogger_current_revisions
                    SET revision_id = ?
                    WHERE entity_kind = ? AND entity_id = ? AND revision_id = ?
                    """,
                    (revision_id, entity_kind, entity_id, expected_revision),
                ).rowcount
                if changed != 1:  # pragma: no cover - locked CAS is defensive
                    conn.rollback()
                    raise ConcurrentModificationError(
                        f"{entity_kind} {entity_id!r}: current revision changed"
                    )
            conn.commit()
        return revision_id

    def _validate_lease(
        self,
        conn: sqlite3.Connection,
        *,
        task_id: int,
        claimed_by: str,
        lease_epoch: int,
        now: int,
    ) -> None:
        row = conn.execute(
            """
            SELECT state, claimed_by, lease_epoch, lease_deadline
            FROM tasks WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
        valid = (
            row is not None
            and row["state"] == "claimed"
            and row["claimed_by"] == claimed_by
            and int(row["lease_epoch"]) == lease_epoch
            and row["lease_deadline"] is not None
            and int(row["lease_deadline"]) > now
        )
        if not valid:
            current = (
                "missing"
                if row is None
                else (
                    f"state={row['state']!r}, holder={row['claimed_by']!r}, "
                    f"epoch={int(row['lease_epoch'])}, "
                    f"deadline={row['lease_deadline']!r}"
                )
            )
            raise LeaseLost(
                f"task {task_id}: authoritative queue lease lost ({current}); "
                f"caller={claimed_by!r}, caller_epoch={lease_epoch!r}"
            )

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA synchronous = FULL;
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS blogger_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    task_id INTEGER NOT NULL,
                    claimed_by TEXT NOT NULL,
                    lease_epoch INTEGER NOT NULL,
                    fencing_suffix TEXT NOT NULL,
                    started_at INTEGER NOT NULL,
                    UNIQUE (task_id, claimed_by, lease_epoch),
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE RESTRICT
                );

                CREATE TABLE IF NOT EXISTS blogger_revisions (
                    revision_id TEXT PRIMARY KEY,
                    entity_kind TEXT NOT NULL
                        CHECK (entity_kind IN ('message', 'event', 'lifecycle')),
                    entity_id TEXT NOT NULL,
                    predecessor_revision_id TEXT,
                    payload_json TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    task_id INTEGER NOT NULL,
                    claimed_by TEXT NOT NULL,
                    lease_epoch INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE (entity_kind, entity_id, revision_id),
                    FOREIGN KEY (predecessor_revision_id)
                        REFERENCES blogger_revisions(revision_id) ON DELETE RESTRICT,
                    FOREIGN KEY (attempt_id)
                        REFERENCES blogger_attempts(attempt_id) ON DELETE RESTRICT,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE RESTRICT
                );

                CREATE TABLE IF NOT EXISTS blogger_current_revisions (
                    entity_kind TEXT NOT NULL
                        CHECK (entity_kind IN ('message', 'event', 'lifecycle')),
                    entity_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL UNIQUE,
                    PRIMARY KEY (entity_kind, entity_id),
                    FOREIGN KEY (revision_id)
                        REFERENCES blogger_revisions(revision_id) ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_blogger_revision_entity
                    ON blogger_revisions(entity_kind, entity_id, created_at);
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = self.queue._connect()
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _task_number(task_id: str) -> int:
    if not isinstance(task_id, str) or not task_id.isdigit():
        raise ValueError("task_id must be the decimal TaskQueue ID string")
    return int(task_id)


def _attempt_id(task_id: int, claimed_by: str, lease_epoch: int) -> str:
    if not isinstance(claimed_by, str) or not claimed_by:
        raise ValueError("claimed_by must be a non-empty worker ID")
    if not isinstance(lease_epoch, int) or lease_epoch < 1:
        raise ValueError("lease_epoch must be a positive integer")
    fencing = Task(id=task_id, lease_epoch=lease_epoch).fencing_suffix()
    return deterministic_entity_id("attempt", task_id, claimed_by, fencing)
