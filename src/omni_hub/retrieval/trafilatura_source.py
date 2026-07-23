"""Trafilatura — open-source URL → cleaned article extractor.

Best in class for static HTML article extraction (blogs / news / wikis):

* Open source, MIT-licensed (https://github.com/adbar/trafilatura)
* No API key, no rate limit, fully local
* Beats Readability.js / boilerpipe on news/blog content per the
  trafilatura paper benchmarks
* Outputs cleaned text + markdown + metadata (author / date / language)

Tier 0 alternative to:
* Jina Reader (free quota but eventually rate-limits)
* Firecrawl (paid hosted)

Usage (in cascade): the connector takes a URL as ``query`` and returns
one record with the cleaned content as ``snippet`` (up to 4000 chars,
full content in ``metadata['full_text']``).

Caller pattern::

    from omni_hub.retrieval.trafilatura_source import TrafilaturaSource
    recs = TrafilaturaSource().retrieve("https://karpathy.github.io/2026/02/12/microgpt/")
    # → one record with cleaned body
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord

if TYPE_CHECKING:                                                 # pragma: no cover
    pass


class TrafilaturaSource:
    """URL → cleaned article via Trafilatura."""

    name = "trafilatura"
    tier = 0          # local, no key

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        try:
            import trafilatura                                    # noqa: F401
            return "ok", "Trafilatura local extractor (no API key)"
        except ImportError:
            return "off", "trafilatura not installed; pip install trafilatura"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 1,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        url = query.strip()
        if not url:
            return []
        if not url.startswith(("http://", "https://")):
            raise RetrievalError(f"trafilatura needs a URL, got {url!r}")

        try:
            import trafilatura
        except ImportError as exc:
            raise RetrievalError(
                "trafilatura not installed (pip install trafilatura)",
            ) from exc

        # Fetch the page with our own urllib (Trafilatura's
        # fetch_url is fine but goes via urllib3; aligning here for
        # consistent timeout + UA across the cascade).
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "omni-hub/0.42 trafilatura (+research)",
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                html = resp.read()
        except urllib.error.HTTPError as exc:
            raise RetrievalError(f"trafilatura HTTP {exc.code}: {exc.reason}") from exc
        except Exception as exc:                                  # noqa: BLE001
            raise RetrievalError(f"trafilatura fetch {type(exc).__name__}: {exc}") from exc

        # Extract main content.  ``output_format='json'`` returns both
        # cleaned text and structured metadata.
        import json
        result_json = trafilatura.extract(
            html,
            url=url,
            output_format="json",
            include_comments=False,
            include_tables=True,
            with_metadata=True,
            favor_precision=True,
        )
        if not result_json:
            return []
        try:
            data = json.loads(result_json) if isinstance(result_json, str) else result_json
        except json.JSONDecodeError:
            return []

        title = str(data.get("title", "") or "")
        body = str(data.get("text", "") or "")
        author = str(data.get("author", "") or "")
        date = str(data.get("date", "") or "")
        language = str(data.get("language", "") or "")
        excerpt = str(data.get("excerpt", "") or "")

        return [
            RetrievalRecord(
                source=self.name,
                title=title or url,
                url=url,
                snippet=(excerpt or body)[:4000],
                score=0.0,
                canonical_id=f"trafilatura:{url}",
                metadata={
                    "author": author,
                    "date": date,
                    "language": language,
                    "full_text": body,
                    "char_count": len(body),
                },
            ),
        ]


__all__ = ["TrafilaturaSource"]
