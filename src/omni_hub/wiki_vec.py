"""Hybrid wiki search — sqlite-vec KNN fused with FTS5 BM25 via RRF.

Pure FTS5 (lexical/BM25) is below the 2026 retrieval baseline: it misses
paraphrases and cross-lingual matches. The fix is *hybrid* — run a vector
KNN and lexical BM25 in parallel and fuse the two rankings with Reciprocal
Rank Fusion (the same RRF the source cascade uses). No reranker required;
RRF is parameter-light and robust.

Design choices (deliberately minimal — "若非必要勿增实体"):
* **sqlite-vec** (v0.1.9, MIT) is the vector store — a ``vec0`` virtual
  table in a sidecar DB next to the FTS5 one. No server, no new service.
* The **embedder is injected** (``embed_fn``). Production uses the already
  installed ``FlagEmbedding`` bge model (lazy-loaded, no extra heavy dep);
  tests pass a deterministic fake, so the whole module runs offline with no
  model download.
* Fusion reuses RRF; ``wiki-search`` stays FTS-only unless ``--hybrid`` is
  given, so default behaviour and every existing test are unchanged.

This module is self-contained: it never imports knowledge_plane at top level
(avoids cycles) and degrades to "vector unavailable" rather than raising when
sqlite-vec or the embedder is missing.
"""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path
from typing import Callable, Sequence

VEC_DB_PATH = ".omni/wiki_vec.sqlite3"
DEFAULT_DIM = 1024  # bge-m3 dimension; overridden by the actual embed_fn output

EmbedFn = Callable[[list[str]], list[list[float]]]


# ---------------------------------------------------------------------------
# sqlite-vec availability (mirror of wiki_fts.fts5_available)
# ---------------------------------------------------------------------------


def sqlite_vec_available() -> bool:
    """True iff the sqlite-vec extension can be loaded into this sqlite build."""

    try:
        import sqlite_vec  # noqa: F401
    except Exception:       # noqa: BLE001
        return False
    try:
        db = sqlite3.connect(":memory:")
        db.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(db)
        db.execute("select vec_version()").fetchone()
        db.close()
        return True
    except Exception:       # noqa: BLE001
        return False


def _connect(path: Path) -> sqlite3.Connection:
    import sqlite_vec
    db = sqlite3.connect(str(path))
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    return db


def _serialize(vec: Sequence[float]) -> bytes:
    """Pack a float vector into sqlite-vec's little-endian float32 blob."""

    return struct.pack(f"<{len(vec)}f", *vec)


# ---------------------------------------------------------------------------
# the vector index
# ---------------------------------------------------------------------------


class WikiVecIndex:
    """A ``vec0`` KNN index over wiki page chunks, keyed by page path.

    ``embed_fn`` maps a list of texts -> list of equal-length float vectors.
    The dimension is taken from the first embedding, so any embedder works.
    """

    def __init__(self, workspace: Path | str = ".", *, embed_fn: EmbedFn | None = None) -> None:
        self.workspace = Path(workspace).resolve()
        self.db_path = self.workspace / VEC_DB_PATH
        self._embed_fn = embed_fn
        self._dim: int | None = None

    # -- embedding -------------------------------------------------------

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        fn = self._embed_fn or _default_embed_fn()
        vecs = fn(texts)
        if vecs and self._dim is None:
            self._dim = len(vecs[0])
        return vecs

    # -- build -----------------------------------------------------------

    def rebuild(self, pages: list[tuple[str, str]]) -> dict[str, object]:
        """(Re)build the index from ``[(page_path, text), ...]``.

        Returns ``{"indexed": N, "dim": D, "ok": bool}``; a no-op (ok False)
        when sqlite-vec is unavailable so callers stay on FTS only.
        """

        if not sqlite_vec_available():
            return {"indexed": 0, "dim": 0, "ok": False, "reason": "sqlite-vec unavailable"}
        texts = [t for _, t in pages]
        vecs = self._embed(texts)
        if not vecs:
            return {"indexed": 0, "dim": 0, "ok": True}
        dim = len(vecs[0])
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = _connect(self.db_path)
        try:
            db.execute("DROP TABLE IF EXISTS wiki_vec")
            db.execute(
                f"CREATE VIRTUAL TABLE wiki_vec USING vec0("
                f"page_path TEXT, embedding float[{dim}])"
            )
            db.executemany(
                "INSERT INTO wiki_vec(page_path, embedding) VALUES (?, ?)",
                [(p, _serialize(v)) for (p, _), v in zip(pages, vecs)],
            )
            db.commit()
        finally:
            db.close()
        return {"indexed": len(vecs), "dim": dim, "ok": True}

    # -- search ----------------------------------------------------------

    def search(self, query: str, *, limit: int = 10) -> list[tuple[str, float]]:
        """KNN search -> ``[(page_path, distance), ...]`` ascending by distance.

        Empty list when sqlite-vec/the index is unavailable (fail-soft).
        """

        if not sqlite_vec_available() or not self.db_path.exists():
            return []
        qv = self._embed([query])
        if not qv:
            return []
        db = _connect(self.db_path)
        try:
            rows = db.execute(
                "SELECT page_path, distance FROM wiki_vec "
                "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (_serialize(qv[0]), limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            db.close()
        return [(str(p), float(d)) for p, d in rows]


# ---------------------------------------------------------------------------
# RRF fusion of lexical (FTS) + vector rankings
# ---------------------------------------------------------------------------


def rrf_fuse(
    lexical: list[str],
    vector: list[str],
    *,
    k: int = 60,
    limit: int = 10,
) -> list[str]:
    """Reciprocal Rank Fusion of two ranked page-path lists.

    score(p) = Σ 1/(k + rank) over each list it appears in (rank from 1).
    Deterministic: ties broken by first appearance (lexical before vector).
    """

    scores: dict[str, float] = {}
    order: dict[str, int] = {}
    seen = 0
    for ranked in (lexical, vector):
        for rank, path in enumerate(ranked, start=1):
            scores[path] = scores.get(path, 0.0) + 1.0 / (k + rank)
            if path not in order:
                order[path] = seen
                seen += 1
    fused = sorted(scores, key=lambda p: (-scores[p], order[p]))
    return fused[:limit]


# ---------------------------------------------------------------------------
# default embedder (lazy bge via the already-installed FlagEmbedding)
# ---------------------------------------------------------------------------

_model_cache: object | None = None


def _default_embed_fn() -> EmbedFn:
    """Lazy bge-m3 embedder via FlagEmbedding (no extra dep; model on first use)."""

    def embed(texts: list[str]) -> list[list[float]]:
        global _model_cache
        if _model_cache is None:
            from FlagEmbedding import BGEM3FlagModel
            _model_cache = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False)
        out = _model_cache.encode(texts, batch_size=8, max_length=512)
        dense = out["dense_vecs"] if isinstance(out, dict) else out
        return [list(map(float, v)) for v in dense]

    return embed


def build_from_workspace(workspace, *, embed_fn=None):
    """Index every active wiki page under vault/wiki/ into the vec store.

    Reads page bodies via knowledge_plane's own page lister (imported lazily
    to avoid a cycle). Returns the rebuild report.
    """

    from pathlib import Path as _P
    root = _P(workspace).resolve()
    wiki_root = root / "vault" / "wiki"
    pages = []
    if wiki_root.exists():
        for md in sorted(wiki_root.rglob("*.md")):
            name = md.name.lower()
            if name in {"index.md", "log.md", "agents.md", "_schema.md"}:
                continue
            try:
                text = md.read_text(encoding="utf-8")
            except OSError:
                continue
            rel = str(md.relative_to(root))
            pages.append((rel, text))
    return WikiVecIndex(root, embed_fn=embed_fn).rebuild(pages)


def hybrid_search(workspace, query, *, limit=10, embed_fn=None, k=60):
    """Fuse FTS5/substring lexical search with vector KNN via RRF.

    Composes knowledge_plane.search_wiki (lexical) + WikiVecIndex.search
    (semantic). Fail-soft: if the vector side is unavailable it returns the
    lexical ranking unchanged, so a caller can always use hybrid safely.
    Returns a list of page paths, best first.
    """

    from pathlib import Path as _P
    root = _P(workspace).resolve()
    try:
        from .knowledge_plane import search_wiki
        lex = [r.path for r in search_wiki(root, query=query, limit=max(limit, 10))]
    except Exception:  # noqa: BLE001
        lex = []
    vec_hits = WikiVecIndex(root, embed_fn=embed_fn).search(query, limit=max(limit, 10))
    vec = [p for p, _d in vec_hits]
    if not vec:
        return lex[:limit]
    return rrf_fuse(lex, vec, k=k, limit=limit)


__all__ = [
    "VEC_DB_PATH",
    "EmbedFn",
    "WikiVecIndex",
    "sqlite_vec_available",
    "rrf_fuse",
    "build_from_workspace",
    "hybrid_search",
]
