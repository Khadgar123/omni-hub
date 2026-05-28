"""Jina Reader — URL → clean markdown via r.jina.ai.

Why: the existing ``connectors/web.py`` uses raw ``urllib`` + ``html.parser``
which catches the pre-hydration HTML on every SPA (Notion, Substack-new,
X, B站, 小红书) — returning an empty ``<div id="root">``.  Jina Reader
renders JS server-side and returns LLM-friendly markdown for free.

Free tier (2026-Q3): 1M tokens / new key, ~50k calls/mo without signup.
For a single user this is effectively unlimited.

Two surfaces:
* :class:`JinaReaderFetcher` — fetch(url) → RetrievalRecord (URL parser)
* a free-tier search endpoint at ``s.jina.ai/<query>`` — not used here;
  see Brave / Exa for query-based search.
"""

from __future__ import annotations

from .base import (
    DEFAULT_TIMEOUT_SEC,
    RetrievalError,
    RetrievalRecord,
    http_get_text,
)


READER_BASE = "https://r.jina.ai/"


class JinaReaderFetcher:
    """URL→markdown.  Not a query source — call :meth:`fetch` directly."""

    name = "jina_reader"
    tier = 0          # works without key (1M tok free); JINA_API_KEY upgrades

    def check(self) -> tuple[str, str]:
        import os as _os
        if _os.environ.get("JINA_API_KEY", "").strip():
            return "ok", "JINA_API_KEY set"
        return "ok", "anonymous ~50k calls/mo (no key)"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 1,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        """If ``query`` looks like a URL, fetch it.  Otherwise return [].

        The cascade dispatcher calls every source with a ``query``; Jina
        Reader only meaningfully responds when the query is a URL.
        """

        if not (query.startswith("http://") or query.startswith("https://")):
            return []
        try:
            record = self.fetch(query)
        except RetrievalError as exc:
            raise exc
        return [record]

    def fetch(self, url: str) -> RetrievalRecord:
        """Fetch ``url`` through Jina Reader; return a single RetrievalRecord
        whose ``snippet`` is the rendered markdown body.

        Title is extracted from the first non-blank markdown header; falls
        back to the URL host.
        """

        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        target = READER_BASE + url
        body, response_headers = http_get_text(
            target,
            headers=headers,
            timeout=self.timeout,
            accept="text/markdown, text/plain, */*",
        )

        title = _extract_title(body) or _host_of(url)
        # Reader sometimes prefixes the body with a few metadata lines —
        # surface the first ~400 chars as snippet for cascade ranking.
        snippet = body.strip()[:400]
        return RetrievalRecord(
            source=self.name,
            title=title,
            url=url,
            snippet=snippet,
            score=1.0,
            metadata={
                "content_type": response_headers.get("content-type", ""),
                "byte_length": len(body),
                "full_markdown": body,
            },
        )


def _extract_title(markdown: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        if stripped.lower().startswith("title:"):
            return stripped.split(":", 1)[1].strip()
    return ""


def _host_of(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc or url
