"""Legal and court-opinion retrieval sources."""

from __future__ import annotations

import os

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord, http_get_json


COURTLISTENER_SEARCH = "https://www.courtlistener.com/api/rest/v4/search/"
COURTLISTENER_BASE = "https://www.courtlistener.com"


COURTLISTENER_SECRET_REF = "local:omni-hub/api/courtlistener/default"


def _resolve_courtlistener_token() -> str:
    env_token = os.environ.get("COURTLISTENER_TOKEN", "").strip()
    if env_token:
        return env_token
    try:
        from ..secrets import resolve_secret_ref, SecretStoreError
    except ImportError:
        return ""
    try:
        return resolve_secret_ref(COURTLISTENER_SECRET_REF) or ""
    except SecretStoreError:
        return ""
    except Exception:                                            # noqa: BLE001
        return ""


class CourtListenerSource:
    """CourtListener opinion search.

    Token via ``COURTLISTENER_TOKEN`` env or
    ``.omni/secrets.json::omni-hub/api/courtlistener/default``.
    Anonymous tier works (lower limits); authenticated raises throughput.
    """

    name = "courtlistener"
    tier = 0

    def __init__(
        self,
        *,
        token: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.token = token if token is not None else _resolve_courtlistener_token()
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self.token:
            return "ok", "authenticated CourtListener REST API"
        return "warn", "anonymous CourtListener; set COURTLISTENER_TOKEN for higher limits"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        headers = {"Authorization": f"Token {self.token}"} if self.token else None
        data = http_get_json(
            COURTLISTENER_SEARCH,
            params={
                "q": query,
                "type": "o",
                "order_by": "score desc",
            },
            headers=headers,
            timeout=self.timeout,
        )
        items = data.get("results", []) if isinstance(data, dict) else []
        records: list[RetrievalRecord] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            absolute_url = str(item.get("absolute_url", ""))
            cluster_id = str(item.get("cluster_id", "") or item.get("id", ""))
            records.append(RetrievalRecord(
                source=self.name,
                title=str(item.get("caseName", "") or item.get("caseNameFull", "")),
                url=_absolute_url(absolute_url),
                snippet=str(item.get("snippet", ""))[:500],
                score=_float(item.get("score")),
                canonical_id=f"courtlistener:{cluster_id}" if cluster_id else "",
                metadata={
                    "court": item.get("court", ""),
                    "date_filed": item.get("dateFiled", ""),
                    "docket_number": item.get("docketNumber", ""),
                    "citations": item.get("citation", []),
                    "cluster_id": cluster_id,
                },
            ))
        return records


def _absolute_url(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path:
        return ""
    return f"{COURTLISTENER_BASE}{path}"


def _float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
