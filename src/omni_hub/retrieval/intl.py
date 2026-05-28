"""International relations — ACLED + World Bank + IMF.

* **ACLED** (Armed Conflict Location & Event Data) — gold standard for
  real-time political violence and protest events.  Free key for
  non-commercial; commercial requires license.  Endpoint
  ``api.acleddata.com/acled/read``.  Auth = ``email`` + ``key`` query
  params (both env vars).
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

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord, http_get_json
from .health import env_var_probe


ACLED_URL = "https://api.acleddata.com/acled/read"
WORLDBANK_INDICATORS = "https://api.worldbank.org/v2/indicator"
IMF_DATAFLOW = "https://dataservices.imf.org/REST/SDMX_JSON.svc/Dataflow"


# ---------------------------------------------------------------------------
# ACLED — needs free non-commercial key
# ---------------------------------------------------------------------------


class ACLEDSource:
    """Conflict events. Needs ``ACLED_EMAIL`` + ``ACLED_KEY`` env vars."""

    name = "acled"
    tier = 1

    def __init__(
        self,
        *,
        email: str | None = None,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.email = email or os.environ.get("ACLED_EMAIL", "")
        self.api_key = api_key or os.environ.get("ACLED_KEY", "")
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self.email and self.api_key:
            return "ok", f"ACLED_EMAIL={self.email[:4]}… + ACLED_KEY set"
        return "off", "ACLED_EMAIL and ACLED_KEY required (free non-commercial)"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        if not (self.email and self.api_key):
            raise RetrievalError("ACLED_EMAIL + ACLED_KEY required")

        # ACLED uses LIKE filters via ``=*X*`` magic on text fields.
        data = http_get_json(
            ACLED_URL,
            params={
                "email": self.email,
                "key": self.api_key,
                "actor1": f"*{query}*",
                "limit": str(min(limit, 50)),
            },
            timeout=self.timeout,
        )
        events = data.get("data", []) if isinstance(data, dict) else []
        records: list[RetrievalRecord] = []
        for item in events[:limit]:
            event_id = str(item.get("data_id", "") or item.get("event_id", ""))
            event_date = str(item.get("event_date", ""))
            actor1 = str(item.get("actor1", ""))
            actor2 = str(item.get("actor2", ""))
            country = str(item.get("country", ""))
            notes = str(item.get("notes", ""))
            event_type = str(item.get("event_type", ""))
            fatalities = int(item.get("fatalities", 0) or 0)
            records.append(RetrievalRecord(
                source=self.name,
                title=f"{event_date} [{event_type}] {actor1} vs {actor2} ({country})",
                url=str(item.get("source_scale", "")),       # ACLED stores source link here
                snippet=notes[:500],
                score=float(fatalities),
                canonical_id=f"acled:{event_id}" if event_id else "",
                metadata={
                    "event_date": event_date,
                    "event_type": event_type,
                    "actor1": actor1,
                    "actor2": actor2,
                    "country": country,
                    "fatalities": fatalities,
                    "interaction": item.get("interaction", ""),
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
