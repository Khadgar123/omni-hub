"""SQLite-backed durable task queue ("AgentJob Queue").

Designed for the single-user, stdlib-only, macOS-first deployment posture:
SQLite WAL is the canonical store; ``BEGIN IMMEDIATE`` + ``UPDATE … RETURNING``
gives atomic claim with zero double-deliveries under multi-worker contention
(see https://dev.to/d_security/why-i-built-a-job-queue-with-sqlite ).

State machine:

    pending  ─enqueue─►  pending  ─claim(visible)─►  claimed
                          ▲                            │
                          │                            │── complete ─► done
                          │                            │
                          │── crash & visibility_timeout
                          │                            │
                          └────────────────────────────┤── fail(retryable) ─► pending (backoff)
                                                       │
                                                       └── fail(>max_attempts) ─► dead

Idempotent enqueue: if ``idempotency_key`` collides with an existing row and
the canonical packet bytes match, the existing task is returned.  A different
packet fails closed instead of silently binding the caller to unrelated work.
"""

from __future__ import annotations

import json
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PENDING = "pending"
CLAIMED = "claimed"
DONE = "done"
FAILED = "failed"
DEAD = "dead"
VALID_STATES = {PENDING, CLAIMED, DONE, FAILED, DEAD}


class LeaseLost(RuntimeError):
    """A worker tried to complete/fail a task it no longer holds.

    Typical cause: the worker stalled past the visibility timeout and
    another worker reclaimed the task.  The losing worker MUST NOT
    silently overwrite the new holder's progress — surface this so the
    caller can drop the result and move on.
    """


class IdempotencyKeyCollision(ValueError):
    """An enqueue key was reused for different canonical packet bytes."""


DEFAULT_VISIBILITY_TIMEOUT_SEC = 600          # 10 min reclaim window
DEFAULT_BACKOFF_BASE_SEC = 60
DEFAULT_BACKOFF_CAP_SEC = 3600


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass(slots=True)
class Task:
    id: int = 0
    idempotency_key: str = ""
    trace_id: str = ""                        # W3C trace correlation (HR #4)
    domain_profile: str = ""
    lane: str = "python"                      # python | claude | codex | ...
    packet: dict[str, Any] = field(default_factory=dict)
    state: str = PENDING
    attempts: int = 0
    max_attempts: int = 3
    available_at: int = 0
    claimed_at: int | None = None
    claimed_by: str | None = None
    lease_epoch: int = 0                      # monotonic fencing token (Kleppmann)
    lease_deadline: int | None = None
    last_error: str | None = None
    output: dict[str, Any] | None = None
    created_at: int = 0
    updated_at: int = 0

    def fencing_suffix(self) -> str:
        """Stable suffix for downstream idempotency keys.

        Embed this into upstream-API idempotency keys so a stale worker's
        retry can be rejected by the *external* service: even if two
        workers think they hold the same lease, only the current epoch's
        suffix matches the row state.
        """

        return f"t{self.id}:e{self.lease_epoch}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "idempotency_key": self.idempotency_key,
            "trace_id": self.trace_id,
            "domain_profile": self.domain_profile,
            "lane": self.lane,
            "packet": self.packet,
            "state": self.state,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "available_at": self.available_at,
            "claimed_at": self.claimed_at,
            "claimed_by": self.claimed_by,
            "lease_epoch": self.lease_epoch,
            "lease_deadline": self.lease_deadline,
            "last_error": self.last_error,
            "output": self.output,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _task_from_row(row: sqlite3.Row) -> Task:
    keys = row.keys()
    return Task(
        id=int(row["id"]),
        idempotency_key=row["idempotency_key"] or "",
        # trace_id added by migration; tolerate missing column on legacy dbs.
        trace_id=row["trace_id"] if "trace_id" in keys and row["trace_id"] is not None else "",
        domain_profile=row["domain_profile"] or "",
        lane=row["lane"],
        packet=json.loads(row["packet_json"]) if row["packet_json"] else {},
        state=row["state"],
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        available_at=int(row["available_at"]),
        claimed_at=int(row["claimed_at"]) if row["claimed_at"] is not None else None,
        claimed_by=row["claimed_by"],
        # lease_epoch is added by migration; tolerate missing column on
        # legacy databases until they're touched.
        lease_epoch=int(row["lease_epoch"]) if "lease_epoch" in keys and row["lease_epoch"] is not None else 0,
        lease_deadline=(
            int(row["lease_deadline"])
            if "lease_deadline" in keys and row["lease_deadline"] is not None
            else None
        ),
        last_error=row["last_error"],
        output=json.loads(row["output_json"]) if row["output_json"] else None,
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


class TaskQueue:
    """Durable task queue backed by a single SQLite file in the workspace."""

    def __init__(
        self,
        workspace: Path | str = ".",
        db_path: str = ".omni/queue.sqlite3",
        *,
        create: bool = True,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.db_path = self._safe_path(db_path)
        if create:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()

    # ------- public API ----------------------------------------------------

    def enqueue(
        self,
        *,
        lane: str,
        packet: dict[str, Any],
        domain_profile: str = "",
        trace_id: str = "",
        idempotency_key: str | None = None,
        available_at: int | None = None,
        max_attempts: int = 3,
    ) -> Task:
        """Insert a task, or replay an exact canonical-packet enqueue."""

        key = idempotency_key or _new_id()
        now = _now_ms()
        avail = int(available_at if available_at is not None else now)
        packet_json = _canonical_packet_json(packet)

        with self._connect() as conn:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO tasks (
                        idempotency_key, trace_id, domain_profile, lane, packet_json,
                        state, attempts, max_attempts, available_at,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?)
                    RETURNING *
                    """,
                    (
                        key, trace_id, domain_profile, lane, packet_json,
                        max_attempts, avail, now, now,
                    ),
                )
                row = cur.fetchone()
                conn.commit()
                return _task_from_row(row)
            except sqlite3.IntegrityError:
                # A duplicate key is valid only for byte-identical canonical
                # packets.  Returning an unrelated task would silently bind
                # the caller to somebody else's work.
                row = conn.execute(
                    "SELECT * FROM tasks WHERE idempotency_key = ?", (key,),
                ).fetchone()
                if row is None:  # pragma: no cover — defensive
                    raise
                existing_packet_json = _canonical_packet_json(
                    json.loads(row["packet_json"])
                )
                if existing_packet_json != packet_json:
                    raise IdempotencyKeyCollision(
                        f"idempotency key collision for {key!r}: "
                        "canonical packet bytes differ"
                    )
                return _task_from_row(row)

    def claim(
        self,
        *,
        lane: str,
        claimed_by: str | None = None,
        visibility_timeout_sec: int = DEFAULT_VISIBILITY_TIMEOUT_SEC,
    ) -> Task | None:
        """Atomically claim the next eligible task for the given lane.

        A task is eligible if it is ``pending`` and ``available_at`` has
        elapsed, OR it is ``claimed`` but the worker who took it has been
        silent past the visibility timeout (likely crashed).

        Every claim **monotonically increments ``lease_epoch``** — this is
        the Kleppmann fencing token.  ``claimed_by`` alone is not enough
        because a UUID identifies *who* held the lease, not *which
        generation* of it; downstream callers should embed both
        ``(claimed_by, lease_epoch)`` (or ``Task.fencing_suffix()``) into
        any external-side idempotency key.
        """

        worker = claimed_by or _new_id()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Sample after the write lock is acquired.  A worker may have
            # waited behind another writer long enough for a lease boundary
            # to pass; using a pre-lock timestamp would mint an already-stale
            # lease or miss an authoritative expiry.
            now = _now_ms()
            stale_threshold = now - int(visibility_timeout_sec * 1000)
            lease_deadline = now + int(visibility_timeout_sec * 1000)
            row = conn.execute(
                """
                UPDATE tasks
                SET state = 'claimed',
                    claimed_at = ?,
                    claimed_by = ?,
                    lease_epoch = lease_epoch + 1,
                    lease_deadline = ?,
                    attempts = attempts + 1,
                    updated_at = ?
                WHERE id = (
                    SELECT id FROM tasks
                    WHERE lane = ?
                      AND (
                        (state = 'pending' AND available_at <= ?)
                        OR (
                            state = 'claimed'
                            AND (
                                (lease_deadline IS NOT NULL AND lease_deadline <= ?)
                                OR (
                                    lease_deadline IS NULL
                                    AND claimed_at IS NOT NULL
                                    AND claimed_at < ?
                                )
                            )
                        )
                      )
                    ORDER BY available_at ASC, id ASC
                    LIMIT 1
                )
                RETURNING *
                """,
                (
                    now,
                    worker,
                    lease_deadline,
                    now,
                    lane,
                    now,
                    now,
                    stale_threshold,
                ),
            ).fetchone()
            conn.commit()

        return _task_from_row(row) if row is not None else None

    def complete(
        self,
        task_id: int,
        *,
        output: dict[str, Any] | None = None,
        claimed_by: str | None = None,
        lease_epoch: int | None = None,
    ) -> Task:
        """Mark a claimed task done.

        Fencing rules:
        * If both ``claimed_by`` AND ``lease_epoch`` are passed, the row
          is only updated when both match — the Kleppmann fencing-token
          pattern.  This is what new worker code must do.
        * If only ``claimed_by`` is passed, falls back to holder-identity
          fencing (correct against most stale-worker races but not
          against the lease-steal-then-restored edge case).
        * If neither is passed, the call is unfenced — legacy contract;
          do not use in new code.
        """

        now = _now_ms()
        output_json = json.dumps(output, ensure_ascii=False) if output is not None else None
        with self._connect() as conn:
            if claimed_by is not None and lease_epoch is not None:
                row = conn.execute(
                    """
                    UPDATE tasks
                    SET state = 'done',
                        output_json = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND state = 'claimed'
                      AND claimed_by = ?
                      AND lease_epoch = ?
                    RETURNING *
                    """,
                    (output_json, now, task_id, claimed_by, lease_epoch),
                ).fetchone()
            elif claimed_by is not None:
                row = conn.execute(
                    """
                    UPDATE tasks
                    SET state = 'done',
                        output_json = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND state = 'claimed'
                      AND claimed_by = ?
                    RETURNING *
                    """,
                    (output_json, now, task_id, claimed_by),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    UPDATE tasks
                    SET state = 'done',
                        output_json = ?,
                        updated_at = ?
                    WHERE id = ?
                    RETURNING *
                    """,
                    (output_json, now, task_id),
                ).fetchone()
            conn.commit()

        if row is None:
            # Either the task doesn't exist OR the lease was lost.
            existing = self._fetch_raw(task_id)
            if existing is None:
                raise FileNotFoundError(f"task does not exist: {task_id}")
            raise LeaseLost(
                f"task {task_id}: lease lost (state={existing['state']!r}, "
                f"current_holder={existing['claimed_by']!r}, "
                f"current_epoch={existing['lease_epoch'] if 'lease_epoch' in existing.keys() else 0}, "
                f"caller={claimed_by!r}, caller_epoch={lease_epoch!r})"
            )
        return _task_from_row(row)

    def fail(
        self,
        task_id: int,
        *,
        error: str,
        claimed_by: str | None = None,
        lease_epoch: int | None = None,
        backoff_base_sec: int = DEFAULT_BACKOFF_BASE_SEC,
        backoff_cap_sec: int = DEFAULT_BACKOFF_CAP_SEC,
    ) -> Task:
        """Record a failure; either reschedule with backoff or transition to dead.

        Fenced by ``(claimed_by, lease_epoch)`` when both supplied — same
        contract as :meth:`complete`.  Backoff uses AWS "Full Jitter"
        (random.randint(0, exp_capped)) to avoid synchronized retries.
        """

        now = _now_ms()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT attempts, max_attempts, state, claimed_by, lease_epoch "
                "FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if current is None:
                conn.execute("ROLLBACK")
                raise FileNotFoundError(f"task does not exist: {task_id}")
            if claimed_by is not None and (
                current["state"] != "claimed" or current["claimed_by"] != claimed_by
                or (lease_epoch is not None and int(current["lease_epoch"]) != lease_epoch)
            ):
                conn.execute("ROLLBACK")
                raise LeaseLost(
                    f"task {task_id}: lease lost (state={current['state']!r}, "
                    f"current_holder={current['claimed_by']!r}, "
                    f"current_epoch={int(current['lease_epoch'])}, "
                    f"caller={claimed_by!r}, caller_epoch={lease_epoch!r})"
                )
            attempts = int(current["attempts"])
            max_attempts = int(current["max_attempts"])

            if attempts >= max_attempts:
                row = conn.execute(
                    """
                    UPDATE tasks
                    SET state = 'dead',
                        last_error = ?,
                        updated_at = ?
                    WHERE id = ?
                    RETURNING *
                    """,
                    (error, now, task_id),
                ).fetchone()
            else:
                # AWS "Full Jitter" — random(0, min(cap, base*2**attempts)) — keeps
                # synchronized retries off the same upstream 429 window.  When
                # callers pass backoff_base_sec=0 (e.g. tests, or fire-and-retry
                # immediately) the jitter ceiling is 0 too, so we get a 0 ms wait.
                ceiling_ms = min(
                    backoff_base_sec * (2 ** attempts),
                    backoff_cap_sec,
                ) * 1000
                backoff_ms = random.randint(0, ceiling_ms) if ceiling_ms > 0 else 0
                row = conn.execute(
                    """
                    UPDATE tasks
                    SET state = 'pending',
                        last_error = ?,
                        available_at = ?,
                        claimed_at = NULL,
                        claimed_by = NULL,
                        lease_deadline = NULL,
                        updated_at = ?
                    WHERE id = ?
                    RETURNING *
                    """,
                    (error, now + backoff_ms, now, task_id),
                ).fetchone()
            conn.commit()
        return _task_from_row(row)

    def _fetch_raw(self, task_id: int) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,),
            ).fetchone()

    def get(self, task_id: int) -> Task:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,),
            ).fetchone()
        if row is None:
            raise FileNotFoundError(f"task does not exist: {task_id}")
        return _task_from_row(row)

    def list(
        self,
        *,
        state: str | None = None,
        lane: str | None = None,
        limit: int = 50,
    ) -> list[Task]:
        if not self.db_path.exists():
            return []
        clauses: list[str] = []
        params: list[object] = []
        if state:
            clauses.append("state = ?")
            params.append(state)
        if lane:
            clauses.append("lane = ?")
            params.append(lane)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def counts_by_state(self) -> dict[str, int]:
        if not self.db_path.exists():
            return {s: 0 for s in VALID_STATES}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT state, COUNT(*) AS n FROM tasks GROUP BY state"
            ).fetchall()
        out = {s: 0 for s in VALID_STATES}
        for row in rows:
            out[row["state"]] = int(row["n"])
        return out

    def stats(self) -> dict[str, Any]:
        """Observability snapshot (derived, no schema changes).

        Returns queue-depth gauges, oldest-pending age, claim-to-done latency
        percentiles, attempts distribution, and dead count.  All values are
        absolute counts / ms — no rates (caller computes those from two
        snapshots).
        """

        empty: dict[str, Any] = {
            "counts_by_state": {s: 0 for s in VALID_STATES},
            "depth_by_lane": {},
            "depth_by_lane_state": {},
            "oldest_pending_age_ms": 0,
            "latency_ms": {"p50": 0, "p95": 0, "p99": 0, "count": 0},
            "attempts_distribution": {},
            "dead_count": 0,
        }
        if not self.db_path.exists():
            return empty

        now = _now_ms()
        with self._connect() as conn:
            counts_by_state = {
                row["state"]: int(row["n"])
                for row in conn.execute(
                    "SELECT state, COUNT(*) AS n FROM tasks GROUP BY state"
                ).fetchall()
            }
            counts_by_state = {s: counts_by_state.get(s, 0) for s in VALID_STATES}

            depth_by_lane: dict[str, int] = {}
            for row in conn.execute(
                "SELECT lane, COUNT(*) AS n FROM tasks "
                "WHERE state IN ('pending','claimed') GROUP BY lane"
            ).fetchall():
                depth_by_lane[row["lane"]] = int(row["n"])

            depth_by_lane_state: dict[str, dict[str, int]] = {}
            for row in conn.execute(
                "SELECT lane, state, COUNT(*) AS n FROM tasks GROUP BY lane, state"
            ).fetchall():
                depth_by_lane_state.setdefault(row["lane"], {})[row["state"]] = int(row["n"])

            oldest_row = conn.execute(
                "SELECT MIN(available_at) AS m FROM tasks WHERE state='pending'"
            ).fetchone()
            oldest_pending_age_ms = (
                max(0, now - int(oldest_row["m"]))
                if oldest_row and oldest_row["m"] is not None else 0
            )

            latencies = [
                int(row["latency"])
                for row in conn.execute(
                    "SELECT (updated_at - claimed_at) AS latency FROM tasks "
                    "WHERE state='done' AND claimed_at IS NOT NULL "
                    "ORDER BY latency"
                ).fetchall()
            ]
            latency_ms = _percentiles(latencies)

            attempts_distribution: dict[str, int] = {}
            for row in conn.execute(
                "SELECT attempts, COUNT(*) AS n FROM tasks GROUP BY attempts"
            ).fetchall():
                attempts_distribution[str(int(row["attempts"]))] = int(row["n"])

        return {
            "counts_by_state": counts_by_state,
            "depth_by_lane": depth_by_lane,
            "depth_by_lane_state": depth_by_lane_state,
            "oldest_pending_age_ms": oldest_pending_age_ms,
            "latency_ms": latency_ms,
            "attempts_distribution": attempts_distribution,
            "dead_count": counts_by_state.get(DEAD, 0),
        }

    # ------- internals -----------------------------------------------------

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA busy_timeout = 30000;

                CREATE TABLE IF NOT EXISTS tasks (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    trace_id        TEXT NOT NULL DEFAULT '',
                    domain_profile  TEXT NOT NULL DEFAULT '',
                    lane            TEXT NOT NULL,
                    packet_json     TEXT NOT NULL,
                    state           TEXT NOT NULL DEFAULT 'pending',
                    attempts        INTEGER NOT NULL DEFAULT 0,
                    max_attempts    INTEGER NOT NULL DEFAULT 3,
                    available_at    INTEGER NOT NULL,
                    claimed_at      INTEGER,
                    claimed_by      TEXT,
                    lease_epoch     INTEGER NOT NULL DEFAULT 0,
                    lease_deadline  INTEGER,
                    last_error      TEXT,
                    output_json     TEXT,
                    created_at      INTEGER NOT NULL,
                    updated_at      INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_claim
                    ON tasks(lane, state, available_at, id);
                """
            )
            # Migration: add lease_epoch to legacy databases created before
            # v0.8 P0-1 — the CREATE TABLE IF NOT EXISTS above does NOT add
            # columns to a pre-existing table.
            cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "lease_epoch" not in cols:
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN lease_epoch INTEGER NOT NULL DEFAULT 0"
                )
            if "trace_id" not in cols:
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN trace_id TEXT NOT NULL DEFAULT ''"
                )
            if "lease_deadline" not in cols:
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN lease_deadline INTEGER"
                )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        from ._storage import connect_sqlite_store
        conn = connect_sqlite_store(self.db_path)
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _safe_path(self, relative_path: str) -> Path:
        from ._storage import safe_workspace_path
        return safe_workspace_path(self.workspace, relative_path)


def _percentiles(sorted_values: list[int]) -> dict[str, int]:
    """Nearest-rank percentiles + count over a sorted list.  Empty → zeros."""

    n = len(sorted_values)
    if n == 0:
        return {"p50": 0, "p95": 0, "p99": 0, "count": 0}

    def _at(p: float) -> int:
        # nearest-rank: ceil(p * n) - 1
        idx = max(0, min(n - 1, int(p * n + 0.999) - 1))
        return int(sorted_values[idx])

    return {
        "p50": _at(0.50),
        "p95": _at(0.95),
        "p99": _at(0.99),
        "count": n,
    }


def _canonical_packet_json(packet: dict[str, Any]) -> str:
    """Canonical packet bytes used for idempotent enqueue collision checks."""

    if not isinstance(packet, dict):
        raise TypeError("task packet must be a JSON object")
    _validate_packet_json(packet)
    try:
        return json.dumps(
            packet,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("task packet must be strict canonical JSON") from exc


def _validate_packet_json(value: object) -> None:
    if value is None or isinstance(value, (str, bool, int, float)):
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_packet_json(item)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("task packet mapping keys must be strings")
        for item in value.values():
            _validate_packet_json(item)
        return
    raise ValueError(
        f"task packet value is not representable as JSON: {type(value).__name__}"
    )
