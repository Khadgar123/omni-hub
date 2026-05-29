"""Photography — Unsplash + Pexels free metadata APIs.

Both APIs are free with attribution.  We DO NOT download the image bytes;
the cascade just surfaces title / URL / metadata so the user can decide
whether to capture.

Auth model:
* Unsplash: ``Authorization: Client-ID <key>`` header. 50 req/h on the
  Demo tier (free, instant). 5000 req/h on Production tier (free, ~5 min
  reviewed approval).
* Pexels: ``Authorization: <key>`` header. Free, no commercial caps.

Set the key via ``UNSPLASH_ACCESS_KEY`` / ``PEXELS_API_KEY`` env vars OR
``.omni/secrets.json`` (``store_api_key('api/unsplash/default', ...)`` /
``store_api_key('api/pexels/default', ...)``) — same dual-resolution as
the other connectors.
"""

from __future__ import annotations

import os

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord, http_get_json
from .health import env_var_probe


UNSPLASH_SEARCH = "https://api.unsplash.com/search/photos"
PEXELS_SEARCH = "https://api.pexels.com/v1/search"

UNSPLASH_SECRET_REF = "local:omni-hub/api/unsplash/default"
PEXELS_SECRET_REF = "local:omni-hub/api/pexels/default"


def _resolve_secret(env_var: str, secret_ref: str) -> str:
    """Env var first, then ``.omni/secrets.json`` — same dual-resolution
    pattern as the other connectors (web_search / pixabay / ucdp / reddit)."""

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


class UnsplashSource:
    """Unsplash search.  Requires ``UNSPLASH_ACCESS_KEY`` env var."""

    name = "unsplash"
    tier = 1          # free key but registration required

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = (
            api_key
            if api_key is not None
            else _resolve_secret("UNSPLASH_ACCESS_KEY", UNSPLASH_SECRET_REF)
        )
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self.api_key:
            return "ok", "Unsplash key configured (env or secrets.json)"
        return env_var_probe("UNSPLASH_ACCESS_KEY")

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
            raise RetrievalError("UNSPLASH_ACCESS_KEY not set")

        data = http_get_json(
            UNSPLASH_SEARCH,
            params={"query": query, "per_page": str(min(limit, 30))},
            headers={"Authorization": f"Client-ID {self.api_key}"},
            timeout=self.timeout,
        )
        results = data.get("results", []) if isinstance(data, dict) else []
        records: list[RetrievalRecord] = []
        for item in results[:limit]:
            user = (item.get("user") or {}).get("name", "")
            description = item.get("description") or item.get("alt_description") or ""
            page_url = (item.get("links") or {}).get("html", "")
            photo_id = item.get("id", "")
            records.append(RetrievalRecord(
                source=self.name,
                title=description or f"Photo by {user}",
                url=page_url,
                snippet=description[:400],
                score=float(item.get("likes", 0)),
                canonical_id=f"unsplash:{photo_id}" if photo_id else "",
                metadata={
                    "photographer": user,
                    "image_url": (item.get("urls") or {}).get("regular", ""),
                    "width": item.get("width"),
                    "height": item.get("height"),
                    "likes": item.get("likes", 0),
                    "color": item.get("color", ""),
                },
            ))
        return records


class PexelsSource:
    """Pexels search.  Requires ``PEXELS_API_KEY`` env var.  No commercial cap."""

    name = "pexels"
    tier = 1

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = (
            api_key
            if api_key is not None
            else _resolve_secret("PEXELS_API_KEY", PEXELS_SECRET_REF)
        )
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self.api_key:
            return "ok", "Pexels key configured (env or secrets.json)"
        return env_var_probe("PEXELS_API_KEY")

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
            raise RetrievalError("PEXELS_API_KEY not set")

        data = http_get_json(
            PEXELS_SEARCH,
            params={"query": query, "per_page": str(min(limit, 80))},
            headers={"Authorization": self.api_key},
            timeout=self.timeout,
        )
        photos = data.get("photos", []) if isinstance(data, dict) else []
        records: list[RetrievalRecord] = []
        for item in photos[:limit]:
            photographer = item.get("photographer", "")
            alt = item.get("alt") or f"Photo by {photographer}"
            photo_id = item.get("id", "")
            records.append(RetrievalRecord(
                source=self.name,
                title=alt,
                url=item.get("url", ""),
                snippet=alt[:400],
                score=0.0,                     # Pexels has no likes
                canonical_id=f"pexels:{photo_id}" if photo_id else "",
                metadata={
                    "photographer": photographer,
                    "photographer_url": item.get("photographer_url", ""),
                    "image_url": (item.get("src") or {}).get("large", ""),
                    "width": item.get("width"),
                    "height": item.get("height"),
                    "avg_color": item.get("avg_color", ""),
                },
            ))
        return records
