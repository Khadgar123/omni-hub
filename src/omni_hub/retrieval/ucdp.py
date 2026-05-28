"""UCDP (Uppsala Conflict Data Program) — open conflict event data.

Tier 0 free academic source for armed-conflict events worldwide.
Complements ACLED (which requires a key) and GDELT (which is noisier).

Why UCDP for the cascade:
* CC BY 4.0 license, fully free, no key
* Academic gold-standard — used by 2000+ peer-reviewed papers
* Georeferenced Event Dataset (GED): one row per fatal incident,
  with date / coords / casualty count / actors
* Coverage: 1989–present, monthly refresh

Limitation: ~6 month lag vs ACLED real-time.  Use ACLED for live ops,
UCDP for historical / verified.

API docs: https://ucdp.uu.se/apidocs/
Base URL: ``https://ucdpapi.pcr.uu.se/api/``

This connector targets the ``gedevents`` endpoint with country / year
filters.  Query format: free-text country name OR
``"<country>:<start_year>-<end_year>"`` (e.g. ``"Ukraine:2023-2026"``).
"""

from __future__ import annotations

import re
from typing import Any

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord, http_get_json


GED_URL = "https://ucdpapi.pcr.uu.se/api/gedevents/24.1"          # version 24.1 (2024 release)
DEFAULT_YEAR_RANGE = (2024, 2026)
UCDP_SECRET_REF = "local:omni-hub/api/ucdp/default"


def _resolve_ucdp_token() -> str:
    import os
    env_token = os.environ.get("UCDP_API_TOKEN", "").strip()
    if env_token:
        return env_token
    try:
        from ..secrets import resolve_secret_ref, SecretStoreError
    except ImportError:
        return ""
    try:
        return resolve_secret_ref(UCDP_SECRET_REF) or ""
    except SecretStoreError:
        return ""
    except Exception:                                            # noqa: BLE001
        return ""


def _parse_query(query: str) -> tuple[str, int, int]:
    """Parse ``country[:start-end]`` syntax → (country, start_year, end_year)."""

    q = query.strip()
    m = re.match(r"^(.+?):(\d{4})-(\d{4})$", q)
    if m:
        return m.group(1).strip(), int(m.group(2)), int(m.group(3))
    return q, DEFAULT_YEAR_RANGE[0], DEFAULT_YEAR_RANGE[1]


class UCDPSource:
    """Uppsala Conflict Data — Georeferenced Event Dataset (GED).

    As of 2024 the public API requires a free token (academic /
    non-commercial use).  Set ``UCDP_API_TOKEN`` env or
    ``.omni/secrets.json::omni-hub/api/ucdp/default`` after registering
    at https://ucdp.uu.se/api/.
    """

    name = "ucdp"
    tier = 1          # CC BY 4.0 data, but token-gated as of 2024

    def __init__(
        self,
        *,
        api_token: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_token = api_token if api_token is not None else _resolve_ucdp_token()
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self.api_token:
            return "ok", "UCDP token configured (CC BY 4.0 data)"
        return "warn", (
            "UCDP_API_TOKEN not set; register free at https://ucdp.uu.se/api/"
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
            raise RetrievalError("UCDP_API_TOKEN not set")
        country, start_year, end_year = _parse_query(query)

        # UCDP GED filter syntax: Country=<name>&StartDate=<YYYY-MM-DD>&EndDate=<YYYY-MM-DD>
        params = {
            "pagesize": str(min(max(limit, 1), 100)),
            "page": "0",
            "Country": country,
            "StartDate": f"{start_year}-01-01",
            "EndDate": f"{end_year}-12-31",
        }
        try:
            data: Any = http_get_json(
                GED_URL,
                params=params,
                headers={
                    "Accept": "application/json",
                    "x-ucdp-access-token": self.api_token,
                },
                timeout=self.timeout,
            )
        except RetrievalError:
            raise
        except Exception as exc:                                  # noqa: BLE001
            raise RetrievalError(f"ucdp {type(exc).__name__}: {exc}") from exc

        if not isinstance(data, dict):
            return []
        events = data.get("Result") or data.get("results") or []
        records: list[RetrievalRecord] = []
        for ev in events[:limit]:
            if not isinstance(ev, dict):
                continue
            event_id = str(ev.get("id", "") or ev.get("event_id", ""))
            country_name = str(ev.get("country", ""))
            date_start = str(ev.get("date_start", "") or "")[:10]
            date_end = str(ev.get("date_end", "") or "")[:10]
            best = ev.get("best", ev.get("best_est", 0)) or 0
            side_a = str(ev.get("side_a", "") or "")
            side_b = str(ev.get("side_b", "") or "")
            event_type = str(ev.get("type_of_violence", "") or "")
            location = str(ev.get("where_coordinates", "") or "")
            source_article = str(ev.get("source_article", "") or "")[:300]

            title = (
                f"[{country_name}] {side_a} ↔ {side_b} — {date_start} ({best} deaths)"
                if side_a or side_b
                else f"[{country_name}] {date_start} ({best} deaths)"
            )
            snippet = (
                f"Type: {event_type} · Location: {location}\n"
                f"Sources: {source_article[:200]}"
            )

            records.append(RetrievalRecord(
                source=self.name,
                title=title[:180],
                url=f"https://ucdp.uu.se/ged/{event_id}" if event_id else "",
                snippet=snippet[:600],
                score=float(best),
                canonical_id=f"ucdp:{event_id}" if event_id else "",
                metadata={
                    "country": country_name,
                    "date_start": date_start,
                    "date_end": date_end,
                    "deaths_best": best,
                    "side_a": side_a,
                    "side_b": side_b,
                    "type_of_violence": event_type,
                    "location": location,
                },
            ))
        return records


__all__ = ["UCDPSource"]
