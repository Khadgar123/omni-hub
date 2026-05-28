"""Mastodon Fediverse search connector.

Mastodon's public search endpoint exposes recent posts (statuses) and
accounts without authentication.  Useful as a Twitter/X replacement —
the AI / policy / open-source crowd has been steadily migrating to
Fediverse instances since 2023.

We query ``mastodon.social`` by default — the largest public instance,
federated with most others.  Set ``OMNI_MASTODON_INSTANCE`` to point to
a different instance (e.g. ``hachyderm.io`` for tech, ``fosstodon.org``
for FOSS folks).

Endpoint: ``https://<instance>/api/v2/search?q=<query>&type=statuses``

Hard constraint: stdlib only.  No ``Mastodon.py`` SDK.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord, http_get_json


DEFAULT_INSTANCE = "mastodon.social"


def _instance() -> str:
    return (os.environ.get("OMNI_MASTODON_INSTANCE") or DEFAULT_INSTANCE).strip()


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class MastodonSource:
    """Mastodon public-search connector (Fediverse statuses)."""

    name = "mastodon"
    tier = 0          # no auth on public-search endpoint

    def __init__(
        self,
        *,
        instance: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.instance = (instance or _instance()).strip()
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return "ok", f"Fediverse search via {self.instance} (public, no auth)"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        """Retrieve Mastodon posts.

        Mastodon 4.x requires OAuth to search statuses by free text.  Two
        unauthenticated paths still work:

        * ``#hashtag`` → ``/api/v1/timelines/tag/<tag>`` (no auth needed)
        * free text → ``/api/v2/search?type=hashtags`` (returns matching
          hashtag names; user can then call again with #hashtag)

        We auto-detect: a query starting with ``#`` → tag timeline.
        Otherwise we fall back to hashtag suggestions (tag-prefixed
        searches still return useful pointers to active discussions).
        """

        q = query.strip()
        if not q:
            return []

        if q.startswith("#"):
            tag = q.lstrip("#").strip().lower()
            url = f"https://{self.instance}/api/v1/timelines/tag/{tag}"
            data: Any = http_get_json(
                url,
                params={"limit": str(min(max(limit, 1), 40))},
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
            statuses = data if isinstance(data, list) else []
        else:
            # Fallback: hashtag suggestions (free-text search still
            # works for hashtags + accounts even without OAuth).
            url = f"https://{self.instance}/api/v2/search"
            data = http_get_json(
                url,
                params={
                    "q": q,
                    "type": "hashtags",
                    "limit": str(min(max(limit, 1), 20)),
                },
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
            if not isinstance(data, dict):
                return []
            tags = data.get("hashtags") or []
            # Convert hashtag hits → records (pointers; user can drill
            # in with #tag query for the actual timeline).
            records: list[RetrievalRecord] = []
            for tag in tags[:limit]:
                if not isinstance(tag, dict):
                    continue
                name = str(tag.get("name", ""))
                tag_url = str(tag.get("url", ""))
                history = tag.get("history") or []
                recent_use = (
                    sum(int((h or {}).get("uses", 0) or 0) for h in history[:7])
                    if isinstance(history, list) else 0
                )
                records.append(RetrievalRecord(
                    source=self.name,
                    title=f"#{name}",
                    url=tag_url,
                    snippet=(
                        f"hashtag · 7-day uses: {recent_use} · "
                        f"call again with query='#{name}' for actual timeline"
                    ),
                    score=float(recent_use) / 1000.0,
                    canonical_id=f"masto-tag:{self.instance}:{name}",
                    metadata={
                        "kind": "hashtag",
                        "instance": self.instance,
                        "name": name,
                        "recent_use_7d": recent_use,
                    },
                ))
            return records
        records: list[RetrievalRecord] = []
        for st in statuses[:limit]:
            if not isinstance(st, dict):
                continue
            text = _strip_html(str(st.get("content", "")))
            url_post = str(st.get("url", "") or st.get("uri", ""))
            account = st.get("account") or {}
            handle = str(account.get("acct", ""))
            display = str(account.get("display_name", ""))
            cid = str(st.get("id", ""))
            records.append(RetrievalRecord(
                source=self.name,
                title=f"@{handle}: {text[:80]}",
                url=url_post,
                snippet=text[:600],
                score=float(st.get("favourites_count", 0) or 0) / 100.0,
                canonical_id=f"masto:{self.instance}:{cid}" if cid else "",
                metadata={
                    "instance": self.instance,
                    "handle": handle,
                    "display_name": display,
                    "created_at": st.get("created_at", ""),
                    "favourites_count": st.get("favourites_count", 0),
                    "reblogs_count": st.get("reblogs_count", 0),
                    "replies_count": st.get("replies_count", 0),
                    "language": st.get("language", ""),
                },
            ))
        return records


__all__ = ["MastodonSource"]
