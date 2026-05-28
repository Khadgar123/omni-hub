from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .proposals import EntityProposal, Proposal, RelationProposal


@dataclass(slots=True)
class MemorySearchResult:
    result_type: str
    title: str
    snippet: str
    score: float
    source_path: str = ""

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["score"] = round(self.score, 4)
        return data


@dataclass(slots=True)
class MemoryDigestResult:
    proposal_id: str
    source_path: str
    document_count: int
    entity_count: int
    relation_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class MemoryStore:
    def __init__(
        self,
        workspace: Path | str = ".",
        db_path: str = ".omni/memory.sqlite3",
        *,
        create: bool = True,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.db_path = self._safe_path(db_path)
        if create:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()

    def digest_proposal(self, proposal: Proposal) -> MemoryDigestResult:
        if proposal.kind != "knowledge":
            raise ValueError(
                f"digest_proposal only handles kind='knowledge'; got kind={proposal.kind!r}"
            )
        entities = [
            EntityProposal.from_dict(item)
            for item in proposal.payload.get("entities", [])
            if isinstance(item, dict)
        ]
        relations = [
            RelationProposal.from_dict(item)
            for item in proposal.payload.get("relations", [])
            if isinstance(item, dict)
        ]

        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO documents (source_path, title, summary, proposal_id, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_path) DO UPDATE SET
                    title = excluded.title,
                    summary = excluded.summary,
                    proposal_id = excluded.proposal_id,
                    updated_at = excluded.updated_at
                """,
                (
                    proposal.source_path,
                    proposal.title,
                    proposal.summary,
                    proposal.proposal_id,
                    now,
                ),
            )

            self._upsert_entity(
                conn,
                proposal.title,
                "topic",
                "document title",
                0.8,
                now,
            )

            for entity in entities:
                self._upsert_entity(
                    conn,
                    entity.name,
                    entity.kind,
                    entity.evidence,
                    entity.confidence,
                    now,
                )

            for relation in relations:
                conn.execute(
                    """
                    INSERT INTO relations (
                        source, relation, target, evidence, confidence,
                        source_path, proposal_id, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, relation, target, source_path) DO UPDATE SET
                        evidence = excluded.evidence,
                        confidence = excluded.confidence,
                        proposal_id = excluded.proposal_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        relation.source,
                        relation.relation,
                        relation.target,
                        relation.evidence,
                        relation.confidence,
                        proposal.source_path,
                        proposal.proposal_id,
                        now,
                    ),
                )

            conn.commit()
            stats = self.stats(conn)

        return MemoryDigestResult(
            proposal_id=proposal.proposal_id,
            source_path=proposal.source_path,
            document_count=stats["documents"],
            entity_count=stats["entities"],
            relation_count=stats["relations"],
        )

    def search(self, query: str, *, limit: int = 10) -> list[MemorySearchResult]:
        normalized_query = query.strip()
        if not normalized_query:
            return []
        if not self.db_path.exists():
            return []

        terms = _query_terms(normalized_query)
        results: list[MemorySearchResult] = []

        with self._connect() as conn:
            documents = conn.execute(
                "SELECT source_path, title, summary FROM documents"
            ).fetchall()
            for row in documents:
                haystack = f"{row['title']} {row['summary']}"
                score = _score(haystack, terms)
                if score > 0:
                    results.append(
                        MemorySearchResult(
                            result_type="document",
                            title=row["title"],
                            snippet=row["summary"],
                            score=score,
                            source_path=row["source_path"],
                        )
                    )

            entities = conn.execute(
                "SELECT name, kind, evidence, confidence FROM entities"
            ).fetchall()
            matched_entities: set[str] = set()
            for row in entities:
                haystack = f"{row['name']} {row['kind']} {row['evidence']}"
                score = _score(haystack, terms)
                if score > 0:
                    matched_entities.add(row["name"])
                    results.append(
                        MemorySearchResult(
                            result_type="entity",
                            title=row["name"],
                            snippet=f"{row['kind']}: {row['evidence']}",
                            score=score + float(row["confidence"]),
                        )
                    )

            for entity_name in matched_entities:
                relation_rows = conn.execute(
                    """
                    SELECT source, relation, target, evidence, confidence, source_path
                    FROM relations
                    WHERE source = ? OR target = ?
                    """,
                    (entity_name, entity_name),
                ).fetchall()
                for row in relation_rows:
                    results.append(
                        MemorySearchResult(
                            result_type="relation",
                            title=f"{row['source']} --{row['relation']}--> {row['target']}",
                            snippet=row["evidence"],
                            score=0.5 + float(row["confidence"]),
                            source_path=row["source_path"],
                        )
                    )

        results.sort(key=lambda result: (-result.score, result.title))
        return results[:limit]

    def list_documents(self, *, limit: int = 100) -> list[dict[str, str]]:
        """Document-only view ordered by updated_at DESC.

        Safe when the database is missing or contains only the ``documents``
        table; callers that need entities/relations should use ``search``.
        """
        if not self.db_path.exists():
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT source_path, title, summary, updated_at "
                    "FROM documents ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [
            {
                "source_path": row["source_path"] or "",
                "title": row["title"] or "",
                "summary": row["summary"] or "",
                "updated_at": row["updated_at"] or "",
            }
            for row in rows
        ]

    def search_documents(self, query: str, *, limit: int = 20) -> list[dict[str, str]]:
        """Substring LIKE search over documents only — for non-scored callers."""
        normalized = query.strip()
        if not normalized or not self.db_path.exists():
            return []
        like = f"%{normalized}%"
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT source_path, title, summary, updated_at FROM documents "
                    "WHERE title LIKE ? OR summary LIKE ? "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (like, like, limit),
                ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [
            {
                "source_path": row["source_path"] or "",
                "title": row["title"] or "",
                "summary": row["summary"] or "",
                "updated_at": row["updated_at"] or "",
            }
            for row in rows
        ]

    def stats(self, conn: sqlite3.Connection | None = None) -> dict[str, int]:
        if conn is None and not self.db_path.exists():
            return {"documents": 0, "entities": 0, "relations": 0}

        owns_connection = conn is None
        active_conn = conn or self._connect()
        try:
            return {
                "documents": active_conn.execute(
                    "SELECT COUNT(*) FROM documents"
                ).fetchone()[0],
                "entities": active_conn.execute(
                    "SELECT COUNT(*) FROM entities"
                ).fetchone()[0],
                "relations": active_conn.execute(
                    "SELECT COUNT(*) FROM relations"
                ).fetchone()[0],
            }
        finally:
            if owns_connection:
                active_conn.close()

    def _upsert_entity(
        self,
        conn: sqlite3.Connection,
        name: str,
        kind: str,
        evidence: str,
        confidence: float,
        updated_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO entities (name, kind, evidence, confidence, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                kind = CASE
                    WHEN excluded.confidence >= entities.confidence THEN excluded.kind
                    ELSE entities.kind
                END,
                evidence = CASE
                    WHEN excluded.confidence >= entities.confidence THEN excluded.evidence
                    ELSE entities.evidence
                END,
                confidence = MAX(entities.confidence, excluded.confidence),
                updated_at = excluded.updated_at
            """,
            (name, kind, evidence, confidence, updated_at),
        )

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA busy_timeout = 30000;

                CREATE TABLE IF NOT EXISTS documents (
                    source_path TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS entities (
                    name TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    target TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_path TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source, relation, target, source_path)
                );

                CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source);
                CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target);
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        # PRAGMA busy_timeout applies per-connection; reader-only paths
        # (search / stats) must also wait through a writer lock.
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _safe_path(self, relative_path: str) -> Path:
        target = (self.workspace / relative_path).resolve()
        try:
            target.relative_to(self.workspace)
        except ValueError as exc:
            raise PermissionError("target path is outside the workspace") from exc
        return target


def _query_terms(query: str) -> list[str]:
    terms = [term.lower() for term in query.split() if term.strip()]
    return terms or [query.lower()]


def _score(text: str, terms: list[str]) -> float:
    lowered = text.lower()
    score = 0.0
    for term in terms:
        if not term:
            continue
        occurrences = lowered.count(term)
        if occurrences:
            score += 1.0 + min(occurrences, 5) * 0.2
    return score
