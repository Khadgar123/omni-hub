"""Cross-encoder rerank tail for the cascade.

2026 Q2 consensus: dense + BM25 → RRF (k=60) → top 50-200 → cross-encoder
rerank → top 5-10.  Cohere Rerank 4 and Voyage rerank-2.5 are the two
production options.  Both are key-gated; the cascade falls through when
no key is configured.

Stdlib-only.  Each reranker wraps the vendor HTTP endpoint as a small
adapter; the caller stays inside the standard ``RetrievalRecord`` shape.
"""

from __future__ import annotations

import os
from typing import Any

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord, http_get_json


COHERE_RERANK_URL = "https://api.cohere.com/v2/rerank"
VOYAGE_RERANK_URL = "https://api.voyageai.com/v1/rerank"


class CohereRerankerV4:
    """Cohere Rerank 4 cross-encoder.  Requires ``COHERE_API_KEY``.

    Cohere v2 endpoint accepts a list of documents (string OR
    {text: ...} dict) and returns indexed scores.  We pass title +
    snippet concatenated and pin the model id so the score column is
    comparable across runs.
    """

    name = "cohere_rerank_4"
    tier = 2

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "rerank-v3.5",
        timeout: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("COHERE_API_KEY", "")
        self.model = model
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return ("ready", "configured") if self.api_key else ("off", "COHERE_API_KEY not set")

    def rerank(
        self,
        query: str,
        records: list[RetrievalRecord],
        *,
        top_n: int | None = None,
    ) -> list[RetrievalRecord]:
        if not self.api_key:
            raise RetrievalError("COHERE_API_KEY required for cohere rerank")
        if not records:
            return []

        documents = [_doc_text(r) for r in records]
        body = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": top_n or len(records),
        }
        try:
            payload = _http_post_json(
                COHERE_RERANK_URL,
                body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        except RetrievalError:
            raise
        except Exception as exc:                                # noqa: BLE001
            raise RetrievalError(f"cohere rerank failed: {exc}") from exc

        # Cohere returns {results: [{index, relevance_score, document?}, ...]}
        ordered: list[RetrievalRecord] = []
        for item in payload.get("results", []):
            idx = int(item.get("index", -1))
            if idx < 0 or idx >= len(records):
                continue
            record = records[idx]
            score = float(item.get("relevance_score", 0.0))
            # Rewrite the score column so downstream formatters see the
            # rerank score, not the upstream BM25/dense float.
            record.score = score
            record.metadata = dict(record.metadata or {})
            record.metadata["rerank_score"] = score
            record.metadata["reranker"] = self.name
            ordered.append(record)
        return ordered


class VoyageRerankerV2_5:
    """Voyage rerank-2.5 cross-encoder.  Requires ``VOYAGE_API_KEY``.

    Anthropic's 2026 recommended reranker.  Voyage v1 endpoint accepts
    {query, documents, model, top_k} and returns {data: [{index, ...
    relevance_score}, ...]}.  +7.94% over Cohere v3.5 per Voyage's
    August-2025 release post.
    """

    name = "voyage_rerank_2_5"
    tier = 2

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "rerank-2.5",
        timeout: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("VOYAGE_API_KEY", "")
        self.model = model
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return ("ready", "configured") if self.api_key else ("off", "VOYAGE_API_KEY not set")

    def rerank(
        self,
        query: str,
        records: list[RetrievalRecord],
        *,
        top_n: int | None = None,
    ) -> list[RetrievalRecord]:
        if not self.api_key:
            raise RetrievalError("VOYAGE_API_KEY required for voyage rerank")
        if not records:
            return []

        documents = [_doc_text(r) for r in records]
        body = {
            "query": query,
            "documents": documents,
            "model": self.model,
            "top_k": top_n or len(records),
        }
        try:
            payload = _http_post_json(
                VOYAGE_RERANK_URL,
                body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        except RetrievalError:
            raise
        except Exception as exc:                                # noqa: BLE001
            raise RetrievalError(f"voyage rerank failed: {exc}") from exc

        ordered: list[RetrievalRecord] = []
        for item in payload.get("data", []):
            idx = int(item.get("index", -1))
            if idx < 0 or idx >= len(records):
                continue
            record = records[idx]
            score = float(item.get("relevance_score", 0.0))
            record.score = score
            record.metadata = dict(record.metadata or {})
            record.metadata["rerank_score"] = score
            record.metadata["reranker"] = self.name
            ordered.append(record)
        return ordered


def build_reranker(name: str):
    """Return a reranker instance by short name, or None for ``"none"``."""

    n = (name or "").strip().lower()
    if n in {"", "none"}:
        return None
    if n == "cohere":
        return CohereRerankerV4()
    if n == "voyage":
        return VoyageRerankerV2_5()
    raise ValueError(f"unknown reranker {name!r}; expected none|cohere|voyage")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _doc_text(record: RetrievalRecord) -> str:
    title = (record.title or "").strip()
    snippet = (record.snippet or "").strip()
    if title and snippet:
        return f"{title}\n\n{snippet}"
    return title or snippet


def _http_post_json(
    url: str,
    body: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """POST JSON via urllib.  Wraps urllib.HTTPError → RetrievalError."""

    import json as _json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url,
        data=_json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
    )
    if headers:
        for key, value in headers.items():
            req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:                                        # noqa: BLE001
            body_text = ""
        raise RetrievalError(f"HTTP {exc.code} from {url}: {body_text[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RetrievalError(f"network error to {url}: {exc.reason}") from exc

    return _json.loads(data.decode("utf-8"))
