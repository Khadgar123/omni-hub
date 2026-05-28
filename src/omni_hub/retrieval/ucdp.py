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


GED_URL = "https://ucdpapi.pcr.uu.se/api/gedevents/25.1"          # version 25.1 (2025 yearly release)
DEFAULT_YEAR_RANGE = (2024, 2026)
UCDP_SECRET_REF = "local:omni-hub/api/ucdp/default"


# Common Gleditsch & Ward country codes — UCDP API requires these (NOT names).
# Full table: https://www.andybeger.com/states/ — we ship the high-traffic
# ones for ergonomic query("Ukraine") instead of forcing query("369").
_GW_CODES: dict[str, int] = {
    "afghanistan": 700, "algeria": 615, "angola": 540,
    "argentina": 160, "australia": 900, "azerbaijan": 373,
    "bangladesh": 771, "belarus": 370, "brazil": 140,
    "burkina faso": 439, "cambodia": 811, "cameroon": 471,
    "canada": 20, "central african republic": 482, "chad": 483,
    "chile": 155, "china": 710, "colombia": 100,
    "congo (kinshasa)": 490, "dr congo": 490, "drc": 490, "democratic republic of congo": 490,
    "cuba": 40, "czech republic": 316,
    "ecuador": 130, "egypt": 651, "el salvador": 92,
    "ethiopia": 530, "france": 220, "georgia": 372,
    "germany": 255, "ghana": 452, "greece": 350,
    "guatemala": 90, "honduras": 91, "india": 750,
    "indonesia": 850, "iran": 630, "iraq": 645,
    "ireland": 205, "israel": 666, "italy": 325,
    "ivory coast": 437, "japan": 740, "jordan": 663,
    "kenya": 501, "korea, north": 731, "north korea": 731,
    "korea, south": 732, "south korea": 732, "kosovo": 347,
    "kuwait": 690, "kyrgyzstan": 703, "laos": 812,
    "lebanon": 660, "liberia": 450, "libya": 620,
    "malaysia": 820, "mali": 432, "mexico": 70,
    "morocco": 600, "mozambique": 541, "myanmar": 775,
    "burma": 775, "nepal": 790, "netherlands": 210,
    "new zealand": 920, "nicaragua": 93, "niger": 436,
    "nigeria": 475, "pakistan": 770, "palestine": 665,
    "panama": 95, "peru": 135, "philippines": 840,
    "poland": 290, "portugal": 235, "russia": 365,
    "rwanda": 517, "saudi arabia": 670, "senegal": 433,
    "serbia": 345, "sierra leone": 451, "singapore": 830,
    "somalia": 520, "south africa": 560, "south sudan": 626,
    "spain": 230, "sri lanka": 780, "sudan": 625,
    "sweden": 380, "syria": 652, "taiwan": 713,
    "tajikistan": 702, "tanzania": 510, "thailand": 800,
    "trinidad and tobago": 52, "tunisia": 616,
    "turkey": 640, "uganda": 500, "ukraine": 369,
    "united arab emirates": 696, "uae": 696,
    "united kingdom": 200, "uk": 200, "great britain": 200,
    "united states": 2, "united states of america": 2, "usa": 2, "us": 2,
    "uruguay": 165, "uzbekistan": 704, "venezuela": 101,
    "vietnam": 816, "yemen": 678, "zambia": 551,
    "zimbabwe": 552,
}


def _to_gw_code(country: str) -> int | None:
    """Map country name → GW code, accept already-integer input."""

    c = country.strip()
    if not c:
        return None
    if c.isdigit():
        return int(c)
    return _GW_CODES.get(c.lower())


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
        country_str, start_year, end_year = _parse_query(query)
        gw_code = _to_gw_code(country_str)
        if gw_code is None:
            raise RetrievalError(
                f"UCDP requires Gleditsch & Ward country code (integer); "
                f"could not resolve {country_str!r}. Pass GW code directly "
                f"or use a country name from the built-in table "
                f"(e.g. 'Ukraine', 'Syria', 'Yemen')."
            )

        # UCDP GED filter — Country must be the integer GW code, NOT a name.
        # Per docs: pagesize defaults to 5 minimum; we use 50 as a sane chunk.
        params = {
            "pagesize": str(max(min(limit, 100), 5)),
            "page": "0",
            "Country": str(gw_code),
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
