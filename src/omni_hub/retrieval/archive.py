"""Archive-backed retrieval sources."""

from __future__ import annotations

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord, http_get_json


IA_ADVANCED_SEARCH = "https://archive.org/advancedsearch.php"
WAYBACK_CDX = "https://web.archive.org/cdx"


class InternetArchiveSource:
    """Internet Archive advanced search.  No key required."""

    name = "internet_archive"
    tier = 0

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return "ok", "anonymous (archive.org advancedsearch)"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        data = http_get_json(
            IA_ADVANCED_SEARCH,
            params={
                "q": query,
                "rows": str(min(max(limit, 1), 50)),
                "page": "1",
                "output": "json",
                "fl[]": [
                    "identifier", "title", "description", "date",
                    "creator", "collection", "mediatype",
                ],
            },
            timeout=self.timeout,
        )
        response = data.get("response", {}) if isinstance(data, dict) else {}
        docs = response.get("docs", []) if isinstance(response, dict) else []
        records: list[RetrievalRecord] = []
        for doc in docs[:limit]:
            if not isinstance(doc, dict):
                continue
            identifier = str(doc.get("identifier", ""))
            title = _first(doc.get("title")) or identifier
            description = _first(doc.get("description"))
            records.append(RetrievalRecord(
                source=self.name,
                title=title,
                url=f"https://archive.org/details/{identifier}" if identifier else "",
                snippet=description[:500],
                score=0.0,
                canonical_id=f"ia:{identifier}" if identifier else "",
                metadata={
                    "identifier": identifier,
                    "date": _first(doc.get("date")),
                    "creator": _first(doc.get("creator")),
                    "collection": doc.get("collection", []),
                    "mediatype": _first(doc.get("mediatype")),
                },
            ))
        return records


class WaybackCDXSource:
    """Wayback Machine CDX lookup for URL-shaped queries.  No key required."""

    name = "wayback_cdx"
    tier = 0

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return "warn", "anonymous Wayback CDX; URL-shaped queries only, can be slow"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        target = query.strip()
        if not _looks_urlish(target):
            return []
        data = http_get_json(
            WAYBACK_CDX,
            params={
                "url": target,
                "output": "json",
                "fl": "urlkey,timestamp,original,mimetype,statuscode,digest",
                "filter": "statuscode:200",
                "collapse": "digest",
                "limit": str(min(max(limit, 1), 25)),
            },
            timeout=self.timeout,
        )
        rows = data if isinstance(data, list) else []
        if rows and isinstance(rows[0], list) and rows[0] and rows[0][0] == "urlkey":
            rows = rows[1:]

        records: list[RetrievalRecord] = []
        for row in rows[:limit]:
            parsed = _parse_cdx_row(row)
            if parsed is None:
                continue
            timestamp, original, mimetype, status, digest = parsed
            records.append(RetrievalRecord(
                source=self.name,
                title=f"Wayback snapshot: {original}",
                url=f"https://web.archive.org/web/{timestamp}/{original}",
                snippet=f"{status} {mimetype} snapshot at {timestamp}",
                score=0.0,
                canonical_id=f"wayback:{digest}" if digest else f"wayback:{timestamp}:{original}",
                metadata={
                    "timestamp": timestamp,
                    "original": original,
                    "mimetype": mimetype,
                    "status": status,
                    "digest": digest,
                },
            ))
        return records


def _first(value: object) -> str:
    if isinstance(value, list):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
        return ""
    return str(value or "").strip()


def _looks_urlish(query: str) -> bool:
    if query.startswith(("http://", "https://")):
        return True
    return "." in query and " " not in query


def _parse_cdx_row(row: object) -> tuple[str, str, str, str, str] | None:
    if isinstance(row, list) and len(row) >= 6:
        return (
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            str(row[5]),
        )
    if isinstance(row, dict):
        return (
            str(row.get("timestamp", "")),
            str(row.get("original", "")),
            str(row.get("mimetype", "")),
            str(row.get("statuscode", "")),
            str(row.get("digest", "")),
        )
    return None
