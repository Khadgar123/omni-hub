"""SQLite TTL cache for retrieval calls.

Pre-empts the MCP 2026-07 spec's protocol-level TTL/ETag primitives:
key = sha256(source + "|" + query + "|" + domain), value = JSON-serialised
record list, expires at per-source TTL (24h openalex / 1h gdelt / 7d
wikipedia / 5min jina / 12h arxiv / 24h s2 / 24h default).

Single SQLite file under ``.omni/retrieval_cache.sqlite3``, shares the
WAL+busy_timeout primitive in ``_storage.connect_sqlite_store``.

Semantic cache (embedding-similarity hits) is NOT implemented here — it
needs a vector dep and violates ``dependencies = []``.  Exact-match TTL
covers ~80% of the wins (repeat queries, schedule-tick periodic crawls).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .._storage import connect_sqlite_store, safe_workspace_path
from .base import RetrievalRecord


# Per-source TTL in seconds.  Tuned to source freshness:
#   * Wikipedia: low churn, weekly fine
#   * OpenAlex / S2 / arxiv: papers don't change after publication
#   * GDELT: 15-min refresh, but queries about "recent X" want hourly
#   * Jina Reader: URL contents can change; 5 min is generous
DEFAULT_TTL_SEC: dict[str, int] = {
    "brave_search":      3600,
    "courtlistener":     7 * 24 * 3600,
    "crossref":          24 * 3600,
    "data_commons":      24 * 3600,
    "europe_pmc":        24 * 3600,
    "internet_archive":  7 * 24 * 3600,
    "wikipedia":         7 * 24 * 3600,
    "wikidata":          7 * 24 * 3600,
    "wikidata_sparql":   7 * 24 * 3600,
    "openalex":          24 * 3600,
    "pubmed":            24 * 3600,
    "semantic_scholar":  24 * 3600,
    "arxiv":             12 * 3600,
    "gdelt":             3600,
    "jina_reader":       300,
    "wayback_cdx":       7 * 24 * 3600,
}
FALLBACK_TTL_SEC = 6 * 3600


@dataclass(slots=True)
class _CacheKey:
    source: str
    query: str
    domain: str

    def hash(self) -> str:
        raw = f"{self.source}|{self.query.strip().lower()}|{self.domain}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:32]


class TTLCache:
    """SQLite-backed exact-match TTL cache for ``(source, query, domain)``."""

    def __init__(
        self,
        workspace: Path | str = ".",
        db_path: str = ".omni/retrieval_cache.sqlite3",
        *,
        create: bool = True,
        ttl_overrides: dict[str, int] | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.db_path = safe_workspace_path(self.workspace, db_path)
        self.ttl = dict(DEFAULT_TTL_SEC)
        if ttl_overrides:
            self.ttl.update(ttl_overrides)
        if create:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()

    # ------- public API ----------------------------------------------------

    def ttl_for(self, source: str) -> int:
        return self.ttl.get(source, FALLBACK_TTL_SEC)

    def get(
        self,
        source: str,
        query: str,
        domain: str,
    ) -> list[RetrievalRecord] | None:
        """Return cached records if present and not expired; else ``None``."""

        if not self.db_path.exists():
            return None
        key = _CacheKey(source, query, domain).hash()
        now = int(time.time())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value_json, expires_at FROM cache "
                "WHERE key = ? AND expires_at > ?",
                (key, now),
            ).fetchone()
        if row is None:
            return None
        try:
            raw = json.loads(row["value_json"])
        except json.JSONDecodeError:
            return None
        return [_record_from_dict(d) for d in raw]

    def put(
        self,
        source: str,
        query: str,
        domain: str,
        records: Iterable[RetrievalRecord],
        *,
        ttl_sec: int | None = None,
    ) -> None:
        key = _CacheKey(source, query, domain).hash()
        now = int(time.time())
        expires = now + (ttl_sec if ttl_sec is not None else self.ttl_for(source))
        payload = json.dumps(
            [r.to_dict() for r in records],
            ensure_ascii=False,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cache
                    (key, source, query, domain, value_json, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    expires_at = excluded.expires_at,
                    created_at = excluded.created_at
                """,
                (key, source, query, domain, payload, expires, now),
            )
            conn.commit()

    def invalidate(self, source: str | None = None) -> int:
        """Remove cached entries.  Returns rows deleted."""

        if not self.db_path.exists():
            return 0
        with self._connect() as conn:
            if source is None:
                cur = conn.execute("DELETE FROM cache")
            else:
                cur = conn.execute("DELETE FROM cache WHERE source = ?", (source,))
            conn.commit()
            return cur.rowcount

    def vacuum_expired(self) -> int:
        """Drop expired rows; call periodically (e.g. from a launchd job).

        Returns rows deleted.
        """

        if not self.db_path.exists():
            return 0
        now = int(time.time())
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM cache WHERE expires_at <= ?", (now,))
            conn.commit()
            return cur.rowcount

    def stats(self) -> dict[str, int]:
        if not self.db_path.exists():
            return {"rows": 0, "expired": 0, "by_source": {}}
        now = int(time.time())
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            expired = conn.execute(
                "SELECT COUNT(*) FROM cache WHERE expires_at <= ?", (now,),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT source, COUNT(*) AS n FROM cache GROUP BY source"
            ).fetchall()
        return {
            "rows": int(total),
            "expired": int(expired),
            "by_source": {row["source"]: int(row["n"]) for row in rows},
        }

    # ------- internals -----------------------------------------------------

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS cache (
                    key         TEXT PRIMARY KEY,
                    source      TEXT NOT NULL,
                    query       TEXT NOT NULL,
                    domain      TEXT NOT NULL DEFAULT '',
                    value_json  TEXT NOT NULL,
                    expires_at  INTEGER NOT NULL,
                    created_at  INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cache_expires
                    ON cache(expires_at);
                CREATE INDEX IF NOT EXISTS idx_cache_source
                    ON cache(source);
                """
            )
            conn.commit()

    def _connect(self):
        return connect_sqlite_store(self.db_path)


def _record_from_dict(d: dict) -> RetrievalRecord:
    return RetrievalRecord(
        source=str(d.get("source", "")),
        title=str(d.get("title", "")),
        url=str(d.get("url", "")),
        snippet=str(d.get("snippet", "")),
        score=float(d.get("score", 0.0)),
        fetched_at=str(d.get("fetched_at", "")),
        domain=str(d.get("domain", "")),
        metadata=dict(d.get("metadata", {})),
        canonical_id=str(d.get("canonical_id", "")),
        cite_id=str(d.get("cite_id", "")),
    )
