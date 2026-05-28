"""Semantic Scholar S2 — 200M+ papers, free with optional key.

Without key: 5k req per 5 minutes shared across all anonymous users; can
return 429 unpredictably.  With ``SEMANTIC_SCHOLAR_API_KEY`` env var:
1 RPS dedicated, still free.

Used as the second academic source after OpenAlex when papers are scarce.
"""

from __future__ import annotations

import os

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord, http_get_json


SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

_DEFAULT_FIELDS = (
    "title,abstract,year,authors,venue,citationCount,openAccessPdf,url,externalIds"
)


class SemanticScholarSource:
    name = "semantic_scholar"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = api_key or os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
        self.timeout = timeout

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        headers: dict[str, str] = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        params = {
            "query": query,
            "limit": str(min(limit, 100)),
            "fields": _DEFAULT_FIELDS,
        }
        data = http_get_json(
            SEARCH_URL, params=params, headers=headers, timeout=self.timeout,
        )
        items = data.get("data", []) if isinstance(data, dict) else []

        records: list[RetrievalRecord] = []
        for item in items[:limit]:
            authors = [a.get("name", "") for a in item.get("authors", [])][:5]
            ext_ids = item.get("externalIds", {}) or {}
            canonical = ""
            if ext_ids.get("DOI"):
                canonical = f"doi:{str(ext_ids['DOI']).lower()}"
            elif ext_ids.get("ArXiv"):
                canonical = f"arxiv:{ext_ids['ArXiv']}"
            elif ext_ids.get("PubMed"):
                canonical = f"pmid:{ext_ids['PubMed']}"
            records.append(RetrievalRecord(
                source=self.name,
                title=item.get("title", ""),
                url=item.get("url", "") or item.get("openAccessPdf", {}).get("url", ""),
                snippet=(item.get("abstract") or "")[:500],
                score=float(item.get("citationCount", 0)),
                canonical_id=canonical,
                metadata={
                    "authors": [a for a in authors if a],
                    "year": item.get("year"),
                    "venue": item.get("venue", ""),
                    "citation_count": item.get("citationCount", 0),
                    "external_ids": ext_ids,
                    "open_access_pdf": (item.get("openAccessPdf") or {}).get("url", ""),
                },
            ))
        return records
