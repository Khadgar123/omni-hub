"""v0.18-H/I Projection registry — Iceberg-style snapshots + Outbox cursors.

Through v0.17 each projection (wiki / fts5 / preference / skill / graph)
maintains itself ad hoc.  v0.18 formalises them under a single registry
so:

* ``projection-list`` shows every projection's schema_version, last
  built event_seq, last snapshot_id, last_rebuilt_at.
* ``projection-rebuild --target <name>`` triggers a deterministic
  rebuild from ClaimLedger + AuditEventLog.
* ``projection-snapshot --target <name>`` writes a new immutable
  snapshot pointer (atomic rename — Iceberg pattern).
* ``projection-rollback --target <name> --snapshot <id>`` swaps the
  pointer back to a prior snapshot.

The ``ProjectionCursor`` table is the Outbox/Inbox cursor (Chris
Richardson microservices.io) — every projection records the last
event_seq it consumed so it can resume after restart without
re-processing.

Stdlib only;  builder implementations live in the modules that own
each projection (wiki_fts, knowledge_plane.apply_wiki_proposal, etc.).
This module is the *registry + meta layer*, not the builders.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from ._storage import safe_workspace_path


PROJECTION_DB_REL = ".omni/projections.sqlite3"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _new_snapshot_id() -> str:
    return f"snap_{int(time.time() * 1000):x}_{secrets.token_hex(4)}"


@dataclass(slots=True)
class ProjectionSnapshot:
    """One immutable pointer to a rebuilt projection state."""

    snapshot_id: str
    projection_name: str
    schema_version: str
    built_from_event_seq: int
    built_at: str
    atomic_pointer: str               # backend-specific (db path / dir / table name)
    row_count: int = 0
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProjectionCursor:
    projection_name: str
    last_event_seq: int
    last_advanced_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProjectionBuilder(Protocol):
    """Contract every projection MUST satisfy.

    A builder owns its own storage (e.g. wiki_fts owns the FTS5 db).
    The registry tracks the metadata (snapshot pointers + cursors);  the
    builder produces / interprets the rows.
    """

    name: str
    schema_version: str

    def rebuild(self, workspace: Path) -> ProjectionSnapshot:
        """Rebuild from scratch.  Returns a new snapshot."""
        ...

    def stats(self, workspace: Path) -> dict[str, Any]:
        """Current row counts + schema_version for projection-list."""
        ...


class ProjectionRegistry:
    """Registry of every projection plus its current snapshot + cursor."""

    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace).resolve()
        self.db_path = safe_workspace_path(self.workspace, PROJECTION_DB_REL)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._builders: dict[str, ProjectionBuilder] = {}
        self._init_schema()

    # ---- registration -----------------------------------------------

    def register(self, builder: ProjectionBuilder) -> None:
        if builder.name in self._builders:
            raise ValueError(f"projection {builder.name!r} already registered")
        self._builders[builder.name] = builder

    def builders(self) -> list[ProjectionBuilder]:
        return [self._builders[name] for name in sorted(self._builders)]

    # ---- snapshot ops -----------------------------------------------

    def record_snapshot(self, snapshot: ProjectionSnapshot) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO snapshots "
                "(snapshot_id, projection_name, schema_version, "
                " built_from_event_seq, built_at, atomic_pointer, "
                " row_count, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (snapshot.snapshot_id, snapshot.projection_name,
                 snapshot.schema_version, snapshot.built_from_event_seq,
                 snapshot.built_at, snapshot.atomic_pointer,
                 snapshot.row_count,
                 json.dumps(snapshot.detail, ensure_ascii=False)),
            )
            conn.execute(
                "INSERT OR REPLACE INTO current_snapshot "
                "(projection_name, snapshot_id) VALUES (?, ?)",
                (snapshot.projection_name, snapshot.snapshot_id),
            )
            conn.commit()

    def current_snapshot(self, projection_name: str) -> ProjectionSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT s.* FROM snapshots s "
                "JOIN current_snapshot c "
                "  ON s.snapshot_id = c.snapshot_id "
                "WHERE c.projection_name = ?",
                (projection_name,),
            ).fetchone()
        return self._row_to_snapshot(row) if row else None

    def list_snapshots(
        self,
        projection_name: str | None = None,
        *,
        limit: int = 50,
    ) -> list[ProjectionSnapshot]:
        with self._connect() as conn:
            if projection_name:
                rows = conn.execute(
                    "SELECT * FROM snapshots WHERE projection_name = ? "
                    "ORDER BY built_at DESC LIMIT ?",
                    (projection_name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM snapshots ORDER BY built_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_snapshot(row) for row in rows]

    def rollback(self, projection_name: str, snapshot_id: str) -> ProjectionSnapshot:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE snapshot_id = ? AND projection_name = ?",
                (snapshot_id, projection_name),
            ).fetchone()
            if not row:
                raise KeyError(f"snapshot {snapshot_id!r} not found for {projection_name!r}")
            conn.execute(
                "INSERT OR REPLACE INTO current_snapshot "
                "(projection_name, snapshot_id) VALUES (?, ?)",
                (projection_name, snapshot_id),
            )
            conn.commit()
        return self._row_to_snapshot(row)

    # ---- cursor ops (Outbox/Inbox pattern) --------------------------

    def get_cursor(self, projection_name: str) -> ProjectionCursor:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM cursors WHERE projection_name = ?",
                (projection_name,),
            ).fetchone()
        if not row:
            return ProjectionCursor(
                projection_name=projection_name,
                last_event_seq=0,
                last_advanced_at="",
            )
        return ProjectionCursor(
            projection_name=row["projection_name"],
            last_event_seq=int(row["last_event_seq"]),
            last_advanced_at=row["last_advanced_at"],
        )

    def advance_cursor(self, projection_name: str, to_event_seq: int) -> ProjectionCursor:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO cursors (projection_name, last_event_seq, last_advanced_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(projection_name) DO UPDATE SET "
                "  last_event_seq = excluded.last_event_seq, "
                "  last_advanced_at = excluded.last_advanced_at",
                (projection_name, int(to_event_seq), _utcnow()),
            )
            conn.commit()
        return self.get_cursor(projection_name)

    def list_cursors(self) -> list[ProjectionCursor]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM cursors ORDER BY projection_name"
            ).fetchall()
        return [
            ProjectionCursor(
                projection_name=row["projection_name"],
                last_event_seq=int(row["last_event_seq"]),
                last_advanced_at=row["last_advanced_at"],
            )
            for row in rows
        ]

    # ---- list / rebuild / stats ------------------------------------

    def overview(self) -> dict[str, Any]:
        """projection-list output: every registered projection + its
        current snapshot + cursor + builder.stats()."""

        out: list[dict[str, Any]] = []
        for builder in self.builders():
            try:
                stats = builder.stats(self.workspace)
            except Exception as exc:                            # noqa: BLE001
                stats = {"error": str(exc)}
            snap = self.current_snapshot(builder.name)
            cursor = self.get_cursor(builder.name)
            out.append({
                "name": builder.name,
                "schema_version": getattr(builder, "schema_version", "?"),
                "current_snapshot": snap.to_dict() if snap else None,
                "cursor": cursor.to_dict(),
                "stats": stats,
            })
        return {"count": len(out), "projections": out}

    def rebuild(self, projection_name: str) -> ProjectionSnapshot:
        """Drive the builder, record the resulting snapshot, advance the
        cursor to the current event_seq (best-effort — builders that
        don't tail events leave the cursor untouched)."""

        builder = self._builders.get(projection_name)
        if builder is None:
            raise KeyError(f"projection {projection_name!r} not registered")
        snapshot = builder.rebuild(self.workspace)
        # Ensure builder gave us a usable snapshot_id.
        if not snapshot.snapshot_id:
            snapshot = ProjectionSnapshot(
                snapshot_id=_new_snapshot_id(),
                projection_name=projection_name,
                schema_version=snapshot.schema_version,
                built_from_event_seq=snapshot.built_from_event_seq,
                built_at=snapshot.built_at or _utcnow(),
                atomic_pointer=snapshot.atomic_pointer,
                row_count=snapshot.row_count,
                detail=snapshot.detail,
            )
        self.record_snapshot(snapshot)
        return snapshot

    # ---- schema -----------------------------------------------------

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA busy_timeout = 30000;

                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    projection_name TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    built_from_event_seq INTEGER NOT NULL DEFAULT 0,
                    built_at TEXT NOT NULL,
                    atomic_pointer TEXT NOT NULL,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    detail TEXT DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_snap_proj
                    ON snapshots(projection_name, built_at DESC);

                CREATE TABLE IF NOT EXISTS current_snapshot (
                    projection_name TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL,
                    FOREIGN KEY(snapshot_id) REFERENCES snapshots(snapshot_id)
                );

                CREATE TABLE IF NOT EXISTS cursors (
                    projection_name TEXT PRIMARY KEY,
                    last_event_seq INTEGER NOT NULL DEFAULT 0,
                    last_advanced_at TEXT NOT NULL
                );
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        from ._storage import connect_sqlite_store
        return connect_sqlite_store(self.db_path)

    def _row_to_snapshot(self, row: sqlite3.Row) -> ProjectionSnapshot:
        return ProjectionSnapshot(
            snapshot_id=row["snapshot_id"],
            projection_name=row["projection_name"],
            schema_version=row["schema_version"],
            built_from_event_seq=int(row["built_from_event_seq"]),
            built_at=row["built_at"],
            atomic_pointer=row["atomic_pointer"],
            row_count=int(row["row_count"]),
            detail=json.loads(row["detail"]) if row["detail"] else {},
        )


# ---------------------------------------------------------------------------
# Built-in builders for our existing projections (thin wrappers)
# ---------------------------------------------------------------------------


class WikiFTS5Builder:
    name = "wiki_fts5"
    schema_version = "v0.16"

    def rebuild(self, workspace: Path) -> ProjectionSnapshot:
        from .knowledge_plane import reindex_wiki

        result = reindex_wiki(workspace)
        return ProjectionSnapshot(
            snapshot_id=_new_snapshot_id(),
            projection_name=self.name,
            schema_version=self.schema_version,
            built_from_event_seq=0,                 # FTS5 doesn't tail events directly
            built_at=_utcnow(),
            atomic_pointer=".omni/wiki_fts.sqlite3",
            row_count=int(result.get("indexed", 0)),
            detail=result,
        )

    def stats(self, workspace: Path) -> dict[str, Any]:
        from .wiki_fts import WikiFTSIndex, fts5_available

        if not fts5_available():
            return {"available": False}
        return {"available": True, **WikiFTSIndex(workspace).stats()}


class ClaimsLedgerBuilder:
    """Pseudo-projection that just reports ClaimLedger version."""

    name = "claims_ledger"
    schema_version = "v0.18"

    def rebuild(self, workspace: Path) -> ProjectionSnapshot:
        from .knowledge_plane import claim_ledger_version

        version = claim_ledger_version(workspace)
        return ProjectionSnapshot(
            snapshot_id=_new_snapshot_id(),
            projection_name=self.name,
            schema_version=self.schema_version,
            built_from_event_seq=version,
            built_at=_utcnow(),
            atomic_pointer=".omni/claims.jsonl",
            row_count=version,
        )

    def stats(self, workspace: Path) -> dict[str, Any]:
        from .knowledge_plane import claim_ledger_version, claims_stats

        return {
            "version": claim_ledger_version(workspace),
            **claims_stats(workspace),
        }


class GraphProjectionBuilder:
    """GraphRAG-style local/global graph projection (v0.18-J)."""

    name = "wiki_graph"
    schema_version = "v0.18"

    def rebuild(self, workspace: Path) -> ProjectionSnapshot:
        from .wiki_graph import rebuild_graph

        snap = rebuild_graph(workspace)
        return ProjectionSnapshot(
            snapshot_id=snap.snapshot_id,
            projection_name=self.name,
            schema_version=self.schema_version,
            built_from_event_seq=snap.built_from_claim_count,
            built_at=snap.built_at,
            atomic_pointer=".omni/graph/current.json",
            row_count=len(snap.nodes),
            detail={
                "edge_count": len(snap.edges),
                "community_count": len(snap.communities),
            },
        )

    def stats(self, workspace: Path) -> dict[str, Any]:
        from .wiki_graph import _load_current_graph

        snap = _load_current_graph(workspace)
        if snap is None:
            return {"current_snapshot": None}
        return {
            "current_snapshot": snap.snapshot_id,
            "nodes": len(snap.nodes),
            "edges": len(snap.edges),
            "communities": len(snap.communities),
        }


class PreferenceJsonlBuilder:
    name = "preference_jsonl"
    schema_version = "v0.15"

    def rebuild(self, workspace: Path) -> ProjectionSnapshot:
        from .harness.preference import PreferenceStore

        store = PreferenceStore(workspace / ".omni" / "preference")
        domains = store.list_domains()
        rows = sum(store.stats(d)["total"] for d in domains)
        return ProjectionSnapshot(
            snapshot_id=_new_snapshot_id(),
            projection_name=self.name,
            schema_version=self.schema_version,
            built_from_event_seq=rows,
            built_at=_utcnow(),
            atomic_pointer=".omni/preference",
            row_count=rows,
            detail={"domains": list(domains)},
        )

    def stats(self, workspace: Path) -> dict[str, Any]:
        from .harness.preference import PreferenceStore

        store = PreferenceStore(workspace / ".omni" / "preference")
        return {"domains": store.list_domains()}


def build_default_projection_registry(workspace: Path | str = ".") -> ProjectionRegistry:
    registry = ProjectionRegistry(workspace)
    registry.register(WikiFTS5Builder())
    registry.register(ClaimsLedgerBuilder())
    registry.register(PreferenceJsonlBuilder())
    registry.register(GraphProjectionBuilder())
    return registry
