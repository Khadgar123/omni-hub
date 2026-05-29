"""arXiv API — every preprint, Atom feed format.

Rate limit: 1 request / 3 seconds, no daily cap (2026-Q3 tightened 429
enforcement — respect this strictly).
"""

from __future__ import annotations

import urllib.parse
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
    tier = 0

    def __init__(self, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return "ok", "anonymous 1 req/3s (export.arxiv.org)"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        q = query.strip()
        if not q:
            return []
        # arXiv accepts a free-form ``search_query=all:X`` plus category
        # narrowing for the ``ai_progress`` domain.  Callers may also
        # pass a raw arXiv DSL clause like ``cat:cs.AI`` or
        # ``ti:transformer`` — in those cases we honour it verbatim
        # (v0.43.5 fix: previously got wrapped as ``all:cat:cs.AI``
        # which always returned zero).
        is_dsl = ":" in q.split(" ", 1)[0] and q.split(":", 1)[0] in {
            "all", "ti", "abs", "au", "cat", "id", "co", "jr", "rn",
        }
        if is_dsl:
            search_query = q
        elif domain == "ai_progress":
            search_query = f"(cat:cs.AI OR cat:cs.LG OR cat:cs.CL) AND all:{q}"
        else:
            search_query = f"all:{q}"

        params = {
            "search_query": search_query,
            "start": "0",
            "max_results": str(min(limit, 25)),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        url = f"{QUERY_URL}?{urllib.parse.urlencode(params)}"
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

            # arXiv IDs are versioned (2510.01234v1).  Strip the version
            # suffix so v1 and v2 of the same paper collapse to one record.
            base_id = arxiv_id.rsplit("v", 1)[0] if "v" in arxiv_id else arxiv_id
            # v0.49: stop under-extraction (Q2/Q3) — the Atom feed carries the
            # journal DOI, journal_ref, the free-text comment (often the
            # acceptance venue, e.g. "Accepted at NeurIPS 2025"), the primary
            # category, the updated timestamp, and per-author affiliations.
            doi = (entry.findtext("arxiv:doi", default="", namespaces=ATOM_NS) or "").strip()
            journal_ref = (entry.findtext("arxiv:journal_ref", default="", namespaces=ATOM_NS) or "").strip()
            comment = (entry.findtext("arxiv:comment", default="", namespaces=ATOM_NS) or "").strip()
            updated = entry.findtext("atom:updated", default="", namespaces=ATOM_NS) or ""
            primary_el = entry.find("arxiv:primary_category", ATOM_NS)
            primary_category = primary_el.attrib.get("term", "") if primary_el is not None else ""
            authors_detailed = []
            for author in entry.findall("atom:author", ATOM_NS):
                nm = (author.findtext("atom:name", default="", namespaces=ATOM_NS) or "").strip()
                aff = (author.findtext("arxiv:affiliation", default="", namespaces=ATOM_NS) or "").strip()
                if nm:
                    authors_detailed.append({"name": nm, "affiliation": aff})
            records.append(RetrievalRecord(
                source=self.name,
                title=title,
                url=entry_id,
                snippet=summary[:500],
                score=1.0,                  # arXiv has no popularity field
                canonical_id=f"arxiv:{base_id}",
                metadata={
                    "arxiv_id": arxiv_id,
                    "arxiv_base_id": base_id,
                    "authors": authors,
                    "authors_detailed": authors_detailed,
                    "published": published,
                    "updated": updated,
                    "categories": categories,
                    "primary_category": primary_category,
                    "doi": doi,
                    "journal_ref": journal_ref,
                    "comment": comment,
                    # arxiv.org/html/<id> renders the paper as accessible HTML —
                    # use this for cheaper extraction than the PDF.
                    "html_url": f"https://arxiv.org/html/{arxiv_id}",
                    "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                },
            ))
        return records
