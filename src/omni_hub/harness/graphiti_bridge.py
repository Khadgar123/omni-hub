"""Graphiti bridge with graceful fallback.

If the ``graphiti-core`` package is importable we hand off; otherwise we read
from the local ``omni_hub.memory.MemoryStore`` SQLite database which already
indexes documents/entities/relations.  The harness loop never blocks on
Graphiti being available.

Note: the public surface here is intentionally tiny — a knowledge query and a
list of document records — because Phase-1 of the harness only needs
provenance + duplicate detection.  Full temporal graph traversal arrives once
the Graphiti fork is wired and we add a real adapter.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator

from ..memory import MemoryStore


@dataclass(slots=True)
class KnowledgeRecord:
    """One document/entity row regardless of backend."""

    source_path: str
    title: str
    summary: str = ""
    updated_at: str = ""
    backend: str = "local"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class KnowledgeQueryResult:
    backend: str
    records: list[KnowledgeRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "records": [r.to_dict() for r in self.records],
        }


def _graphiti_available() -> bool:
    try:
        import graphiti_core  # type: ignore[import-not-found]  # noqa: F401
        return True
    except Exception:
        return False


class KnowledgeBackend:
    """Common interface; subclasses implement the actual backend."""

    name: str = "abstract"

    def list_documents(self, *, limit: int = 100) -> list[KnowledgeRecord]:  # pragma: no cover
        raise NotImplementedError

    def search(self, query: str, *, limit: int = 20) -> list[KnowledgeRecord]:  # pragma: no cover
        raise NotImplementedError


class LocalSQLiteBackend(KnowledgeBackend):
    """Thin document view over ``.omni/memory.sqlite3`` — delegates to ``MemoryStore``."""

    name = "local-sqlite"

    def __init__(self, db_path: Path | str = ".omni/memory.sqlite3") -> None:
        self.db_path = Path(db_path)

    def _store(self) -> MemoryStore:
        path = self.db_path
        if path.is_absolute():
            workspace = path.parent
            relative = path.name
        else:
            workspace = Path(".")
            relative = str(path)
        return MemoryStore(workspace=workspace, db_path=relative, create=False)

    def _record(self, row: dict[str, str]) -> KnowledgeRecord:
        return KnowledgeRecord(
            source_path=row["source_path"],
            title=row["title"],
            summary=row["summary"],
            updated_at=row["updated_at"],
            backend=self.name,
        )

    def list_documents(self, *, limit: int = 100) -> list[KnowledgeRecord]:
        return [self._record(row) for row in self._store().list_documents(limit=limit)]

    def search(self, query: str, *, limit: int = 20) -> list[KnowledgeRecord]:
        return [
            self._record(row)
            for row in self._store().search_documents(query, limit=limit)
        ]


class GraphitiBackend(KnowledgeBackend):  # pragma: no cover — exercised once installed
    """Real Graphiti adapter; stub until we pin the fork."""

    name = "graphiti"

    def __init__(self) -> None:
        try:
            import graphiti_core  # type: ignore[import-not-found]  # noqa: F401
        except Exception as exc:
            raise RuntimeError("graphiti_core not installed") from exc

    def list_documents(self, *, limit: int = 100) -> list[KnowledgeRecord]:
        # TODO: real Graphiti query once fork pinned
        return []

    def search(self, query: str, *, limit: int = 20) -> list[KnowledgeRecord]:
        # TODO: temporal graph traversal
        return []


def get_backend(
    *,
    prefer: str = "auto",
    db_path: Path | str = ".omni/memory.sqlite3",
) -> KnowledgeBackend:
    """Resolve a backend.  ``prefer`` is ``"auto"`` (graphiti if available,
    else local), ``"graphiti"`` to require it, or ``"local"`` to force the
    SQLite fallback."""

    if prefer == "graphiti":
        return GraphitiBackend()
    if prefer == "auto" and _graphiti_available():
        return GraphitiBackend()
    return LocalSQLiteBackend(db_path)


def query(
    text: str,
    *,
    prefer_backend: str = "auto",
    db_path: Path | str = ".omni/memory.sqlite3",
    limit: int = 20,
) -> KnowledgeQueryResult:
    backend = get_backend(prefer=prefer_backend, db_path=db_path)
    records = backend.search(text, limit=limit)
    return KnowledgeQueryResult(backend=backend.name, records=records)


def iter_all_documents(
    *,
    prefer_backend: str = "auto",
    db_path: Path | str = ".omni/memory.sqlite3",
    page_size: int = 200,
) -> Iterator[KnowledgeRecord]:
    """Stream all documents.  Used by redundancy scan."""

    backend = get_backend(prefer=prefer_backend, db_path=db_path)
    # Local backend uses LIMIT-only pagination; safe over small personal corpora.
    seen: set[str] = set()
    while True:
        batch = backend.list_documents(limit=page_size + len(seen))
        new = [r for r in batch if r.source_path not in seen]
        if not new:
            return
        for record in new:
            seen.add(record.source_path)
            yield record
        if len(new) < page_size:
            return
