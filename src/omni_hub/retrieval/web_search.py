"""Broad web search connectors.

Brave Search is the first broad-web discovery adapter.  It is key-gated
(``BRAVE_SEARCH_API_KEY``) so it stays out of the anonymous hot path until
the operator explicitly configures it, but once present it gives the
``default`` and ``engineering`` cascades a real web-index source instead of
leaning only on Wikipedia / scholarly / news feeds.
"""

from __future__ import annotations

import hashlib
import os

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord, http_get_json
from .health import env_var_probe


BRAVE_WEB_SEARCH = "https://api.search.brave.com/res/v1/web/search"


class BraveSearchSource:
    """Brave Search Web API.  Requires ``BRAVE_SEARCH_API_KEY``."""

    name = "brave_search"
    tier = 1

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = (
            api_key
            if api_key is not None
            else os.environ.get("BRAVE_SEARCH_API_KEY", "")
        )
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return env_var_probe("BRAVE_SEARCH_API_KEY")

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        if not self.api_key:
            raise RetrievalError("BRAVE_SEARCH_API_KEY not set")

        data = http_get_json(
            BRAVE_WEB_SEARCH,
            params={
                "q": query,
                "count": str(min(max(limit, 1), 20)),
                "text_decorations": "false",
            },
            headers={
                "X-Subscription-Token": self.api_key,
                "Accept": "application/json",
            },
            timeout=self.timeout,
        )
        web = data.get("web", {}) if isinstance(data, dict) else {}
        results = web.get("results", []) if isinstance(web, dict) else []

        records: list[RetrievalRecord] = []
        for item in results[:limit]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", ""))
            title = str(item.get("title", ""))
            snippet = str(item.get("description", "") or item.get("snippet", ""))
            canonical = f"web:{_url_hash(url)}" if url else ""
            profile = item.get("profile") if isinstance(item.get("profile"), dict) else {}
            records.append(RetrievalRecord(
                source=self.name,
                title=title,
                url=url,
                snippet=snippet[:500],
                score=0.0,
                canonical_id=canonical,
                metadata={
                    "age": item.get("age", ""),
                    "source_name": profile.get("name", ""),
                    "family_friendly": item.get("family_friendly", None),
                },
            ))
        return records


def _url_hash(url: str) -> str:
    base = url.split("#", 1)[0]
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
