"""v0.18-F/G Workflow Kernel — multi-step state machine + Signal/Query.

Background
----------
Through v0.17 a Command runs a single Operation (one Step).  Multi-step
flows are implicit: schedule-tick enqueues N tasks, each is independent.
ResearchFlow's real pipeline is 6 steps across hours
(collect → parse → analyse → extract → propose → review) — losing
intermediate state on crash is the v0.17 reality.

This module is the Temporal-shape but SQLite-backed kernel:

    WorkflowRun     (id, template_name, state, cursor, schema_version)
    StepRun         (workflow_run_id, op_name, inputs, attempts, state,
                     lease_epoch, started_at, ended_at, artifact_id)
    StepDefinition  (forward + compensate + idempotency_key_fn)         ← orchestration saga
    Signal          (workflow_run_id, name, payload, consumed_at)        ← Temporal Signal
    Query           (workflow_run_id, name, response, served_at)         ← Temporal Query

Why SQLite, not Temporal: single-user, local-first, stdlib-only.  The
contract is **Temporal-compatible** so we can swap the backend later
without touching ResearchFlow / wiki-ingest callers.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from ._storage import safe_workspace_path


WORKFLOW_DB_REL = ".omni/workflows.sqlite3"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str = "wf") -> str:
    return f"{prefix}_{int(time.time() * 1000):x}_{secrets.token_hex(4)}"


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


class WorkflowState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUSPENDED = "suspended"           # waiting for Signal / Proposal approval
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"


@dataclass(slots=True)
class StepDefinition:
    """One step a workflow template can include.

    ``forward`` is required; ``compensate`` is optional — present only
    when the step has reversible side effects (orchestration-style
    saga compensation, microservices.io pattern).
    """

    name: str                         # canonical step name (op_name)
    forward: Callable[[dict], dict]
    compensate: Callable[[dict, dict], None] | None = None
    idempotency_key_fn: Callable[[dict], str] | None = None
    schema_version: str = "v0.18"


@dataclass(slots=True)
class WorkflowTemplate:
    """A reusable multi-step plan.  Steps run in order;  each may
    suspend on Proposal approval and resume after Signal."""

    name: str
    steps: list[StepDefinition]
    description: str = ""
    schema_version: str = "v0.18"


@dataclass(slots=True)
class WorkflowRun:
    workflow_run_id: str
    template_name: str
    state: WorkflowState
    cursor: int                       # next step index
    trace_id: str
    started_at: str = field(default_factory=_utcnow)
    suspended_at: str | None = None
    completed_at: str | None = None
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value if isinstance(self.state, WorkflowState) else self.state
        return data


@dataclass(slots=True)
class StepRun:
    step_id: str
    workflow_run_id: str
    op_name: str
    cursor_index: int
    inputs: dict[str, Any]
    state: StepState
    attempts: int = 0
    lease_epoch: int = 0
    started_at: str | None = None
    ended_at: str | None = None
    artifact: dict[str, Any] | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value if isinstance(self.state, StepState) else self.state
        return data


# ---------------------------------------------------------------------------
# Store + Kernel
# ---------------------------------------------------------------------------


class WorkflowStore:
    """SQLite-backed state machine.  Single writer per process — like
    TaskQueue, we rely on SQLite WAL + BEGIN IMMEDIATE for atomicity."""

    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace).resolve()
        self.db_path = safe_workspace_path(self.workspace, WORKFLOW_DB_REL)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ---- workflow ops -----------------------------------------------

    def create_workflow(
        self,
        *,
        template_name: str,
        trace_id: str,
        first_step_inputs: dict[str, Any],
        step_op_names: list[str],
    ) -> WorkflowRun:
        run_id = _new_id("wf")
        run = WorkflowRun(
            workflow_run_id=run_id,
            template_name=template_name,
            state=WorkflowState.PENDING,
            cursor=0,
            trace_id=trace_id,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO workflows (workflow_run_id, template_name, state, "
                "cursor, trace_id, started_at) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, template_name, run.state.value, 0, trace_id, run.started_at),
            )
            for idx, op_name in enumerate(step_op_names):
                step_id = _new_id("step")
                inputs = first_step_inputs if idx == 0 else {}
                conn.execute(
                    "INSERT INTO steps (step_id, workflow_run_id, op_name, "
                    "cursor_index, inputs, state, attempts) "
                    "VALUES (?, ?, ?, ?, ?, ?, 0)",
                    (step_id, run_id, op_name, idx,
                     json.dumps(inputs, ensure_ascii=False), StepState.PENDING.value),
                )
            conn.commit()
        return run

    def get_workflow(self, run_id: str) -> WorkflowRun:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workflows WHERE workflow_run_id = ?",
                (run_id,),
            ).fetchone()
        if not row:
            raise KeyError(f"workflow {run_id!r} not found")
        return self._row_to_workflow(row)

    def list_workflows(
        self,
        *,
        state: str | None = None,
        limit: int = 50,
    ) -> list[WorkflowRun]:
        with self._connect() as conn:
            if state:
                rows = conn.execute(
                    "SELECT * FROM workflows WHERE state = ? "
                    "ORDER BY started_at DESC LIMIT ?",
                    (state, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM workflows ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_workflow(row) for row in rows]

    def list_steps(self, run_id: str) -> list[StepRun]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM steps WHERE workflow_run_id = ? "
                "ORDER BY cursor_index",
                (run_id,),
            ).fetchall()
        return [self._row_to_step(row) for row in rows]

    def advance_cursor(self, run_id: str, *, to_state: WorkflowState | None = None) -> WorkflowRun:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cursor FROM workflows WHERE workflow_run_id = ?",
                (run_id,),
            ).fetchone()
            if not row:
                raise KeyError(run_id)
            new_cursor = int(row["cursor"]) + 1
            state_val = (to_state.value if to_state else None)
            if state_val:
                conn.execute(
                    "UPDATE workflows SET cursor = ?, state = ? "
                    "WHERE workflow_run_id = ?",
                    (new_cursor, state_val, run_id),
                )
            else:
                conn.execute(
                    "UPDATE workflows SET cursor = ? WHERE workflow_run_id = ?",
                    (new_cursor, run_id),
                )
            conn.commit()
        return self.get_workflow(run_id)

    def suspend(self, run_id: str, reason: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE workflows SET state = ?, suspended_at = ?, "
                "last_error = ? WHERE workflow_run_id = ?",
                (WorkflowState.SUSPENDED.value, _utcnow(), reason, run_id),
            )
            conn.commit()

    def resume(self, run_id: str) -> WorkflowRun:
        with self._connect() as conn:
            conn.execute(
                "UPDATE workflows SET state = ?, suspended_at = NULL "
                "WHERE workflow_run_id = ?",
                (WorkflowState.RUNNING.value, run_id),
            )
            conn.commit()
        return self.get_workflow(run_id)

    def complete(self, run_id: str, *, state: WorkflowState, error: str = "") -> WorkflowRun:
        with self._connect() as conn:
            conn.execute(
                "UPDATE workflows SET state = ?, completed_at = ?, last_error = ? "
                "WHERE workflow_run_id = ?",
                (state.value, _utcnow(), error, run_id),
            )
            conn.commit()
        return self.get_workflow(run_id)

    def update_step(
        self,
        step_id: str,
        *,
        state: StepState | None = None,
        artifact: dict[str, Any] | None = None,
        error: str = "",
        increment_attempts: bool = False,
    ) -> StepRun:
        with self._connect() as conn:
            sets: list[str] = []
            params: list[Any] = []
            if state:
                sets.append("state = ?")
                params.append(state.value)
                if state == StepState.RUNNING:
                    sets.append("started_at = ?")
                    params.append(_utcnow())
                elif state in (StepState.DONE, StepState.FAILED,
                               StepState.COMPENSATED):
                    sets.append("ended_at = ?")
                    params.append(_utcnow())
            if artifact is not None:
                sets.append("artifact = ?")
                params.append(json.dumps(artifact, ensure_ascii=False))
            if error:
                sets.append("error = ?")
                params.append(error)
            if increment_attempts:
                sets.append("attempts = attempts + 1")
            if not sets:
                return self.get_step(step_id)
            params.append(step_id)
            conn.execute(
                f"UPDATE steps SET {', '.join(sets)} WHERE step_id = ?",
                params,
            )
            conn.commit()
        return self.get_step(step_id)

    def get_step(self, step_id: str) -> StepRun:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM steps WHERE step_id = ?", (step_id,),
            ).fetchone()
        if not row:
            raise KeyError(step_id)
        return self._row_to_step(row)

    # ---- Signal + Query (v0.18-G) -----------------------------------

    def send_signal(self, run_id: str, name: str, payload: dict[str, Any]) -> str:
        """Push an external event into a (possibly-suspended) workflow.
        Returns the signal_id; consumption happens when the kernel
        next picks the workflow up."""

        signal_id = _new_id("sig")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO signals (signal_id, workflow_run_id, name, "
                "payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (signal_id, run_id, name,
                 json.dumps(payload, ensure_ascii=False), _utcnow()),
            )
            conn.commit()
        return signal_id

    def consume_signals(self, run_id: str) -> list[dict[str, Any]]:
        """Atomic claim of all unconsumed signals for a workflow.
        Marks them as consumed in the same transaction."""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT signal_id, name, payload FROM signals "
                "WHERE workflow_run_id = ? AND consumed_at IS NULL "
                "ORDER BY created_at",
                (run_id,),
            ).fetchall()
            now = _utcnow()
            for row in rows:
                conn.execute(
                    "UPDATE signals SET consumed_at = ? WHERE signal_id = ?",
                    (now, row["signal_id"]),
                )
            conn.commit()
        return [
            {"signal_id": row["signal_id"],
             "name": row["name"],
             "payload": json.loads(row["payload"]) if row["payload"] else {}}
            for row in rows
        ]

    def serve_query(
        self,
        run_id: str,
        name: str,
        *,
        responder: Callable[[WorkflowRun, list[StepRun]], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Sync read of workflow state.  Does NOT advance the workflow
        or write an event;  returns whatever `responder` produces (or
        the default state snapshot when responder=None)."""

        wf = self.get_workflow(run_id)
        steps = self.list_steps(run_id)
        if responder is not None:
            response = responder(wf, steps)
        else:
            response = {
                "workflow_run_id": run_id,
                "state": wf.state.value if isinstance(wf.state, WorkflowState) else wf.state,
                "cursor": wf.cursor,
                "total_steps": len(steps),
                "steps_done": sum(1 for s in steps if s.state == StepState.DONE),
                "trace_id": wf.trace_id,
                "name": name,
            }
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO queries (workflow_run_id, name, response, served_at) "
                "VALUES (?, ?, ?, ?)",
                (run_id, name,
                 json.dumps(response, ensure_ascii=False),
                 _utcnow()),
            )
            conn.commit()
        return response

    # ---- internals --------------------------------------------------

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA busy_timeout = 30000;

                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_run_id TEXT PRIMARY KEY,
                    template_name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    cursor INTEGER NOT NULL DEFAULT 0,
                    trace_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    suspended_at TEXT,
                    completed_at TEXT,
                    last_error TEXT DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_workflows_state ON workflows(state);
                CREATE INDEX IF NOT EXISTS idx_workflows_trace ON workflows(trace_id);

                CREATE TABLE IF NOT EXISTS steps (
                    step_id TEXT PRIMARY KEY,
                    workflow_run_id TEXT NOT NULL,
                    op_name TEXT NOT NULL,
                    cursor_index INTEGER NOT NULL,
                    inputs TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_epoch INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT,
                    ended_at TEXT,
                    artifact TEXT,
                    error TEXT DEFAULT '',
                    FOREIGN KEY(workflow_run_id) REFERENCES workflows(workflow_run_id)
                );

                CREATE INDEX IF NOT EXISTS idx_steps_workflow ON steps(workflow_run_id, cursor_index);

                CREATE TABLE IF NOT EXISTS signals (
                    signal_id TEXT PRIMARY KEY,
                    workflow_run_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    consumed_at TEXT,
                    FOREIGN KEY(workflow_run_id) REFERENCES workflows(workflow_run_id)
                );

                CREATE INDEX IF NOT EXISTS idx_signals_pending
                    ON signals(workflow_run_id, consumed_at);

                CREATE TABLE IF NOT EXISTS queries (
                    query_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_run_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    response TEXT NOT NULL,
                    served_at TEXT NOT NULL,
                    FOREIGN KEY(workflow_run_id) REFERENCES workflows(workflow_run_id)
                );
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        from ._storage import connect_sqlite_store
        return connect_sqlite_store(self.db_path)

    def _row_to_workflow(self, row: sqlite3.Row) -> WorkflowRun:
        return WorkflowRun(
            workflow_run_id=row["workflow_run_id"],
            template_name=row["template_name"],
            state=WorkflowState(row["state"]),
            cursor=int(row["cursor"]),
            trace_id=row["trace_id"],
            started_at=row["started_at"],
            suspended_at=row["suspended_at"],
            completed_at=row["completed_at"],
            last_error=row["last_error"] or "",
        )

    def _row_to_step(self, row: sqlite3.Row) -> StepRun:
        inputs = json.loads(row["inputs"]) if row["inputs"] else {}
        artifact = json.loads(row["artifact"]) if row["artifact"] else None
        return StepRun(
            step_id=row["step_id"],
            workflow_run_id=row["workflow_run_id"],
            op_name=row["op_name"],
            cursor_index=int(row["cursor_index"]),
            inputs=inputs,
            state=StepState(row["state"]),
            attempts=int(row["attempts"]),
            lease_epoch=int(row["lease_epoch"]),
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            artifact=artifact,
            error=row["error"] or "",
        )
