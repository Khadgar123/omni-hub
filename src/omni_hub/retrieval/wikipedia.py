"""Wikipedia REST API — entity disambiguation + plain-language summaries.

Used for grounding any query that names a real-world entity (person,
company, technology, country, ...).  Multilingual: pass ``lang=zh`` for
Chinese Wikipedia.

Free tier: 50k requests/h anonymous, 100k/h with an OAuth token (2026
rate-limit changes phased in May 2026).  Way more than a single user
needs.
"""

from __future__ import annotations

import urllib.parse

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord, http_get_json


SEARCH_URL_TMPL = "https://{lang}.wikipedia.org/w/rest.php/v1/search/page"
SUMMARY_URL_TMPL = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"


class WikipediaSource:
    name = "wikipedia"

    def __init__(
        self,
        *,
        lang: str = "en",
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.lang = lang
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
        lang = self._detect_lang(query)
        search_url = SEARCH_URL_TMPL.format(lang=lang)
        data = http_get_json(
            search_url,
            params={"q": query, "limit": str(min(limit, 10))},
            timeout=self.timeout,
        )
        pages = data.get("pages", []) if isinstance(data, dict) else []

        records: list[RetrievalRecord] = []
        for page in pages[:limit]:
            title = page.get("title", "")
            slug = title.replace(" ", "_")
            url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(slug)}"
            records.append(RetrievalRecord(
                source=self.name,
                title=title,
                url=url,
                snippet=(page.get("excerpt") or page.get("description") or "")[:500],
                score=1.0,
                metadata={
                    "lang": lang,
                    "page_id": page.get("id"),
                    "description": page.get("description", ""),
                },
            ))
        return records

    def summary(self, title: str, *, lang: str | None = None) -> dict[str, str]:
        """Fetch the lead paragraph + canonical URL for a known title."""

        lng = lang or self.lang
        url = SUMMARY_URL_TMPL.format(
            lang=lng, title=urllib.parse.quote(title.replace(" ", "_")),
        )
        return http_get_json(url, timeout=self.timeout)

    def _detect_lang(self, query: str) -> str:
        """Crude: if the query has any CJK characters, switch to zh.wikipedia."""
        for ch in query:
            if "一" <= ch <= "鿿":
                return "zh"
        return self.lang
