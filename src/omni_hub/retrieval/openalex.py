"""OpenAlex — 250M+ scholarly works, free, no signup required.

REST API: ``https://api.openalex.org/works?search=Q``.  The polite-pool
``mailto=`` parameter is recommended (OpenAlex routes those requests to
a faster pool and exempts them from the $1/day credit limit that landed
in Feb 2026 for anonymous traffic).
"""

from __future__ import annotations

import os

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord, http_get_json


WORKS_URL = "https://api.openalex.org/works"
OPENALEX_SECRET_REF = "local:omni-hub/api/openalex/mailto"


def _resolve_openalex_mailto() -> str:
    env_v = os.environ.get("OPENALEX_MAILTO", "").strip()
    if env_v:
        return env_v
    try:
        from ..secrets import resolve_secret_ref, SecretStoreError
    except ImportError:
        return ""
    try:
        return resolve_secret_ref(OPENALEX_SECRET_REF) or ""
    except SecretStoreError:
        return ""
    except Exception:                                            # noqa: BLE001
        return ""


class OpenAlexSource:
    """Scholarly works query.  No API key needed.

    Set ``OPENALEX_MAILTO`` env var to your email to enter the polite-pool
    (~10x rate limit + exempt from anonymous credit cap)."""

    name = "openalex"
    tier = 0          # works out of the box; mailto is optional polite-pool upgrade

    def __init__(
        self,
        *,
        mailto: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.mailto = mailto if mailto is not None else _resolve_openalex_mailto()
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self.mailto:
            return "ok", f"polite-pool: mailto={self.mailto}"
        return "warn", "anonymous tier ($1/day credit cap since Feb 2026); set OPENALEX_MAILTO"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        params: dict[str, str] = {
            "search": query,
            "per-page": str(min(limit, 25)),
        }
        if self.mailto:
            params["mailto"] = self.mailto

        data = http_get_json(WORKS_URL, params=params, timeout=self.timeout)
        results = data.get("results", []) if isinstance(data, dict) else []

        records: list[RetrievalRecord] = []
        for item in results[:limit]:
            authors = [
                (auth.get("author") or {}).get("display_name", "")
                for auth in item.get("authorships", [])
            ][:5]
            year = item.get("publication_year")
            venue = (
                (item.get("primary_location") or {}).get("source") or {}
            ).get("display_name", "")
            doi = item.get("doi", "")
            abstract = _reconstruct_abstract(
                item.get("abstract_inverted_index") or {}
            )

            # v0.46: keep the API-native authorship structure (ORCID + ROR +
            # affiliation + corresponding flag).  This is the recommended way
            # to close the author / lab / ORCID gap — far cleaner and more
            # reliable than parsing a PDF header.  `authors` (display names,
            # capped at 5) stays for the snippet; `authors_detailed` carries
            # the full structured list (uncapped at the connector, 50 max).
            authors_detailed = []
            for a in (item.get("authorships") or [])[:50]:
                author = a.get("author") or {}
                authors_detailed.append({
                    "name": author.get("display_name", ""),
                    "orcid": author.get("orcid") or "",
                    "is_corresponding": bool(a.get("is_corresponding", False)),
                    "institutions": [
                        {
                            "display_name": (inst or {}).get("display_name", ""),
                            "ror": (inst or {}).get("ror", ""),
                            "country_code": (inst or {}).get("country_code", ""),
                        }
                        for inst in (a.get("institutions") or [])
                    ],
                })
            topics = [
                (t or {}).get("display_name", "") for t in (item.get("topics") or [])
            ]
            topics = [t for t in topics if t][:8]
            best_oa = item.get("best_oa_location") or {}
            oa_pdf_url = (
                best_oa.get("pdf_url", "")
                or (item.get("open_access") or {}).get("oa_url", "")
                or ""
            )

            # DOI is the strongest canonical_id for scholarly works; fall
            # back to the openalex_id when DOI is missing (some grey lit).
            canonical = (
                _normalise_doi(doi)
                or (item.get("id", "") or "").replace("https://openalex.org/", "openalex:")
            )
            records.append(RetrievalRecord(
                source=self.name,
                title=item.get("display_name", ""),
                url=item.get("id", "") or doi,
                snippet=abstract[:500] if abstract else venue,
                score=float(item.get("cited_by_count", 0)),
                canonical_id=canonical,
                metadata={
                    "authors": [a for a in authors if a],
                    "authors_detailed": authors_detailed,
                    "topics": topics,
                    "year": year,
                    "venue": venue,
                    "doi": doi,
                    "openalex_id": item.get("id", ""),
                    "cited_by_count": item.get("cited_by_count", 0),
                    "open_access": (item.get("open_access") or {}).get("is_oa", False),
                    "oa_pdf_url": oa_pdf_url,
                },
            ))
        return records


def _reconstruct_abstract(inverted: dict[str, list[int]]) -> str:
    """OpenAlex stores abstracts as ``{word: [positions]}`` for copyright
    reasons.  Reconstruct linear text."""
    if not inverted:
        return ""
    pos: dict[int, str] = {}
    for word, positions in inverted.items():
        for p in positions:
            pos[p] = word
    return " ".join(pos[i] for i in sorted(pos))


def _normalise_doi(doi: str) -> str:
    """OpenAlex returns DOIs in mixed form (https://doi.org/10.x, doi:10.x,
    raw 10.x).  Strip to a canonical ``doi:10.<rest>`` shape so two records
    citing the same DOI in different formats hash to one canonical_id."""

    if not doi:
        return ""
    s = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/", "doi:"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return f"doi:{s}" if s else ""
