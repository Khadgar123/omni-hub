"""WeChat 公众号 retrieval via a self-hosted ``we-mp-rss`` RSS endpoint.

Background: ``cooderl/wewe-rss`` was archived 2026-05-11.  The only 2026
maintained broker is ``rachelos/we-mp-rss`` (Docker self-host).  Pinned
under ``agent-harness/forks/we-mp-rss``.

The user runs the broker locally (e.g. http://localhost:4321), exposing
RSS feeds per subscribed 公众号.  This connector reads those feeds.
We do NOT scrape WeChat directly — the broker handles the QR login and
session refresh; we only consume RSS.

Configuration: ``WEMPRSS_BASE`` env var (e.g. ``http://localhost:4321``).
Falls back to localhost:4321 if unset.  ``WEMPRSS_TOKEN`` optional bearer
when the broker is exposed externally.
"""

from __future__ import annotations

import os
import urllib.parse
import xml.etree.ElementTree as ET

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord, http_get_text
from .health import env_var_probe


DEFAULT_BASE = "http://localhost:4321"


class WeChatMPSource:
    """Read 公众号 articles from a self-hosted we-mp-rss broker."""

    name = "wechat_mp"
    tier = 2          # broker self-host + login ritual

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.base_url = (base_url or os.environ.get("WEMPRSS_BASE", DEFAULT_BASE)).rstrip("/")
        self.token = token or os.environ.get("WEMPRSS_TOKEN", "")
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if not self.base_url:
            return "off", "WEMPRSS_BASE not set"
        # Don't actually GET on every doctor run; just confirm config.
        return "ok", f"broker={self.base_url}"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        # we-mp-rss exposes /feed.atom?search=<query> (newer versions) or
        # /api/articles?keyword=<q> (older).  Try the search query param;
        # if zero results, the user is responsible for picking a feed.
        url = f"{self.base_url}/feed.atom"
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        body, _resp_headers = http_get_text(
            url + "?" + urllib.parse.urlencode({"search": query, "limit": str(limit)}),
            headers=headers,
            timeout=self.timeout,
            accept="application/atom+xml, application/rss+xml, */*",
        )
        if not body.strip():
            return []

        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return []

        records: list[RetrievalRecord] = []
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns) or root.findall(".//entry")
        for entry in entries[:limit]:
            title_el = entry.find("atom:title", ns) or entry.find("title")
            link_el = entry.find("atom:link", ns) or entry.find("link")
            summary_el = entry.find("atom:summary", ns) or entry.find("summary")
            id_el = entry.find("atom:id", ns) or entry.find("id")
            title = (title_el.text or "").strip() if title_el is not None else ""
            href = link_el.get("href") if link_el is not None and link_el.get("href") else (
                (link_el.text or "").strip() if link_el is not None else ""
            )
            summary = (summary_el.text or "").strip() if summary_el is not None else ""
            atom_id = (id_el.text or "").strip() if id_el is not None else href
            records.append(RetrievalRecord(
                source=self.name,
                title=title,
                url=href,
                snippet=summary[:500],
                score=0.0,
                canonical_id=f"wechat_mp:{atom_id}" if atom_id else "",
                metadata={"lang": "zh", "broker": self.base_url},
            ))
        return records
