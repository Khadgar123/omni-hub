"""BGE-reranker — local cross-encoder reranker (no API key, no cloud).

Replaces / augments Cohere / Voyage paid rerank APIs for the cascade.
Model: ``BAAI/bge-reranker-v2-m3`` (~600MB, multilingual incl. Chinese).

**Setup (one-time, ~3 minutes on first call)**::

    pip install -U FlagEmbedding torch

    # First retrieve(...) call will auto-download the model to
    # ~/.cache/huggingface/hub/ (only happens once).

**Why a separate module instead of patching cascade.py**:
The cascade defaults to RRF (rank-fusion) which is cheap and ML-free.
BGE adds 100-300ms latency per query (CPU) so it's opt-in via the
``--reranker bge`` CLI flag, not the default fusion mode.

Usage in code::

    from omni_hub.retrieval.bge_reranker import bge_rerank
    records = bge_rerank(query, records, top_k=10)

Usage via CLI (once wired into ``retrieve`` operation)::

    omni-hub retrieve --query "..." --domain research --reranker bge

The reranker is **lazy-loaded**: import + model download only happens on
first ``bge_rerank()`` call, so this module is safe to import everywhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import RetrievalRecord


MODEL_NAME = "BAAI/bge-reranker-v2-m3"
_model_cache: object | None = None


def _ensure_model():                                              # noqa: ANN202
    """Load BGE reranker on first use.  Subsequent calls reuse the
    in-process cached instance — model weights stay resident.
    """

    global _model_cache
    if _model_cache is not None:
        return _model_cache
    try:
        from FlagEmbedding import FlagReranker
    except ImportError as exc:
        raise RuntimeError(
            "BGE reranker requires `pip install FlagEmbedding torch`. "
            "Install once and the model will download automatically "
            "on next call (~600MB to ~/.cache/huggingface/)."
        ) from exc
    _model_cache = FlagReranker(MODEL_NAME, use_fp16=False)
    return _model_cache


def bge_rerank(
    query: str,
    records: list["RetrievalRecord"],
    *,
    top_k: int | None = None,
) -> list["RetrievalRecord"]:
    """Re-score ``records`` by cross-encoder relevance to ``query``.

    Returns the records sorted descending by reranker score; ``top_k``
    truncates to the highest N.  Each record's ``score`` is overwritten
    with the reranker output (range typically -10..10 in log-odds; for
    use as a sort key the absolute scale doesn't matter).

    If the model can't be loaded (no FlagEmbedding installed), returns
    the input unchanged so callers can fail-soft.
    """

    if not records:
        return []
    try:
        model = _ensure_model()
    except RuntimeError:
        # Library missing → keep cascade-order as-is.
        return records[:top_k] if top_k else records

    pairs = [(query, r.snippet or r.title or "") for r in records]
    scores = model.compute_score(pairs)
    if not isinstance(scores, list):
        scores = [scores]
    for r, s in zip(records, scores):
        r.score = float(s)
    ranked = sorted(records, key=lambda r: r.score, reverse=True)
    return ranked[:top_k] if top_k else ranked


__all__ = ["bge_rerank", "MODEL_NAME"]
