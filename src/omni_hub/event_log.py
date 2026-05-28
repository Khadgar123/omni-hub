"""Hash-chained, append-only event log per task (OpenHands V1 / Hermes pattern).

The :class:`AuditLogger` records *decisions* — policy evaluations, operation
starts/successes/failures.  This module records *agent state* — every step
a worker takes while processing a task: claim, adapter start, intermediate
tool uses, the produced artifact, errors.  Together they give:

* a tamper-evident audit trail (every event embeds the hash of the
  previous event in the same file → any deletion or reorder breaks the
  chain)
* deterministic replay: ``replay(task_id)`` walks the file in append
  order, reconstructing what the worker did.

Storage layout (one file per task):

    .omni/events/task-<id>.jsonl

Each line is one ``Event.to_dict()``.  Files are append-only — we never
rewrite.  Genesis ``prev_hash`` is 64 zeros so the first entry is
self-consistent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from ._storage import safe_workspace_path


GENESIS_HASH = "0" * 64

# Canonical event kinds.  Custom kinds are allowed (string), but workers
# should prefer these when one fits.
KIND_TASK_CREATED = "task.created"
KIND_TASK_CLAIMED = "task.claimed"
KIND_TASK_COMPLETED = "task.completed"
KIND_TASK_FAILED = "task.failed"
KIND_TASK_LEASE_LOST = "task.lease_lost"
KIND_WORKER_ADAPTER_START = "worker.adapter_start"
KIND_WORKER_ADAPTER_DONE = "worker.adapter_done"
KIND_WORKER_ERROR = "worker.error"
KIND_WORKER_TOOL_USE = "worker.tool_use"
KIND_WORKER_TOOL_RESULT = "worker.tool_result"
KIND_PROPOSAL_STAGED = "proposal.staged"
KIND_PROPOSAL_REJECTED_BY_WORKER = "proposal.rejected_by_worker"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _content_hash(prev_hash: str, payload_json: str) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("ascii"))
    h.update(b"|")
    h.update(payload_json.encode("utf-8"))
    return h.hexdigest()


@dataclass(slots=True)
class Event:
    """One immutable step in a task's life.

    ``data`` carries the kind-specific payload (worker_id, artifact_id,
    error message, etc.).  ``prev_hash`` and ``content_hash`` form the
    tamper-evidence chain.
    """

    event_id: str = field(default_factory=lambda: str(uuid4()))
    task_id: int = 0
    kind: str = ""
    timestamp: str = field(default_factory=_utcnow_iso)
    prev_hash: str = GENESIS_HASH
    content_hash: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def canonical_payload(self) -> str:
        """JSON used as the hash input — stable key order, no whitespace."""
        return json.dumps(
            {
                "event_id": self.event_id,
                "task_id": self.task_id,
                "kind": self.kind,
                "timestamp": self.timestamp,
                "data": self.data,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )


class EventLog:
    """One ``.omni/events/task-<id>.jsonl`` per task, plus global.jsonl for
    system events not tied to a single task.
    """

    def __init__(
        self,
        workspace: Path | str = ".",
        event_dir: str = ".omni/events",
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.event_dir = safe_workspace_path(self.workspace, event_dir)

    # ------- public API ----------------------------------------------------

    def append(
        self,
        kind: str,
        *,
        task_id: int = 0,
        data: dict[str, Any] | None = None,
    ) -> Event:
        path = self._path_for(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        prev_hash = self._last_hash(path)

        event = Event(
            task_id=task_id,
            kind=kind,
            prev_hash=prev_hash,
            data=dict(data or {}),
        )
        event.content_hash = _content_hash(prev_hash, event.canonical_payload())

        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        return event

    def replay(self, task_id: int) -> Iterator[Event]:
        """Walk events for ``task_id`` in append order.

        Caller may pass ``task_id=0`` to walk the global stream instead.
        Yields :class:`Event` instances.  Does NOT verify the hash chain
        — call :meth:`verify_chain` for that.
        """

        path = self._path_for(task_id)
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                yield Event(
                    event_id=str(raw.get("event_id") or str(uuid4())),
                    task_id=int(raw.get("task_id", 0)),
                    kind=str(raw.get("kind", "")),
                    timestamp=str(raw.get("timestamp") or _utcnow_iso()),
                    prev_hash=str(raw.get("prev_hash") or GENESIS_HASH),
                    content_hash=str(raw.get("content_hash", "")),
                    data=dict(raw.get("data", {})),
                )

    def verify_chain(self, task_id: int) -> tuple[bool, list[str]]:
        """Walk the file and confirm every event's ``content_hash`` matches.

        Returns ``(ok, errors)``.  Errors are human-readable strings naming
        which event_id broke the chain (deletion / reorder / forgery).
        """

        errors: list[str] = []
        prev = GENESIS_HASH
        for event in self.replay(task_id):
            if event.prev_hash != prev:
                errors.append(
                    f"event {event.event_id} prev_hash mismatch "
                    f"(expected {prev[:8]}…, saw {event.prev_hash[:8]}…)"
                )
            expected = _content_hash(prev, event.canonical_payload())
            if event.content_hash != expected:
                errors.append(
                    f"event {event.event_id} content_hash forged "
                    f"(expected {expected[:8]}…, saw {event.content_hash[:8]}…)"
                )
            prev = event.content_hash
        return (not errors), errors

    def list_tasks(self) -> list[int]:
        """Task ids that have at least one event."""

        if not self.event_dir.exists():
            return []
        ids: list[int] = []
        for path in sorted(self.event_dir.glob("task-*.jsonl")):
            stem = path.stem  # "task-42"
            try:
                ids.append(int(stem.split("-", 1)[1]))
            except (IndexError, ValueError):
                continue
        return ids

    # ------- internals -----------------------------------------------------

    def _path_for(self, task_id: int) -> Path:
        if task_id <= 0:
            return self.event_dir / "global.jsonl"
        return self.event_dir / f"task-{task_id}.jsonl"

    def _last_hash(self, path: Path) -> str:
        """Return the ``content_hash`` of the last event in ``path``,
        or :data:`GENESIS_HASH` if the file is empty / missing."""

        if not path.exists():
            return GENESIS_HASH
        last = ""
        # Linear scan — files are small (<10k events per task in practice)
        # and the alternative (seeking from the end + line-walking) is
        # fragile on partially-written final lines.
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = line
        if not last:
            return GENESIS_HASH
        try:
            return str(json.loads(last)["content_hash"]) or GENESIS_HASH
        except (json.JSONDecodeError, KeyError):
            return GENESIS_HASH
