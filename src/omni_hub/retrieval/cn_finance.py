"""Chinese financial data — Tushare (v0.22).

Tushare is a community-maintained A-stock / fund / commodity / macro
data API.  Free tier requires registration + token at
`https://tushare.pro/`.  POST API at ``http://api.tushare.pro/`` with
JSON body ``{api_name, token, params, fields}``.

Limitation: Tushare's API is **structured** (``stock_basic`` /
``daily`` / ``income`` / etc.), not free-text search.  Mapping
arbitrary user queries to the right Tushare endpoint requires either
an LLM-driven query planner or a dedicated CLI wrapper.  For v0.22 we
treat Tushare as a **probe-only source** in the cascade: ``check()``
reports the token state so ``retrieve-doctor`` can call it out, but
``retrieve()`` returns ``[]`` unless the query matches a recognisable
ticker pattern (e.g. ``600519.SH``, ``000001.SZ``, ``NVDA``) — in
which case we make a single ``stock_basic`` lookup.

For richer use: pin ``waditu/tushare`` under
``agent-harness/integrations/tushare/`` + add a structured-query skill
(``tushare-query --api daily --ts-code 600519.SH --start 2026-01-01``).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord


_TUSHARE_URL = "http://api.tushare.pro"

# Match A-stock (600519.SH / 000001.SZ) or up-to-5-char Latin tickers.
_TICKER_PATTERN = re.compile(
    r"\b(?:(?P<a_share>[0-9]{6}\.(?:SH|SZ|BJ))|(?P<latin>[A-Z]{1,5}))\b"
)


class TushareSource:
    """Tushare A-stock + macro data probe.  Tier-1 (free token)."""

    name = "tushare"
    tier = 1

    def __init__(
        self,
        *,
        token: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.token = token or os.environ.get("TUSHARE_TOKEN", "")
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if not self.token:
            return "off", "TUSHARE_TOKEN not set; register at tushare.pro"
        return "ok", "TUSHARE_TOKEN present"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip() or not self.token:
            return []
        match = _TICKER_PATTERN.search(query.upper())
        if not match:
            return []
        ts_code = match.group("a_share") or match.group("latin") or ""
        if not ts_code:
            return []
        payload = {
            "api_name": "stock_basic",
            "token": self.token,
            "params": {"ts_code": ts_code} if "." in ts_code else {"symbol": ts_code},
            "fields": "ts_code,symbol,name,area,industry,market,list_date",
        }
        try:
            data = self._post(payload)
        except RetrievalError:
            return []
        items = (data or {}).get("data", {}).get("items") or []
        fields = (data or {}).get("data", {}).get("fields") or []
        records: list[RetrievalRecord] = []
        for row in items[:limit]:
            if not isinstance(row, list):
                continue
            mapping = dict(zip(fields, row, strict=False))
            ts = str(mapping.get("ts_code", ""))
            name = str(mapping.get("name", ""))
            industry = str(mapping.get("industry", ""))
            records.append(RetrievalRecord(
                source=self.name,
                title=f"{ts} {name}".strip(),
                url=f"https://tushare.pro/document/2?doc_id=25&query={ts}",
                snippet=f"行业: {industry}  上市: {mapping.get('list_date','')}",
                score=0.0,
                canonical_id=f"tushare:ts_code:{ts}" if ts else "",
                metadata={
                    "ts_code": ts,
                    "industry": industry,
                    "market": mapping.get("market", ""),
                    "area": mapping.get("area", ""),
                    "lang": "zh",
                },
            ))
        return records

    # ---- internals ----------------------------------------------

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            _TUSHARE_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            raise RetrievalError(f"tushare HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RetrievalError(f"tushare unreachable: {exc.reason}") from exc
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RetrievalError(f"tushare non-JSON: {exc}") from exc


__all__ = ["TushareSource"]
