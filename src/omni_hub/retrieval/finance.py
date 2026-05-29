"""Finance — SEC EDGAR + FRED.

Lifted from the *patterns* in ``anthropics/financial-services`` (April 2026
official skill bundle); we re-implement as stdlib connectors rather than
pinning the full bundle because the bundle includes prompts and CLI that
duplicate omni-hub's CLI surface.  Two thin HTTP wrappers — no SDK.

* **SEC EDGAR full-text search** — every 10-K/10-Q/8-K/13F since 1994.
  Endpoint ``efts.sec.gov/LATEST/search-index?q=...``.  **No key**; SEC
  asks for a polite ``User-Agent`` with name + email (``SEC_USER_AGENT``
  env var, defaults to omni-hub's default UA).
* **FRED** — 800k+ macro time series from St. Louis Fed.  Free key required
  (``FRED_API_KEY`` env var).  Endpoint
  ``api.stlouisfed.org/fred/series/search``.

These two cover the user-facing edge of ``finance`` and ``policy``
domains: filings + macro indicators. Live prices (yfinance, etc.) are
out of scope — omni-hub is a knowledge harness, not a trading platform.
"""

from __future__ import annotations

import os
import re

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord, http_get_json
from .health import env_var_probe


EDGAR_FT_SEARCH = "https://efts.sec.gov/LATEST/search-index"

# Human-readable labels for the common form types so synthesized answers
# read naturally ("Annual report (10-K)" not bare "10-K").
_EDGAR_FORM_LABELS = {
    "10-K": "Annual report",
    "10-Q": "Quarterly report",
    "8-K": "Current report (material event)",
    "6-K": "Foreign issuer report",
    "20-F": "Foreign annual report",
    "S-1": "IPO registration",
    "424B4": "Prospectus",
    "DEF 14A": "Proxy statement",
    "40-F": "Canadian annual report",
}
FRED_SEARCH = "https://api.stlouisfed.org/fred/series/search"


# ---------------------------------------------------------------------------
# SEC EDGAR — no key, polite UA required
# ---------------------------------------------------------------------------


SEC_UA_SECRET_REF = "local:omni-hub/api/edgar/user_agent"


def _resolve_sec_user_agent() -> str:
    env_v = os.environ.get("SEC_USER_AGENT", "").strip()
    if env_v:
        return env_v
    try:
        from ..secrets import resolve_secret_ref, SecretStoreError
    except ImportError:
        return ""
    try:
        return resolve_secret_ref(SEC_UA_SECRET_REF) or ""
    except SecretStoreError:
        return ""
    except Exception:                                            # noqa: BLE001
        return ""


class EdgarSource:
    """SEC EDGAR full-text filing search.  No key; needs polite UA."""

    name = "edgar"
    tier = 0          # works anonymous; SEC_USER_AGENT just polite-ups

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        resolved = _resolve_sec_user_agent()
        self.user_agent = (
            user_agent
            or resolved
            or "omni-hub/0.10 (personal-knowledge-harness)"
        )
        # Track whether the polite-pool UA came from env/secrets vs default
        self._polite = bool(user_agent or resolved)
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self._polite:
            return "ok", "SEC_USER_AGENT polite-set"
        return "warn", "default UA; set SEC_USER_AGENT='Name email@x.com' to be polite"

    # Core company filing forms — what users almost always mean by "the
    # company's filings".  Excludes fund/adviser noise (NPORT-P, 13F-HR,
    # 497, N-CEN, etc.) that floods full-text search for common terms like
    # "Federal Reserve" or a ticker, because thousands of funds *hold* the
    # stock and mention it in routine filings.
    _CORE_FORMS = "10-K,10-Q,8-K,6-K,20-F,S-1,424B4,DEF 14A,40-F"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        params: dict[str, str] = {"q": query, "hits": str(min(limit, 25))}
        # v0.45: for finance / enterprise domains, restrict to core company
        # forms so fund-holding noise (NPORT-P etc.) doesn't drown the
        # actual company filings.  Other domains keep full-text breadth.
        if domain in {"finance", "enterprise"}:
            params["forms"] = self._CORE_FORMS
        data = http_get_json(
            EDGAR_FT_SEARCH,
            params=params,
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
        )
        hits = (
            (data.get("hits") or {}).get("hits", [])
            if isinstance(data, dict) else []
        )
        records: list[RetrievalRecord] = []
        for hit in hits[:limit]:
            src = hit.get("_source", {}) if isinstance(hit, dict) else {}
            adsh = str(hit.get("_id", "") or src.get("adsh", ""))
            form = str(src.get("form", ""))
            file_date = str(src.get("file_date", ""))
            display_names = src.get("display_names", []) or []
            issuer = str(display_names[0]) if display_names else ""
            # accession to URL:
            #   https://www.sec.gov/Archives/edgar/data/<CIK>/<adsh-no-dashes>/<adsh>-index.htm
            cik = str((src.get("ciks") or [""])[0])
            accession_clean = adsh.replace("-", "") if adsh else ""
            url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                f"{accession_clean}/{adsh}-index.htm"
                if cik and adsh and accession_clean
                else ""
            )
            # Richer snippet: form-type + filer + date + any FTS highlight
            # so the synthesizer/judge can tell what the filing actually is.
            highlight = ""
            hl = hit.get("highlight") if isinstance(hit, dict) else None
            if isinstance(hl, dict):
                frags = []
                for v in hl.values():
                    if isinstance(v, list):
                        frags.extend(str(x) for x in v)
                highlight = re.sub(r"<[^>]+>", "", " … ".join(frags))[:300]
            form_label = _EDGAR_FORM_LABELS.get(form, form)
            snippet_parts = [p for p in [
                f"{form_label} ({form})" if form_label != form else form,
                f"filed {file_date}" if file_date else "",
                ", ".join(str(n) for n in display_names[:2]),
                highlight,
            ] if p]
            records.append(RetrievalRecord(
                source=self.name,
                title=f"{form} — {issuer}" if form and issuer else (form or issuer),
                url=url,
                snippet=" · ".join(snippet_parts)[:600],
                score=0.0,
                canonical_id=f"edgar:{adsh}" if adsh else "",
                metadata={
                    "form": form,
                    "file_date": file_date,
                    "cik": cik,
                    "accession_number": adsh,
                    "issuer": issuer,
                },
            ))
        return records


# ---------------------------------------------------------------------------
# FRED — free key required
# ---------------------------------------------------------------------------


FRED_SECRET_REF = "local:omni-hub/api/fred/default"


def _resolve_fred_key() -> str:
    env_key = os.environ.get("FRED_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        from ..secrets import resolve_secret_ref, SecretStoreError
    except ImportError:
        return ""
    try:
        return resolve_secret_ref(FRED_SECRET_REF) or ""
    except SecretStoreError:
        return ""
    except Exception:                                            # noqa: BLE001
        return ""


class FREDSource:
    """St. Louis Fed FRED series search.

    Key via ``FRED_API_KEY`` env or ``.omni/secrets.json::omni-hub/api/fred/default``.
    """

    name = "fred"
    tier = 1

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = api_key if api_key is not None else _resolve_fred_key()
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self.api_key:
            return "ok", "api key configured"
        return env_var_probe("FRED_API_KEY")

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
            raise RetrievalError("FRED_API_KEY not set")

        data = http_get_json(
            FRED_SEARCH,
            params={
                "search_text": query,
                "api_key": self.api_key,
                "file_type": "json",
                "limit": str(min(limit, 25)),
                "order_by": "popularity",
                "sort_order": "desc",
            },
            timeout=self.timeout,
        )
        series = data.get("seriess", []) if isinstance(data, dict) else []
        records: list[RetrievalRecord] = []
        for item in series[:limit]:
            sid = str(item.get("id", ""))
            title = str(item.get("title", ""))
            popularity = int(item.get("popularity", 0) or 0)
            records.append(RetrievalRecord(
                source=self.name,
                title=title,
                url=f"https://fred.stlouisfed.org/series/{sid}" if sid else "",
                snippet=str(item.get("notes", ""))[:500],
                score=float(popularity),
                canonical_id=f"fred:{sid}" if sid else "",
                metadata={
                    "series_id": sid,
                    "frequency": item.get("frequency", ""),
                    "units": item.get("units", ""),
                    "seasonal_adjustment": item.get("seasonal_adjustment", ""),
                    "observation_start": item.get("observation_start", ""),
                    "observation_end": item.get("observation_end", ""),
                    "popularity": popularity,
                },
            ))
        return records
