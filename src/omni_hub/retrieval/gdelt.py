"""GDELT 2.0 — 100+ languages of news events, 15-minute refresh, fully free.

Used for any "what's happening with X recently" query — policy / IR /
finance / market shifts.  Coverage is 47 years deep (1979-present)
in the historical project, with 15-minute updates on the 2.0 stream.

We use the DOC API (article-level search), not the GKG/Events tables —
DOC is enough for surfacing news links into the cascade, and avoids
the BigQuery dance.

The GDELT DOC endpoint is known to flap: requests randomly return 502
or RST mid-stream during high traffic.  We retry twice with short
backoff, and cache successful responses for 5 minutes so cascade
fan-outs with the same query don't re-hit the upstream.
"""

from __future__ import annotations

import hashlib
import time
import urllib.parse
from typing import Any

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord, http_get_json


DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
# v0.43.5: stress test showed only 25% raw success rate; bumped cache
# to 15 minutes (still well under GDELT's own 15-min refresh cycle)
# and retry count to 3 to absorb the flaky periods.
_CACHE_TTL_S = 900                              # 15 minutes (was 5)
_RETRY_COUNT = 3                                # (was 2)
_RETRY_BACKOFF_S = 2.0                          # (was 1.5)


class GDELTSource:
    name = "gdelt"
    tier = 0

    # Class-level cache: shared across instances since GDELT data is
    # the same for everyone (no auth, no user-scope).
    _cache: dict[str, tuple[float, Any]] = {}

    def __init__(self, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return "ok", "anonymous, 15-min refresh (api.gdeltproject.org)"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        # GDELT DOC API uses a custom DSL; quote multi-word terms.
        gdelt_query = query if " " not in query.strip() else f'"{query}"'
        params = {
            "query": gdelt_query,
            "mode": "artlist",
            "maxrecords": str(min(limit, 75)),
            "format": "json",
            "sort": "datedesc",
        }
        url = f"{DOC_API}?{urllib.parse.urlencode(params)}"

        # Cache lookup — 5-minute TTL keyed on full URL so different
        # limits / queries each cache independently.
        cached = self._cache.get(url)
        if cached and time.time() - cached[0] < _CACHE_TTL_S:
            data = cached[1]
        else:
            data = self._fetch_with_retry(url)
            self._cache[url] = (time.time(), data)
        articles = (data.get("articles") or []) if isinstance(data, dict) else []

        records: list[RetrievalRecord] = []
        for art in articles[:limit]:
            art_url = art.get("url", "")
            # News articles often have ?utm_*= query strings appended by
            # republishers; canonical_id strips the query so reposts of
            # the same article merge.
            canonical = ""
            if art_url:
                base = art_url.split("?", 1)[0].split("#", 1)[0]
                canonical = "gdelt:" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
            records.append(RetrievalRecord(
                source=self.name,
                title=art.get("title", ""),
                url=art_url,
                snippet=(art.get("seendate") or "") + " · " + art.get("domain", ""),
                score=1.0,
                canonical_id=canonical,
                metadata={
                    "seendate": art.get("seendate", ""),
                    "language": art.get("language", ""),
                    "source_country": art.get("sourcecountry", ""),
                    "outlet_domain": art.get("domain", ""),
                    "social_image": art.get("socialimage", ""),
                },
            ))
        return records

    def _fetch_with_retry(self, url: str) -> Any:
        """GET ``url`` with up to ``_RETRY_COUNT`` retries.

        GDELT DOC API returns transient 502 / RST under load.  Short
        linear backoff between attempts; if everything fails, the last
        exception propagates so the cascade can fail-soft-skip.
        """

        last_exc: Exception | None = None
        for attempt in range(_RETRY_COUNT + 1):
            try:
                return http_get_json(url, timeout=self.timeout)
            except RetrievalError as exc:
                last_exc = exc
                if attempt < _RETRY_COUNT:
                    time.sleep(_RETRY_BACKOFF_S * (attempt + 1))
                    continue
                raise
        if last_exc:                                                # pragma: no cover
            raise last_exc
        return {}                                                    # pragma: no cover
