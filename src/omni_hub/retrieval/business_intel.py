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
        self.api_key = api_key or os.environ.get("CRUNCHBASE_API_KEY", "")
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if not self.api_key:
            return "off", "CRUNCHBASE_API_KEY not set"
        return "ok", "CRUNCHBASE_API_KEY present"

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
