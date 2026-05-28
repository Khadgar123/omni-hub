"""arXiv API — every preprint, Atom feed format.

Rate limit: 1 request / 3 seconds, no daily cap (2026-Q3 tightened 429
enforcement — respect this strictly).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord, http_get_text


QUERY_URL = "https://export.arxiv.org/api/query"
ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}


class ArxivSource:
    name = "arxiv"

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
        # arXiv accepts a free-form ``search_query=all:X`` plus category
        # narrowing for the ``ai_progress`` domain.
        if domain == "ai_progress":
            search_query = f"(cat:cs.AI OR cat:cs.LG OR cat:cs.CL) AND all:{query}"
        else:
            search_query = f"all:{query}"

        url = (
            f"{QUERY_URL}?search_query={search_query}"
            f"&start=0&max_results={min(limit, 25)}"
            "&sortBy=submittedDate&sortOrder=descending"
        )
        text, _ = http_get_text(url, timeout=self.timeout, accept="application/atom+xml")
        root = ET.fromstring(text)

        records: list[RetrievalRecord] = []
        for entry in root.findall("atom:entry", ATOM_NS):
            title = (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").strip()
            summary = (entry.findtext("atom:summary", default="", namespaces=ATOM_NS) or "").strip()
            published = entry.findtext("atom:published", default="", namespaces=ATOM_NS) or ""
            entry_id = entry.findtext("atom:id", default="", namespaces=ATOM_NS) or ""
            authors = [
                (author.findtext("atom:name", default="", namespaces=ATOM_NS) or "").strip()
                for author in entry.findall("atom:author", ATOM_NS)
            ][:5]
            categories = [
                cat.attrib.get("term", "")
                for cat in entry.findall("atom:category", ATOM_NS)
            ]
            arxiv_id = entry_id.rsplit("/", 1)[-1]

            records.append(RetrievalRecord(
                source=self.name,
                title=title,
                url=entry_id,
                snippet=summary[:500],
                score=1.0,                  # arXiv has no popularity field
                metadata={
                    "arxiv_id": arxiv_id,
                    "authors": authors,
                    "published": published,
                    "categories": categories,
                    # arxiv.org/html/<id> renders the paper as accessible HTML —
                    # use this for cheaper extraction than the PDF.
                    "html_url": f"https://arxiv.org/html/{arxiv_id}",
                    "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                },
            ))
        return records
