"""Enterprise intelligence broker stubs (v0.22) — Crunchbase + LinkedIn.

Both APIs are commercial:

* **Crunchbase v4 API** — paid, returns company / investment / personnel.
  Stub honors ``CRUNCHBASE_API_KEY`` env var; real subprocess broker
  belongs under ``agent-harness/integrations/crunchbase/``.

* **LinkedIn** — no public people/company search API in 2026; only the
  partner-tier ``Marketing Developer Platform`` exists.  The stub here
  is broker-only; pin ``agent-harness/integrations/linkedin/`` for any
  real implementation (Voyager API reverse engineering or a paid SaaS
  proxy like Proxycurl).

Personal-use only; do not scrape at scale.  The cascade fail-soft-skips
both when their tokens/brokers are missing.
"""

from __future__ import annotations

import os

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord, http_get_json


_CRUNCHBASE_URL = "https://api.crunchbase.com/api/v4"
_OPENCORPORATES_URL = "https://api.opencorporates.com/v0.4/companies/search"
_OPENCORPORATES_SECRET_REF = "local:omni-hub/api/opencorporates/default"
_CRUNCHBASE_SECRET_REF = "local:omni-hub/api/crunchbase/default"


def _resolve_secret(env_var: str, secret_ref: str) -> str:
    """Env var first, then ``.omni/secrets.json`` — shared by both
    OpenCorporates and Crunchbase below."""

    env_val = os.environ.get(env_var, "").strip()
    if env_val:
        return env_val
    try:
        from ..secrets import resolve_secret_ref, SecretStoreError
    except ImportError:
        return ""
    try:
        return resolve_secret_ref(secret_ref) or ""
    except SecretStoreError:
        return ""
    except Exception:                                            # noqa: BLE001
        return ""


def _resolve_opencorporates_token() -> str:
    return _resolve_secret("OPENCORPORATES_API_TOKEN", _OPENCORPORATES_SECRET_REF)


class OpenCorporatesSource:
    """OpenCorporates global company registry search.

    Covers ~200M companies across 140+ jurisdictions vs Crunchbase's
    ~3M startup-skewed dataset.  Strong on registration metadata
    (jurisdiction, incorporation date, status, officers, address).

    Auth (as of 2024): the anonymous API tier was retired; every search
    now requires a free API key.  Get one at https://opencorporates.com/api_accounts/new
    (free tier: 500 req/month, no credit card).  Configure via
    ``OPENCORPORATES_API_TOKEN`` env or
    ``.omni/secrets.json::omni-hub/api/opencorporates/default``.

    Limitations vs Crunchbase: no funding round / valuation, no founders
    or executives, no acquisition news.  Use Crunchbase when you need
    investment data.
    """

    name = "opencorporates"
    tier = 1                                                # free key, monthly quota

    def __init__(
        self,
        *,
        api_token: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_token = (
            api_token if api_token is not None else _resolve_opencorporates_token()
        )
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self.api_token:
            return "ok", "api token configured (500/mo free tier)"
        return "warn", (
            "OPENCORPORATES_API_TOKEN not set; "
            "register at opencorporates.com/api_accounts/new"
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
        if not self.api_token:
            raise RetrievalError("OPENCORPORATES_API_TOKEN not set")

        data = http_get_json(
            _OPENCORPORATES_URL,
            params={
                "q": query,
                "per_page": str(min(max(limit, 1), 30)),
                "order": "score",
                "api_token": self.api_token,
            },
            headers={"Accept": "application/json"},
            timeout=self.timeout,
        )
        if not isinstance(data, dict):
            return []
        results = (data.get("results") or {}).get("companies") or []
        records: list[RetrievalRecord] = []
        for entry in results[:limit]:
            company = (entry or {}).get("company") or {}
            if not company:
                continue
            name = str(company.get("name", ""))
            jurisdiction = str(company.get("jurisdiction_code", ""))
            number = str(company.get("company_number", ""))
            status = str(company.get("current_status", ""))
            inc = str(company.get("incorporation_date", "") or "")
            url = str(company.get("opencorporates_url", ""))
            snippet_bits = [
                f"jurisdiction={jurisdiction}",
                f"company_number={number}",
                f"status={status}",
            ]
            if inc:
                snippet_bits.append(f"incorporated={inc}")
            records.append(RetrievalRecord(
                source=self.name,
                title=name,
                url=url,
                snippet=" | ".join(snippet_bits),
                score=0.0,
                canonical_id=f"oc:{jurisdiction}:{number}" if (jurisdiction and number) else "",
                metadata={
                    "jurisdiction_code": jurisdiction,
                    "company_number": number,
                    "status": status,
                    "incorporation_date": inc,
                    "company_type": company.get("company_type", ""),
                    "registered_address": company.get("registered_address_in_full", ""),
                },
            ))
        return records


class CrunchbaseSource:
    """Crunchbase v4 ``autocompletes`` endpoint.  Tier-2 (paid key)."""

    name = "crunchbase"
    tier = 2

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = (
            api_key
            if api_key is not None
            else _resolve_secret("CRUNCHBASE_API_KEY", _CRUNCHBASE_SECRET_REF)
        )
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if not self.api_key:
            return "off", "CRUNCHBASE_API_KEY not set"
        return "ok", "Crunchbase key configured (env or secrets.json)"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip() or not self.api_key:
            return []
        url = f"{_CRUNCHBASE_URL}/autocompletes"
        try:
            payload = http_get_json(
                url,
                params={
                    "query": query,
                    "collection_ids": "organizations",
                    "limit": limit,
                },
                headers={"X-cb-user-key": self.api_key},
                timeout=self.timeout,
            )
        except RetrievalError:
            return []
        entities = (payload or {}).get("entities") or []
        records: list[RetrievalRecord] = []
        for entity in entities[:limit]:
            if not isinstance(entity, dict):
                continue
            ident = entity.get("identifier") or {}
            uuid = str(ident.get("uuid", ""))
            name = str(ident.get("value", ""))
            short_desc = str(entity.get("short_description", ""))
            permalink = str(ident.get("permalink", ""))
            url_ = f"https://www.crunchbase.com/organization/{permalink}" if permalink else ""
            records.append(RetrievalRecord(
                source=self.name,
                title=name,
                url=url_,
                snippet=short_desc[:500],
                score=0.0,
                canonical_id=f"crunchbase:uuid:{uuid}" if uuid else "",
                metadata={
                    "uuid": uuid,
                    "permalink": permalink,
                    "kind": str(ident.get("entity_def_id", "")),
                },
            ))
        return records


class LinkedInBrokerSource:
    """LinkedIn broker stub (v0.22).

    No public people/company search API exists.  This stub expects a
    broker CLI (``agent-harness/integrations/linkedin/`` — Proxycurl or
    Voyager reverse-engineering wrapper) to be on PATH as ``linkedin``,
    returning JSON arrays of company / people records.

    Until then ``retrieve()`` returns ``[]``.
    """

    name = "linkedin"
    tier = 2

    def __init__(self, *, binary: str = "linkedin",
                 timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.binary = binary
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        import shutil
        if shutil.which(self.binary) is None:
            return "off", (
                f"`{self.binary}` not on PATH. "
                "Pin agent-harness/integrations/linkedin/ broker."
            )
        return "ok", f"{self.binary} present"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        import json
        import shutil
        import subprocess
        if not query.strip() or shutil.which(self.binary) is None:
            return []
        try:
            result = subprocess.run(
                [self.binary, "search", query, "--limit", str(limit), "--json"],
                capture_output=True, text=True, timeout=self.timeout,
            )
        except (subprocess.TimeoutExpired, Exception):       # noqa: BLE001
            return []
        if result.returncode != 0:
            return []
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return []
        items = payload.get("results", []) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        records: list[RetrievalRecord] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind", "company"))
            urn = str(item.get("urn") or item.get("id") or "")
            name = str(item.get("name") or item.get("title") or "")
            summary = str(item.get("summary") or item.get("headline") or "")
            url_ = str(item.get("url") or "")
            records.append(RetrievalRecord(
                source=self.name,
                title=name,
                url=url_,
                snippet=summary[:500],
                score=0.0,
                canonical_id=f"linkedin:{kind}:{urn}" if urn else "",
                metadata={"kind": kind, "urn": urn},
            ))
        return records


__all__ = ["CrunchbaseSource", "LinkedInBrokerSource"]
