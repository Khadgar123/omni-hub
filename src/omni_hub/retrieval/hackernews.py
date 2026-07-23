"""Hacker News (Algolia search API) connector.

Why HN over Reddit / Twitter for the ``social_en`` cascade:

* Free, no API key, no OAuth
* High signal-to-noise: YC/founder/AI/tech crowd, mostly long-form text
* Algolia-backed full-text search of all stories + comments since 2007
* Time filters built-in (numericFilters=created_at_i>...)

API docs: https://hn.algolia.com/api/

This connector defaults to story-only search (skips noisy comment hits)
sorted by relevance.  Pass ``+by-date`` in the query to switch to
recency-sorted (e.g. ``"transformer +by-date"``).
"""

from __future__ import annotations

from typing import Any

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord, http_get_json


SEARCH_URL = "https://hn.algolia.com/api/v1/search"
SEARCH_BY_DATE_URL = "https://hn.algolia.com/api/v1/search_by_date"


class HackerNewsSource:
    """Search Hacker News stories via the Algolia public API."""

    name = "hackernews"
    tier = 0          # free, no auth

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return "ok", "hn.algolia.com public search API, no auth"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        query = query.strip()
        if not query:
            return []

        # Suffix "+by-date" → sort by recency (newest first) instead of
        # relevance.  Useful for "what was discussed today about X".
        by_date = query.endswith("+by-date")
        if by_date:
            query = query[: -len("+by-date")].strip()
            url = SEARCH_BY_DATE_URL
        else:
            url = SEARCH_URL

        data: Any = http_get_json(
            url,
            params={
                "query": query,
                "tags": "story",                             # skip noisy comment results
                "hitsPerPage": str(min(max(limit, 1), 30)),
            },
            headers={"Accept": "application/json"},
            timeout=self.timeout,
        )
        if not isinstance(data, dict):
            return []
        hits = data.get("hits", []) or []
        records: list[RetrievalRecord] = []
        for hit in hits[:limit]:
            if not isinstance(hit, dict):
                continue
            title = str(hit.get("title", "") or hit.get("story_title", ""))
            story_url = str(hit.get("url", "") or "")
            object_id = str(hit.get("objectID", ""))
            hn_url = f"https://news.ycombinator.com/item?id={object_id}" if object_id else ""
            # Prefer the external URL; fall back to the HN thread URL when
            # the story is an Ask HN / Show HN with no external link.
            final_url = story_url or hn_url
            points = hit.get("points", 0) or 0
            num_comments = hit.get("num_comments", 0) or 0
            author = str(hit.get("author", ""))
            created = str(hit.get("created_at", ""))
            story_text = str(hit.get("story_text", "") or "")
            snippet_bits = []
            if story_text:
                snippet_bits.append(story_text[:300])
            snippet_bits.append(
                f"by {author} | {points} pts | {num_comments} comments | "
                f"thread: {hn_url}",
            )
            records.append(RetrievalRecord(
                source=self.name,
                title=title,
                url=final_url,
                snippet=" — ".join(snippet_bits)[:600],
                score=float(points) / 100.0,
                canonical_id=f"hn:{object_id}" if object_id else "",
                metadata={
                    "author": author,
                    "points": points,
                    "num_comments": num_comments,
                    "created_at": created,
                    "hn_thread": hn_url,
                    "external_url": story_url,
                },
            ))
        return records


__all__ = ["HackerNewsSource"]
