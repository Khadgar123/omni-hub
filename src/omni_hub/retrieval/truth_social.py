"""Truth Social via RSSHub.

Truth Social has no public API.  RSSHub (open-source feed aggregator)
exposes per-user feeds at::

    https://<rsshub-base>/truthsocial/<username>

We default to the public ``https://rsshub.app`` instance — same backend
the project already uses for gov_cn / stats_gov_cn / court_gov_cn /
pbc_gov_cn connectors.  Override with ``OMNI_RSSHUB_BASE`` env var or
``self-host`` (see docker-compose example in docs).

Usage in ``follow_person``::

    profile["truth_social"] = "realDonaldTrump"
    # → fetches https://rsshub.app/truthsocial/realDonaldTrump

Public rsshub.app is best-effort: it deduplicates upstream rate limits
across all users.  For reliable production use, self-host.
"""

from __future__ import annotations

import os

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord
from .rss import RSSSource


DEFAULT_RSSHUB_BASE = "https://rsshub.app"


def _rsshub_base() -> str:
    return (os.environ.get("OMNI_RSSHUB_BASE") or DEFAULT_RSSHUB_BASE).rstrip("/")


class TruthSocialSource:
    """Fetch a Truth Social user's posts via RSSHub.

    The ``query`` argument is the Truth Social username (without @).
    """

    name = "truth_social"
    tier = 1                                                # depends on RSSHub uptime

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.timeout = timeout
        self._rss = RSSSource(timeout=timeout)

    def check(self) -> tuple[str, str]:
        base = _rsshub_base()
        if "rsshub.app" in base:
            return "warn", f"using public rsshub.app (shared rate limit); self-host for reliability"
        return "ok", f"RSSHub endpoint={base}/truthsocial/<user>"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        username = query.strip().lstrip("@")
        if not username:
            return []
        feed_url = f"{_rsshub_base()}/truthsocial/{username}"
        records = self._rss.retrieve(feed_url, limit=limit, domain=domain)
        # Rebrand source so downstream knows this came from Truth Social.
        for r in records:
            r.source = self.name
            r.metadata["username"] = username
            r.metadata["platform"] = "truth_social"
        return records


__all__ = ["TruthSocialSource"]
