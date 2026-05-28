"""Biomedical literature sources — Europe PMC + PubMed.

These complement Crossref/OpenAlex/Semantic Scholar with biomedical-specific
indexes.  Both adapters stay stdlib-only and emit the same RetrievalRecord
shape as the rest of the retrieval plane.
"""

from __future__ import annotations

import os
from typing import Any

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord, http_get_json


EUROPE_PMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
NCBI_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


class EuropePMCSource:
    """Europe PMC REST search.  No key required."""

    name = "europe_pmc"
    tier = 0

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return "ok", "anonymous (Europe PMC REST search)"

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
            EUROPE_PMC_SEARCH,
            params={
                "query": query,
                "format": "json",
                "pageSize": str(min(max(limit, 1), 25)),
            },
            timeout=self.timeout,
        )
        result_list = data.get("resultList", {}) if isinstance(data, dict) else {}
        items = result_list.get("result", []) if isinstance(result_list, dict) else []

        records: list[RetrievalRecord] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            doi = str(item.get("doi", "")).strip()
            pmid = str(item.get("pmid", "") or item.get("id", "")).strip()
            source = str(item.get("source", "")).strip()
            record_id = str(item.get("id", "")).strip() or pmid
            url = (
                f"https://doi.org/{doi}"
                if doi
                else f"https://europepmc.org/article/{source}/{record_id}"
                if source and record_id
                else ""
            )
            records.append(RetrievalRecord(
                source=self.name,
                title=str(item.get("title", "")),
                url=url,
                snippet=str(item.get("abstractText", ""))[:500],
                score=_float(item.get("citedByCount")),
                canonical_id=_canonical_literature_id(doi=doi, pmid=pmid),
                metadata={
                    "doi": doi,
                    "pmid": pmid,
                    "pmcid": item.get("pmcid", ""),
                    "source": source,
                    "journal": item.get("journalTitle", ""),
                    "pub_year": item.get("pubYear", ""),
                    "authors": _split_authors(item.get("authorString", "")),
                    "is_open_access": str(item.get("isOpenAccess", "")).upper() == "Y",
                },
            ))
        return records


class PubMedSource:
    """NCBI PubMed via E-utilities.  Optional ``NCBI_API_KEY`` / ``NCBI_EMAIL``."""

    name = "pubmed"
    tier = 0

    def __init__(
        self,
        *,
        api_key: str | None = None,
        email: str | None = None,
        tool: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("NCBI_API_KEY", "")
        self.email = email if email is not None else os.environ.get("NCBI_EMAIL", "")
        self.tool = tool if tool is not None else os.environ.get("NCBI_TOOL", "omni-hub")
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self.email:
            return "ok", f"anonymous PubMed with email={self.email}"
        return "warn", "anonymous PubMed; set NCBI_EMAIL for polite E-utilities traffic"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        params = self._base_params()
        params.update({
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": str(min(max(limit, 1), 20)),
        })
        search = http_get_json(NCBI_ESEARCH, params=params, timeout=self.timeout)
        result = search.get("esearchresult", {}) if isinstance(search, dict) else {}
        ids = result.get("idlist", []) if isinstance(result, dict) else []
        pmids = [str(pmid) for pmid in ids[:limit] if str(pmid)]
        if not pmids:
            return []

        summary_params = self._base_params()
        summary_params.update({
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "json",
        })
        summary = http_get_json(NCBI_ESUMMARY, params=summary_params, timeout=self.timeout)
        summary_result = summary.get("result", {}) if isinstance(summary, dict) else {}

        records: list[RetrievalRecord] = []
        for pmid in pmids:
            item = summary_result.get(pmid, {}) if isinstance(summary_result, dict) else {}
            if not isinstance(item, dict):
                continue
            doi = _pubmed_doi(item.get("articleids", []))
            journal = str(item.get("fulljournalname", "") or item.get("source", ""))
            pubdate = str(item.get("pubdate", ""))
            records.append(RetrievalRecord(
                source=self.name,
                title=str(item.get("title", "")),
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                snippet="; ".join(part for part in (journal, pubdate) if part)[:500],
                score=0.0,
                canonical_id=_canonical_literature_id(doi=doi, pmid=pmid),
                metadata={
                    "pmid": pmid,
                    "doi": doi,
                    "journal": journal,
                    "pubdate": pubdate,
                    "authors": _pubmed_authors(item.get("authors", [])),
                },
            ))
        return records

    def _base_params(self) -> dict[str, str]:
        params = {"tool": self.tool}
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        return params


def _canonical_literature_id(*, doi: str = "", pmid: str = "") -> str:
    if doi:
        return f"doi:{doi.lower()}"
    if pmid:
        return f"pmid:{pmid}"
    return ""


def _split_authors(value: object) -> list[str]:
    text = str(value or "")
    if not text:
        return []
    return [part.strip() for part in text.replace(";", ",").split(",") if part.strip()][:8]


def _pubmed_authors(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for item in value[:8]:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            if name:
                authors.append(name)
    return authors


def _pubmed_doi(value: object) -> str:
    if not isinstance(value, list):
        return ""
    for item in value:
        if not isinstance(item, dict):
            continue
        if str(item.get("idtype", "")).lower() == "doi":
            return str(item.get("value", "")).strip()
    return ""


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
