"""GDELT 2.0 — 100+ languages of news events, 15-minute refresh, fully free.

Used for any "what's happening with X recently" query — policy / IR /
finance / market shifts.  Coverage is 47 years deep (1979-present)
in the historical project, with 15-minute updates on the 2.0 stream.

We use the DOC API (article-level search), not the GKG/Events tables —
DOC is enough for surfacing news links into the cascade, and avoids
the BigQuery dance.
"""

from __future__ import annotations

import urllib.parse

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord, http_get_json


DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"


class GDELTSource:
    name = "gdelt"

    def __init__(self, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
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
        # GDELT DOC API uses a custom DSL; quote multi-word terms.
        gdelt_query = query if " " not in query.strip() else f'"{query}"'
        params = {
            "query": gdelt_query,
            "mode": "artlist",
            "maxrecords": str(min(limit, 75)),
            "format": "json",
            "sort": "datedesc",
        }
        url = f"{DOC_API}?{urllib.parse.urlencode(params)}"
        data = http_get_json(url, timeout=self.timeout)
        articles = data.get("articles", []) if isinstance(data, dict) else []

        records: list[RetrievalRecord] = []
        for art in articles[:limit]:
            records.append(RetrievalRecord(
                source=self.name,
                title=art.get("title", ""),
                url=art.get("url", ""),
                snippet=(art.get("seendate") or "") + " · " + art.get("domain", ""),
                score=1.0,
                metadata={
                    "seendate": art.get("seendate", ""),
                    "language": art.get("language", ""),
                    "source_country": art.get("sourcecountry", ""),
                    "outlet_domain": art.get("domain", ""),
                    "social_image": art.get("socialimage", ""),
                },
            ))
        return records
