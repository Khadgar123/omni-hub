"""US federal policy — Federal Register + Regulations.gov + Congress.gov.

Three sibling sources covering the federal regulatory pipeline:

* **Federal Register** (1994–): every rule, notice, proclamation. *No key
  required.* Endpoint ``federalregister.gov/api/v1/articles.json``.
* **Regulations.gov** (dockets + public comments, 100+ agencies). Free
  ``data.gov`` API key with 1000 req/h. Endpoint
  ``api.regulations.gov/v4/documents``.
* **Congress.gov** (bills, votes, member records, CRS reports). Same
  ``data.gov`` key works. Endpoint ``api.congress.gov/v3/bill``.

ONE ``DATA_GOV_API_KEY`` env var unlocks both Regulations.gov and
Congress.gov. Sign up at ``https://api.data.gov/signup/``.

We expose three separate :class:`RetrievalSource` instances so the
cascade can route each domain (rule vs comment vs bill) at different
priorities.
"""

from __future__ import annotations

import os

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord, http_get_json
from .health import env_var_probe


FED_REGISTER_URL = "https://www.federalregister.gov/api/v1/articles.json"
REGS_GOV_URL = "https://api.regulations.gov/v4/documents"
CONGRESS_URL = "https://api.congress.gov/v3/bill"


# ---------------------------------------------------------------------------
# Federal Register — no key
# ---------------------------------------------------------------------------


class FederalRegisterSource:
    """Free public ledger of all federal rules / notices / proclamations."""

    name = "federal_register"
    tier = 0          # no key required

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return "ok", "anonymous (federalregister.gov)"

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
            FED_REGISTER_URL,
            params={
                "conditions[term]": query,
                "per_page": str(min(limit, 20)),
                "order": "relevance",
                "fields[]": [
                    "title", "abstract", "publication_date", "type",
                    "html_url", "document_number", "agencies",
                ],
            },
            timeout=self.timeout,
        )
        results = data.get("results", []) if isinstance(data, dict) else []
        records: list[RetrievalRecord] = []
        for item in results[:limit]:
            doc_num = str(item.get("document_number", ""))
            agencies = [
                a.get("name", "") for a in (item.get("agencies") or [])
                if isinstance(a, dict)
            ][:3]
            records.append(RetrievalRecord(
                source=self.name,
                title=str(item.get("title", "")),
                url=str(item.get("html_url", "")),
                snippet=str(item.get("abstract") or "")[:500],
                score=0.0,
                canonical_id=f"fedreg:{doc_num}" if doc_num else "",
                metadata={
                    "document_type": item.get("type", ""),
                    "publication_date": item.get("publication_date", ""),
                    "agencies": agencies,
                },
            ))
        return records


# ---------------------------------------------------------------------------
# Regulations.gov — needs data.gov key
# ---------------------------------------------------------------------------


DATA_GOV_SECRET_REF = "local:omni-hub/api/data_gov/default"


def _resolve_data_gov_key() -> str:
    env_key = os.environ.get("DATA_GOV_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        from ..secrets import resolve_secret_ref, SecretStoreError
    except ImportError:
        return ""
    try:
        return resolve_secret_ref(DATA_GOV_SECRET_REF) or ""
    except SecretStoreError:
        return ""
    except Exception:                                            # noqa: BLE001
        return ""


class RegulationsGovSource:
    """Federal dockets + public comments.

    Needs free ``DATA_GOV_API_KEY`` (also unlocks Congress.gov).  Set via
    env or ``.omni/secrets.json::omni-hub/api/data_gov/default``.
    Sign up: https://api.data.gov/signup/ (instant, no email confirmation
    delay for low-volume personal research).
    """

    name = "regulations_gov"
    tier = 1

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = api_key if api_key is not None else _resolve_data_gov_key()
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self.api_key:
            return "ok", "DATA_GOV_API_KEY configured (1000/h)"
        return "warn", (
            "DATA_GOV_API_KEY not set; free at https://api.data.gov/signup/"
        )

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        if not self.api_key:
            raise RetrievalError("DATA_GOV_API_KEY not set")

        data = http_get_json(
            REGS_GOV_URL,
            params={
                "filter[searchTerm]": query,
                "page[size]": str(min(limit, 25)),
                "sort": "-postedDate",
            },
            headers={"X-Api-Key": self.api_key},
            timeout=self.timeout,
        )
        items = data.get("data", []) if isinstance(data, dict) else []
        records: list[RetrievalRecord] = []
        for item in items[:limit]:
            attrs = item.get("attributes") or {}
            doc_id = str(item.get("id", ""))
            title = str(attrs.get("title", ""))
            doc_type = str(attrs.get("documentType", ""))
            agency = str(attrs.get("agencyId", ""))
            records.append(RetrievalRecord(
                source=self.name,
                title=title,
                url=f"https://www.regulations.gov/document/{doc_id}" if doc_id else "",
                snippet=(attrs.get("subject") or "")[:500],
                score=0.0,
                canonical_id=f"regulations:{doc_id}" if doc_id else "",
                metadata={
                    "document_type": doc_type,
                    "agency": agency,
                    "posted_date": attrs.get("postedDate", ""),
                    "docket_id": attrs.get("docketId", ""),
                },
            ))
        return records


# ---------------------------------------------------------------------------
# Congress.gov — same key
# ---------------------------------------------------------------------------


class CongressGovSource:
    """US Congress bills + votes.  Same ``DATA_GOV_API_KEY``."""

    name = "congress_gov"
    tier = 1

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = api_key if api_key is not None else _resolve_data_gov_key()
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self.api_key:
            return "ok", "DATA_GOV_API_KEY configured (1000/h)"
        return "warn", (
            "DATA_GOV_API_KEY not set; free at https://api.data.gov/signup/"
        )

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        if not self.api_key:
            raise RetrievalError("DATA_GOV_API_KEY not set")

        # Congress.gov text search uses `query=` on the bill endpoint.
        data = http_get_json(
            CONGRESS_URL,
            params={
                "query": query,
                "limit": str(min(limit, 25)),
                "sort": "updateDate+desc",
                "format": "json",
                "api_key": self.api_key,
            },
            timeout=self.timeout,
        )
        bills = data.get("bills", []) if isinstance(data, dict) else []
        records: list[RetrievalRecord] = []
        for item in bills[:limit]:
            congress = str(item.get("congress", ""))
            bill_type = str(item.get("type", "")).lower()
            number = str(item.get("number", ""))
            title = str(item.get("title", ""))
            cid = (
                f"congress:{congress}-{bill_type}-{number}"
                if congress and bill_type and number
                else ""
            )
            url = (
                f"https://www.congress.gov/bill/{congress}th-congress/"
                f"{_bill_slug(bill_type)}-bill/{number}"
                if congress and bill_type and number
                else ""
            )
            records.append(RetrievalRecord(
                source=self.name,
                title=title,
                url=url,
                snippet=(item.get("latestAction") or {}).get("text", "")[:500],
                score=0.0,
                canonical_id=cid,
                metadata={
                    "congress": congress,
                    "type": bill_type,
                    "number": number,
                    "update_date": item.get("updateDate", ""),
                    "latest_action": (item.get("latestAction") or {}).get("text", ""),
                },
            ))
        return records


def _bill_slug(bill_type: str) -> str:
    """Map api short codes to URL slugs."""
    return {
        "hr": "house",
        "s": "senate",
        "hjres": "house-joint-resolution",
        "sjres": "senate-joint-resolution",
        "hconres": "house-concurrent-resolution",
        "sconres": "senate-concurrent-resolution",
        "hres": "house-resolution",
        "sres": "senate-resolution",
    }.get(bill_type, bill_type)
