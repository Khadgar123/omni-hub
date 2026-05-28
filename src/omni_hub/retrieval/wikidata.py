"""Wikidata entity search — structured fact anchor for broad queries.

The hot-path connector uses the Wikidata Action API's ``wbsearchentities``
endpoint instead of SPARQL because entity search is cheap, fast, and enough
to anchor a natural-language query to stable QIDs.  SPARQL expansion can be
added later as a slower enrichment step once a QID is known.
"""

from __future__ import annotations

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord, http_get_json


SEARCH_URL = "https://www.wikidata.org/w/api.php"


class WikidataSource:
    """Search Wikidata entities.  No key required."""

    name = "wikidata"
    tier = 0

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return "ok", "anonymous (wikidata.org wbsearchentities)"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        lang = _detect_lang(query)
        data = http_get_json(
            SEARCH_URL,
            params={
                "action": "wbsearchentities",
                "search": query,
                "language": lang,
                "uselang": lang,
                "format": "json",
                "limit": str(min(max(limit, 1), 20)),
            },
            timeout=self.timeout,
        )
        items = data.get("search", []) if isinstance(data, dict) else []

        records: list[RetrievalRecord] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            qid = str(item.get("id", "") or item.get("title", ""))
            label = str(item.get("label", "") or item.get("title", ""))
            description = str(item.get("description", ""))
            url = str(item.get("concepturi", "")) or (
                f"https://www.wikidata.org/wiki/{qid}" if qid else ""
            )
            records.append(RetrievalRecord(
                source=self.name,
                title=label,
                url=url,
                snippet=description[:500],
                score=1.0,
                canonical_id=f"wikidata:{qid}" if qid else "",
                metadata={
                    "qid": qid,
                    "lang": lang,
                    "match": item.get("match", {}),
                    "aliases": item.get("aliases", []),
                },
            ))
        return records


def _detect_lang(query: str) -> str:
    for ch in query:
        if "一" <= ch <= "鿿":
            return "zh"
    return "en"
