"""SQLite FTS5 sidecar for vault/wiki/.

Substring scoring in `knowledge_plane.search_wiki` is fine for <500 pages
but degrades linearly above that — every search reads every .md off disk.
This module backs `wiki-search` with a SQLite FTS5 virtual table that is
rebuilt incrementally on `wiki-apply-proposal` (and refreshable on demand
via `wiki-reindex`).

Design notes
------------

* **One table, one row per page.** Frontmatter is parsed once and stored
  as JSON in a side column; the searchable haystack is title + body
  (frontmatter stripped).  rebuild_one(path) re-indexes a single file;
  rebuild_all() walks the wiki tree.
* **External-content FTS5 is NOT used** because the content lives in
  markdown files, not in another SQLite table.  We just maintain the FTS
  table directly.
* **Bitemporal filter happens at query time**, not at index time — we
  index everything and skip closed pages in Python (closure metadata
  lives in the row's `frontmatter_json` column).  Cheaper than
  re-indexing whenever t_valid_to flips.
* **Stdlib only.**  `sqlite3` ships with Python 3.12+ and FTS5 is
  enabled by default on macOS / Linux builds.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._storage import safe_workspace_path


FTS_DB_REL = ".omni/wiki_fts.sqlite3"
WIKI_ROOT_REL = "vault/wiki"
META_FILES = {"AGENTS.md", "index.md", "log.md", "_schema.md"}

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


@dataclass(slots=True)
class FtsHit:
    path: str
    title: str
    snippet: str
    score: float                 # bm25-derived (lower = better in SQLite; we invert)
    frontmatter: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "snippet": self.snippet,
            "score": round(self.score, 4),
            "frontmatter": dict(self.frontmatter),
        }


class WikiFTSIndex:
    """Open / maintain / query the FTS5 sidecar.

    Caller decides when to rebuild — typical pattern:

        idx = WikiFTSIndex(workspace)
        idx.rebuild_all()                # one-time on wiki-init
        ...
        idx.rebuild_one(path)            # after wiki-apply-proposal
        hits = idx.search("query", limit=10)
    """

    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace).resolve()
        self.db_path = safe_workspace_path(self.workspace, FTS_DB_REL)
        self.wiki_root = safe_workspace_path(self.workspace, WIKI_ROOT_REL)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ---- mutation ---------------------------------------------------

    def rebuild_all(self) -> dict[str, int]:
        """Drop + recreate index from every .md under vault/wiki/.

        Returns ``{"indexed": N, "skipped": M}``.  Skipped entries are
        meta files (AGENTS.md / index.md / log.md / _schema.md).
        """

        with self._connect() as conn:
            conn.execute("DELETE FROM wiki_pages")
            conn.commit()

        if not self.wiki_root.exists():
            return {"indexed": 0, "skipped": 0}

        indexed = 0
        skipped = 0
        for path in sorted(self.wiki_root.rglob("*.md")):
            if path.name in META_FILES:
                skipped += 1
                continue
            self._index_path(path)
            indexed += 1
        return {"indexed": indexed, "skipped": skipped}

    def rebuild_one(self, path: Path | str) -> bool:
        """Re-index a single page (after apply or supersede).

        ``path`` may be absolute or workspace-relative.  Returns True if
        the page was indexed, False if it was skipped (meta file / not
        under wiki_root).
        """

        full = Path(path)
        if not full.is_absolute():
            full = (self.workspace / full)
        # Resolve to canonical form so macOS /var <-> /private/var symlinks
        # don't defeat the relative_to check.
        full = full.resolve() if full.exists() else full
        try:
            full.relative_to(self.wiki_root)
        except ValueError:
            return False
        if full.name in META_FILES:
            return False
        if not full.exists():
            self.delete_one(full)
            return False
        self._index_path(full)
        return True

    def delete_one(self, path: Path | str) -> bool:
        full = Path(path)
        if not full.is_absolute():
            full = (self.workspace / full)
        # Same /var <-> /private/var canonicalisation as rebuild_one.
        full = full.resolve() if full.exists() else full
        relative = self._relative_path(full)
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM wiki_pages WHERE path = ?", (relative,))
            conn.commit()
        return bool(cur.rowcount)

    # ---- query ------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        include_closed: bool = False,
        now: datetime | None = None,
    ) -> list[FtsHit]:
        normalized = query.strip()
        if not normalized:
            return []
        now = now or datetime.now(UTC)
        fts_query = _to_fts_query(normalized)
        if not fts_query:
            return []
        with self._connect() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT path, title, snippet(wiki_pages, 2, '', '', '…', 12) AS snippet_text,
                           bm25(wiki_pages) AS bm25,
                           frontmatter_json
                    FROM wiki_pages
                    WHERE wiki_pages MATCH ?
                    ORDER BY bm25
                    LIMIT ?
                    """,
                    (fts_query, max(int(limit) * 4, int(limit))),  # over-fetch then filter
                ).fetchall()
            except sqlite3.OperationalError:
                return []

        hits: list[FtsHit] = []
        for row in rows:
            try:
                frontmatter = json.loads(row["frontmatter_json"] or "{}")
            except json.JSONDecodeError:
                frontmatter = {}
            if not include_closed and _is_closed_page(frontmatter, now=now):
                continue
            bm25 = float(row["bm25"]) if row["bm25"] is not None else 0.0
            # Invert: smaller bm25 means better; we want higher score for "better".
            score = -bm25
            hits.append(
                FtsHit(
                    path=row["path"],
                    title=row["title"] or Path(row["path"]).stem,
                    snippet=row["snippet_text"] or "",
                    score=score,
                    frontmatter=frontmatter,
                )
            )
            if len(hits) >= limit:
                break
        return hits

    def stats(self) -> dict[str, int]:
        with self._connect() as conn:
            try:
                row = conn.execute("SELECT COUNT(*) FROM wiki_pages").fetchone()
            except sqlite3.OperationalError:
                return {"indexed": 0}
        return {"indexed": int(row[0]) if row else 0}

    # ---- helpers ----------------------------------------------------

    def _index_path(self, path: Path) -> None:
        relative = self._relative_path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return
        frontmatter, body = _split_frontmatter(text)
        title = _markdown_title(body) or path.stem.replace("-", " ").title()
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM wiki_pages WHERE path = ?",
                (relative,),
            )
            conn.execute(
                """
                INSERT INTO wiki_pages (path, title, body, frontmatter_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    relative,
                    title,
                    body,
                    json.dumps(frontmatter, ensure_ascii=False, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )
            conn.commit()

    def _relative_path(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workspace))
        except ValueError:
            return str(path)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA busy_timeout = 30000;

                CREATE VIRTUAL TABLE IF NOT EXISTS wiki_pages USING fts5(
                    path UNINDEXED,
                    title,
                    body,
                    frontmatter_json UNINDEXED,
                    updated_at UNINDEXED,
                    tokenize = 'unicode61 remove_diacritics 2'
                );
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        from ._storage import connect_sqlite_store
        return connect_sqlite_store(self.db_path)


# ---------------------------------------------------------------------------
# Module helpers (also used by knowledge_plane for fallback parity)
# ---------------------------------------------------------------------------


def _to_fts_query(query: str) -> str:
    """Convert a plain user query into a safe FTS5 MATCH expression.

    Strategy: split on whitespace, drop punctuation, AND the surviving
    terms.  Quote each term so SQL-injection-style tokens (NEAR, *, etc.)
    can't escape into operator territory.
    """

    parts = []
    for token in re.split(r"\s+", query.strip()):
        cleaned = re.sub(r"[^\w一-鿿]+", "", token)
        if cleaned:
            parts.append(f'"{cleaned}"')
    return " AND ".join(parts)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    body = text[match.end():]
    frontmatter: dict[str, Any] = {}
    for raw_line in match.group(1).splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if ":" not in raw_line:
            continue
        key, _, value = raw_line.partition(":")
        frontmatter[key.strip()] = _parse_yaml_value(value.strip())
    return frontmatter, body


def _parse_yaml_value(value: str) -> Any:
    if not value:
        return ""
    if value.lower() == "null":
        return None
    if value.startswith("[") and value.endswith("]"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            inner = value[1:-1].strip()
            if not inner:
                return []
            return [p.strip().strip('"').strip("'") for p in inner.split(",")]
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value


def _markdown_title(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _is_closed_page(frontmatter: dict[str, Any], *, now: datetime) -> bool:
    state = str(frontmatter.get("review_state", "")).strip().lower()
    if state in {"rejected", "superseded"}:
        return True
    t_valid_to = frontmatter.get("t_valid_to")
    if t_valid_to is None:
        return False
    if isinstance(t_valid_to, str) and t_valid_to.strip().lower() in {"", "null", "none"}:
        return False
    try:
        parsed = datetime.fromisoformat(str(t_valid_to))
    except ValueError:
        return False
    return parsed < now


# ---------------------------------------------------------------------------
# Detect FTS5 availability — informational; callers fall back to substring.
# ---------------------------------------------------------------------------


def fts5_available() -> bool:
    """True if the local sqlite3 build has FTS5 compiled in."""

    from contextlib import closing
    try:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute(
                "CREATE VIRTUAL TABLE _probe USING fts5(t)",
            )
        return True
    except sqlite3.OperationalError:
        return False
