"""Crossref REST API — DOI-centred scholarly metadata.

Crossref complements OpenAlex / Semantic Scholar by anchoring scholarly
records to publisher-deposited DOI metadata.  It is not a full-text search
engine, but it is excellent for canonical IDs, journal / proceedings
metadata, dates, authors, funding/license hints, and DOI landing pages.
"""

from __future__ import annotations

import html
import os
import re
from typing import Any

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord, http_get_json


WORKS_URL = "https://api.crossref.org/works"


class CrossrefSource:
    """Crossref works search.  No key; ``CROSSREF_MAILTO`` is polite."""

    name = "crossref"
    tier = 0

    def __init__(
        self,
        *,
        mailto: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.mailto = mailto or os.environ.get("CROSSREF_MAILTO", "")
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self.mailto:
            return "ok", f"polite-pool: mailto={self.mailto}"
        return "warn", "anonymous; set CROSSREF_MAILTO for polite pool"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []

        params: dict[str, Any] = {
            "query": query,
            "rows": str(min(max(limit, 1), 20)),
        }
        if self.mailto:
            params["mailto"] = self.mailto

        data = http_get_json(WORKS_URL, params=params, timeout=self.timeout)
        message = data.get("message", {}) if isinstance(data, dict) else {}
        items = message.get("items", []) if isinstance(message, dict) else []

        records: list[RetrievalRecord] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            title = _first_text(item.get("title")) or _first_text(item.get("subtitle"))
            doi = str(item.get("DOI", "")).strip()
            url = str(item.get("URL", "")) or (f"https://doi.org/{doi}" if doi else "")
            year = _year_from_date_parts(
                item.get("published-print")
                or item.get("published-online")
                or item.get("published")
                or item.get("issued")
                or item.get("created")
            )
            venue = _first_text(item.get("container-title"))
            abstract = _strip_markup(str(item.get("abstract", "")))
            authors = _authors(item.get("author", []))
            records.append(RetrievalRecord(
                source=self.name,
                title=title,
                url=url,
                snippet=(abstract or venue)[:500],
                score=float(item.get("is-referenced-by-count", 0) or 0),
                canonical_id=_normalise_doi(doi),
                metadata={
                    "doi": doi,
                    "year": year,
                    "venue": venue,
                    "authors": authors,
                    "publisher": item.get("publisher", ""),
                    "type": item.get("type", ""),
                    "license": item.get("license", []),
                    "reference_count": item.get("reference-count", 0),
                    "is_referenced_by_count": item.get("is-referenced-by-count", 0),
                },
            ))
        return records


def _first_text(value: object) -> str:
    if isinstance(value, list):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
        return ""
    return str(value or "").strip()


def _year_from_date_parts(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    parts = value.get("date-parts")
    if not isinstance(parts, list) or not parts:
        return None
    first = parts[0]
    if not isinstance(first, list) or not first:
        return None
    try:
        return int(first[0])
    except (TypeError, ValueError):
        return None


def _authors(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for author in value[:8]:
        if not isinstance(author, dict):
            continue
        name = str(author.get("name", "")).strip()
        if not name:
            given = str(author.get("given", "")).strip()
            family = str(author.get("family", "")).strip()
            name = " ".join(part for part in (given, family) if part)
        if name:
            out.append(name)
    return out


def _strip_markup(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return " ".join(text.split())


def _normalise_doi(doi: str) -> str:
    if not doi:
        return ""
    s = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/", "doi:"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return f"doi:{s}" if s else ""
