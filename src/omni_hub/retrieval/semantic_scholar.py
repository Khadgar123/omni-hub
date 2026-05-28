"""Semantic Scholar S2 — 200M+ papers, free with optional key.

Without key: 5k req per 5 minutes shared across all anonymous users; can
return 429 unpredictably.  With a key (via ``SEMANTIC_SCHOLAR_API_KEY``
env var or ``.omni/secrets.json::omni-hub/api/semantic-scholar/default``):
1 RPS dedicated, still free.

S2 recycles unused keys after ~60 days of inactivity — keep a heartbeat
(e.g. weekly channel-health ping) to avoid silent revocation.

Used as the second academic source after OpenAlex when papers are scarce.
"""

from __future__ import annotations

import os

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord, http_get_json


SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_SECRET_REF = "local:omni-hub/api/semantic-scholar/default"

_DEFAULT_FIELDS = (
    "title,abstract,year,authors,venue,citationCount,openAccessPdf,url,externalIds"
)


def _resolve_s2_key() -> str:
    env_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        from ..secrets import resolve_secret_ref, SecretStoreError
    except ImportError:
        return ""
    try:
        return resolve_secret_ref(S2_SECRET_REF) or ""
    except SecretStoreError:
        return ""
    except Exception:                                                # noqa: BLE001
        return ""


class SemanticScholarSource:
    name = "semantic_scholar"
    tier = 0          # works anonymous; key upgrades from 5k/5min shared → 1 RPS dedicated

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = api_key if api_key is not None else _resolve_s2_key()
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self.api_key:
            return "ok", "dedicated 1 RPS (api key configured)"
        return "warn", "anonymous (5k/5min shared, 429-prone)"

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
