"""Pixabay image search.

Replaces / augments Unsplash + Pexels for the ``photography`` domain.
Why Pixabay over the others:

* 5000 req / hour free tier (100x Unsplash's 50/h)
* Covers photos, illustrations, vectors, music, video (Unsplash only photos)
* Permissive Pixabay Content License (similar to public domain)

Get a key (free) at https://pixabay.com/api/docs/ — instant, no
credit card.  Configure via ``PIXABAY_API_KEY`` env or
``.omni/secrets.json::omni-hub/api/pixabay/default``.
"""

from __future__ import annotations

import os
from typing import Any

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord, http_get_json


SEARCH_URL = "https://pixabay.com/api/"
PIXABAY_SECRET_REF = "local:omni-hub/api/pixabay/default"


def _resolve_pixabay_key() -> str:
    env_key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        from ..secrets import resolve_secret_ref, SecretStoreError
    except ImportError:
        return ""
    try:
        return resolve_secret_ref(PIXABAY_SECRET_REF) or ""
    except SecretStoreError:
        return ""
    except Exception:                                            # noqa: BLE001
        return ""


class PixabaySource:
    """Pixabay image / video search."""

    name = "pixabay"
    tier = 1

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = api_key if api_key is not None else _resolve_pixabay_key()
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self.api_key:
            return "ok", "api key configured (5000/h free tier)"
        return "warn", "PIXABAY_API_KEY not set; register at pixabay.com/api/docs"

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
            raise RetrievalError("PIXABAY_API_KEY not set")

        data: Any = http_get_json(
            SEARCH_URL,
            params={
                "key": self.api_key,
                "q": query,
                "per_page": str(min(max(limit, 3), 200)),    # API min 3, max 200
                "safesearch": "true",
                "image_type": "photo",                       # photo|illustration|vector|all
            },
            headers={"Accept": "application/json"},
            timeout=self.timeout,
        )
        if not isinstance(data, dict):
            return []
        hits = data.get("hits", []) or []
        records: list[RetrievalRecord] = []
        for item in hits[:limit]:
            if not isinstance(item, dict):
                continue
            page_url = str(item.get("pageURL", ""))
            tags = str(item.get("tags", ""))
            largeImg = str(item.get("largeImageURL", ""))
            records.append(RetrievalRecord(
                source=self.name,
                title=f"{tags[:60]} (by {item.get('user', '')})",
                url=page_url,
                snippet=f"Tags: {tags} | Likes: {item.get('likes', 0)} | "
                        f"Downloads: {item.get('downloads', 0)}",
                score=float(item.get("likes", 0) or 0) / 1000.0,
                canonical_id=f"pixabay:{item.get('id', '')}",
                metadata={
                    "image_url": largeImg,
                    "preview_url": item.get("previewURL", ""),
                    "width": item.get("imageWidth", 0),
                    "height": item.get("imageHeight", 0),
                    "user": item.get("user", ""),
                    "likes": item.get("likes", 0),
                    "downloads": item.get("downloads", 0),
                    "views": item.get("views", 0),
                },
            ))
        return records


__all__ = ["PixabaySource"]
