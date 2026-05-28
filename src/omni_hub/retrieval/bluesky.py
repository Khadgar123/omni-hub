"""Bluesky (AT Protocol) public-search connector.

Replaces TwitterAPI.io for the ``social_en`` domain.  Bluesky's public
search endpoint requires **no authentication** and surfaces every public
post — by design (AT Protocol is content-addressed and federation-
friendly).  As of 2026 Bluesky's western tech / AI / policy density has
overtaken pre-2023 Twitter, so this is the natural replacement source.

API: ``https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts``.

Hard constraint: stdlib only.  No ``atproto`` SDK dependency.
"""

from __future__ import annotations

from typing import Any

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord, http_get_json


SEARCH_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"


class BlueskySource:
    """Bluesky public post search.  No API key required."""

    name = "bluesky"
    tier = 0          # no auth, no recycle risk

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return "ok", "public AT Protocol endpoint, no auth"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        data: Any = http_get_json(
            SEARCH_URL,
            params={
                "q": query,
                "limit": str(min(max(limit, 1), 25)),       # endpoint caps at 25
                "sort": "latest",                            # 'latest' or 'top'
            },
            headers={"Accept": "application/json"},
            timeout=self.timeout,
        )
        posts = data.get("posts", []) if isinstance(data, dict) else []
        records: list[RetrievalRecord] = []
        for post in posts[:limit]:
            if not isinstance(post, dict):
                continue
            record = post.get("record") or {}
            author = post.get("author") or {}
            text = str(record.get("text", "")).strip()
            uri = str(post.get("uri", ""))                  # at:// internal
            cid = str(post.get("cid", ""))
            handle = str(author.get("handle", ""))
            # Build the human-readable URL from AT URI:
            #   at://did:plc:xxx/app.bsky.feed.post/<rkey>  →
            #   https://bsky.app/profile/<handle>/post/<rkey>
            rkey = uri.rsplit("/", 1)[-1] if uri else ""
            url = f"https://bsky.app/profile/{handle}/post/{rkey}" if (handle and rkey) else uri
            records.append(RetrievalRecord(
                source=self.name,
                title=f"@{handle}: {text[:80]}",
                url=url,
                snippet=text[:500],
                score=float(post.get("likeCount", 0) or 0) / 100.0,
                canonical_id=f"bsky:{cid}" if cid else "",
                metadata={
                    "handle": handle,
                    "display_name": author.get("displayName", ""),
                    "indexed_at": post.get("indexedAt", ""),
                    "like_count": post.get("likeCount", 0),
                    "repost_count": post.get("repostCount", 0),
                    "reply_count": post.get("replyCount", 0),
                },
            ))
        return records


__all__ = ["BlueskySource"]
