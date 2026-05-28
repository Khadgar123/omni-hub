"""X (Twitter) retrieval via twitterapi.io — paid PAYG at $0.15 / 1K tweets.

Why: native X API jumped to $5/1K PPU in Feb 2026 (33× this price).
snscrape is dead since 2023; twscrape requires an account pool we WILL
NOT maintain.  twitterapi.io is the cheapest 2026 PAYG mirror with no
minimum and ~$0.10 free credit on signup.

Auth: ``TWITTERAPI_IO_KEY`` env var → ``Authorization: Bearer <key>``.

Fallback: ``socialdata.tools`` (constructor arg ``alt_endpoint=...``) at
$0.20/1K — same record shape, swap by changing one env var.  Use this
when twitterapi.io is briefly down.

Cost ceiling: at 30k reads/month → $4.50.  Well under the user's
single-user budget; cascaded behind cheaper free sources by default.
"""

from __future__ import annotations

import os

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord, http_get_json
from .health import env_var_probe


DEFAULT_ENDPOINT = "https://api.twitterapi.io/twitter/tweet/advanced_search"


class TwitterApiIoSource:
    """X advanced-search via twitterapi.io.  Paid, PAYG.

    Standard query syntax (same as X UI): ``"agent skills" since:2026-05-01``.
    """

    name = "x_twitter"
    tier = 2          # paid key

    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = api_key or os.environ.get("TWITTERAPI_IO_KEY", "")
        self.endpoint = endpoint
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return env_var_probe("TWITTERAPI_IO_KEY", tier=2)

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
            raise RetrievalError("TWITTERAPI_IO_KEY not set")

        data = http_get_json(
            self.endpoint,
            params={
                "query": query,
                "queryType": "Latest",
                "limit": str(min(limit, 20)),
            },
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        tweets = data.get("tweets", []) if isinstance(data, dict) else []
        records: list[RetrievalRecord] = []
        for item in tweets[:limit]:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("id", ""))
            author = item.get("author") or {}
            username = str(author.get("userName", "") or author.get("screen_name", ""))
            text = str(item.get("text", ""))
            metrics = {
                "like_count": int(item.get("likeCount", 0) or 0),
                "retweet_count": int(item.get("retweetCount", 0) or 0),
                "reply_count": int(item.get("replyCount", 0) or 0),
                "view_count": int(item.get("viewCount", 0) or 0),
            }
            url = (
                f"https://x.com/{username}/status/{tid}"
                if username and tid else ""
            )
            records.append(RetrievalRecord(
                source=self.name,
                title=f"@{username}: {text[:60]}…" if text else f"@{username}",
                url=url,
                snippet=text[:500],
                # Score: like_count + 2*retweet_count (retweets weigh more)
                score=float(metrics["like_count"] + 2 * metrics["retweet_count"]),
                canonical_id=f"x:tweet:{tid}" if tid else "",
                metadata={
                    "author": username,
                    "author_name": author.get("name", ""),
                    "created_at": item.get("createdAt", ""),
                    "lang": item.get("lang", ""),
                    **metrics,
                },
            ))
        return records
