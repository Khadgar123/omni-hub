"""International relations — ACLED + World Bank + IMF.

* **ACLED** (Armed Conflict Location & Event Data) — gold standard for
  real-time political violence and protest events.  Free non-commercial
  tier (institutional email gets full disaggregated data).  ACLED moved
  off the old ``email``+``key`` query-param auth in 2024: the legacy host
  ``api.acleddata.com`` is dead and keys were frozen 2025-09-15.  Current
  scheme (verified live 2026-05-29):

    1. OAuth2 password grant → Bearer token at ``acleddata.com/oauth/token``
       (``client_id=acled``, ``scope=authenticated``; access token 24h,
       refresh token 14d).
    2. Read events at ``acleddata.com/api/acled/read`` with
       ``Authorization: Bearer <token>``; filter per-column (``country=``,
       ``event_date=A|B&event_date_where=BETWEEN``), ``limit`` default 5000,
       ``page`` 1-based.  The unique id field is ``event_id_cnty`` (not
       ``data_id``); all values come back as strings.

  Credentials: ``ACLED_EMAIL`` + ``ACLED_PASSWORD`` env, or
  ``.omni/secrets.json`` (``store_api_key('account/acled/email', ...)`` /
  ``store_api_key('account/acled/password', ...)``).
* **World Bank** — open dev indicators, no key.  Endpoint
  ``api.worldbank.org/v2/indicator``.  Query model: indicator ID + country.
  We surface this as a free-text search over ~1500 indicator names.
* **IMF SDMX** — WEO / IFS / BOP datasets, no key.  Endpoint
  ``dataservices.imf.org/REST/SDMX_JSON.svc/DataStructure``.

We don't BUILD the full ACLED Python client — just the 1 endpoint we use.
Same for World Bank: skip ``wbdata`` dep, single HTTP call.
"""

from __future__ import annotations

import os
import re
import time

from .base import (
    DEFAULT_TIMEOUT_SEC,
    RetrievalError,
    RetrievalRecord,
    http_get_json,
    http_post_json,
)


ACLED_TOKEN_URL = "https://acleddata.com/oauth/token"
ACLED_READ_URL = "https://acleddata.com/api/acled/read"
ACLED_CLIENT_ID = "acled"
ACLED_EMAIL_REF = "local:omni-hub/account/acled/email"
ACLED_PASSWORD_REF = "local:omni-hub/account/acled/password"
WORLDBANK_INDICATORS = "https://api.worldbank.org/v2/indicator"
IMF_DATAFLOW = "https://dataservices.imf.org/REST/SDMX_JSON.svc/Dataflow"


def _resolve_secret(env_var: str, secret_ref: str) -> str:
    """Env var first, then ``.omni/secrets.json`` — same dual-resolution
    pattern as the other connectors."""

    val = os.environ.get(env_var, "").strip()
    if val:
        return val
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


# ---------------------------------------------------------------------------
# ACLED — needs free non-commercial key
# ---------------------------------------------------------------------------


class ACLEDSource:
    """Conflict events via ACLED OAuth2 (2024+ scheme).

    Credentials: ``ACLED_EMAIL`` + ``ACLED_PASSWORD`` env, or
    ``.omni/secrets.json::omni-hub/account/acled/{email,password}``.
    Register free at https://acleddata.com/user/register (institutional
    email unlocks full disaggregated event data).

    Query syntax: ``"<country>"`` or ``"<country>:<start>-<end>"`` years,
    e.g. ``"Ukraine"`` or ``"Sudan:2023-2024"``.
    """

    name = "acled"
    tier = 1

    def __init__(
        self,
        *,
        email: str | None = None,
        password: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.email = (
            email if email is not None
            else _resolve_secret("ACLED_EMAIL", ACLED_EMAIL_REF)
        )
        self.password = (
            password if password is not None
            else _resolve_secret("ACLED_PASSWORD", ACLED_PASSWORD_REF)
        )
        self.timeout = timeout
        self._token: str = ""
        self._token_expires_at: float = 0.0

    def check(self) -> tuple[str, str]:
        if self.email and self.password:
            return "ok", f"ACLED OAuth creds set (email={self.email[:3]}…)"
        return "off", "ACLED_EMAIL + ACLED_PASSWORD required (free; register at acleddata.com)"

    def _ensure_token(self) -> str:
        # 60s safety margin before the 24h access-token expiry.
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        if not (self.email and self.password):
            raise RetrievalError("ACLED_EMAIL + ACLED_PASSWORD required")
        payload = http_post_json(
            ACLED_TOKEN_URL,
            params={
                "username": self.email,
                "password": self.password,
                "grant_type": "password",
                "client_id": ACLED_CLIENT_ID,
                "scope": "authenticated",
            },
            content_type="application/x-www-form-urlencoded",
            timeout=self.timeout,
        )
        token = str((payload or {}).get("access_token", "")) if isinstance(payload, dict) else ""
        if not token:
            raise RetrievalError(f"ACLED oauth response missing access_token: {str(payload)[:200]}")
        self._token = token
        self._token_expires_at = time.time() + int((payload or {}).get("expires_in", 86400))
        return token

    @staticmethod
    def _parse_query(query: str) -> tuple[str, int | None, int | None]:
        """Parse ``country[:start-end]`` → (country, start_year, end_year)."""

        m = re.match(r"^(.+?):(\d{4})-(\d{4})$", query.strip())
        if m:
            return m.group(1).strip(), int(m.group(2)), int(m.group(3))
        return query.strip(), None, None

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        token = self._ensure_token()
        country, start_year, end_year = self._parse_query(query)

        params: dict[str, str] = {
            "country": country,
            "limit": str(min(max(limit, 1), 5000)),
            "page": "1",
        }
        if start_year and end_year:
            params["event_date"] = f"{start_year}-01-01|{end_year}-12-31"
            params["event_date_where"] = "BETWEEN"

        data = http_get_json(
            ACLED_READ_URL,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=self.timeout,
        )
        events = (data.get("data") or []) if isinstance(data, dict) else []
        records: list[RetrievalRecord] = []
        for item in events[:limit]:
            event_id = str(item.get("event_id_cnty", "") or item.get("data_id", ""))
            event_date = str(item.get("event_date", ""))
            actor1 = str(item.get("actor1", ""))
            actor2 = str(item.get("actor2", ""))
            country_name = str(item.get("country", ""))
            notes = str(item.get("notes", ""))
            event_type = str(item.get("event_type", ""))
            fatalities = int(float(item.get("fatalities", 0) or 0))
            records.append(RetrievalRecord(
                source=self.name,
                title=f"{event_date} [{event_type}] {actor1} vs {actor2} ({country_name})",
                url=str(item.get("source_scale", "") or item.get("source", "")),
                snippet=notes[:500],
                score=float(fatalities),
                canonical_id=f"acled:{event_id}" if event_id else "",
                metadata={
                    "event_date": event_date,
                    "event_type": event_type,
                    "sub_event_type": item.get("sub_event_type", ""),
                    "actor1": actor1,
                    "actor2": actor2,
                    "country": country_name,
                    "admin1": item.get("admin1", ""),
                    "location": item.get("location", ""),
                    "latitude": item.get("latitude", ""),
                    "longitude": item.get("longitude", ""),
                    "fatalities": fatalities,
                    "source": item.get("source", ""),
                },
            ))
        return records


# ---------------------------------------------------------------------------
# World Bank — no key
# ---------------------------------------------------------------------------


class WorldBankSource:
    """World Bank Open Data indicators.  No key required."""

    name = "world_bank"
    tier = 0

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return "ok", "anonymous (api.worldbank.org)"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        # World Bank API returns [meta, indicators]; pagination via per_page.
        data = http_get_json(
            WORLDBANK_INDICATORS,
            params={
                "format": "json",
                "per_page": str(min(limit * 4, 100)),     # filter client-side
                "source": "2",                             # WDI = main indicators
            },
            timeout=self.timeout,
        )
        if not isinstance(data, list) or len(data) < 2:
            return []
        q_norm = query.strip().lower()
        records: list[RetrievalRecord] = []
        for item in data[1]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", ""))
            note = str(item.get("sourceNote", ""))
            if q_norm not in (name.lower() + " " + note.lower()):
                continue
            ind_id = str(item.get("id", ""))
            records.append(RetrievalRecord(
                source=self.name,
                title=name,
                url=f"https://data.worldbank.org/indicator/{ind_id}" if ind_id else "",
                snippet=note[:500],
                score=0.0,
                canonical_id=f"wb:indicator:{ind_id}" if ind_id else "",
                metadata={
                    "indicator_id": ind_id,
                    "source_name": (item.get("source") or {}).get("value", ""),
                    "topics": [t.get("value", "") for t in (item.get("topics") or [])],
                },
            ))
            if len(records) >= limit:
                break
        return records


# ---------------------------------------------------------------------------
# IMF SDMX — no key
# ---------------------------------------------------------------------------


class IMFSource:
    """IMF SDMX dataflow catalogue.  No key required, JSON variant."""

    name = "imf"
    tier = 0

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return "ok", "anonymous (dataservices.imf.org SDMX_JSON)"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []

        data = http_get_json(IMF_DATAFLOW, timeout=self.timeout)
        # Walk the SDMX structure: Structure.Dataflows.Dataflow[]
        struct = data.get("Structure", {}) if isinstance(data, dict) else {}
        flows = (
            struct.get("Dataflows", {})
                  .get("Dataflow", [])
            if isinstance(struct, dict) else []
        )
        if isinstance(flows, dict):
            flows = [flows]

        q_norm = query.strip().lower()
        records: list[RetrievalRecord] = []
        for item in flows:
            if not isinstance(item, dict):
                continue
            # Name is a list of {@xml:lang, #text} or a dict.
            name_node = item.get("Name", {})
            if isinstance(name_node, list):
                name = next(
                    (
                        str(n.get("#text", "")) for n in name_node
                        if isinstance(n, dict) and n.get("@xml:lang") == "en"
                    ),
                    "",
                )
            elif isinstance(name_node, dict):
                name = str(name_node.get("#text", ""))
            else:
                name = ""
            flow_id = str(item.get("@id", ""))
            if q_norm not in name.lower() and q_norm not in flow_id.lower():
                continue
            records.append(RetrievalRecord(
                source=self.name,
                title=name or flow_id,
                url=(
                    f"https://dataservices.imf.org/REST/SDMX_JSON.svc/"
                    f"DataStructure/{flow_id}"
                    if flow_id else ""
                ),
                snippet=name[:500],
                score=0.0,
                canonical_id=f"imf:{flow_id}" if flow_id else "",
                metadata={"flow_id": flow_id},
            ))
            if len(records) >= limit:
                break
        return records
