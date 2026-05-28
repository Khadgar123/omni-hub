"""中国政策 RSS connectors (v0.21).

Strategy: Chinese government sites have been deprecating their built-in
RSS endpoints since ~2024, so the realistic SOTA is a **community RSS
aggregator** — RSSHub being the most-deployed.  Each connector here
points at an RSSHub-style endpoint (configurable via ``OMNI_RSSHUB_BASE``
env var, defaults to ``https://rsshub.app`` for one-off probes, but a
self-hosted instance is strongly recommended for sustained use).

Personal-use only; respect upstream robots.txt + rate limits.  When the
user has no broker the cascade fail-soft-skips and the doctor command
explains the gap.

Sources covered:

| Slug                | Endpoint                                  | Upstream                     |
|---------------------|--------------------------------------------|-------------------------------|
| ``gov_cn``          | ``/gov/zhengce/zuixin``                    | 国务院 政策最新                |
| ``stats_gov_cn``    | ``/stats/news/topnews``                    | 国家统计局 重要新闻            |
| ``court_gov_cn``    | ``/court/index``                           | 最高人民法院 公告              |
| ``pbc_gov_cn``      | ``/pbc/goutongjiaoliu``                    | 央行 沟通交流                  |

Queries filter the latest N RSS items by case-insensitive substring
match on title + description.  Real "search" requires a dedicated
search backend; v0.21 keeps the connector small and lets users wire
heavier search via ``brave_search`` for the same domain.
"""

from __future__ import annotations

import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .base import (
    DEFAULT_TIMEOUT_SEC,
    RetrievalError,
    RetrievalRecord,
    http_get_text,
)


DEFAULT_RSSHUB_BASE = "https://rsshub.app"


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(text: str) -> str:
    return _HTML_TAG_RE.sub("", text or "").strip()


def _resolve_base() -> str:
    return (os.environ.get("OMNI_RSSHUB_BASE", DEFAULT_RSSHUB_BASE) or "").rstrip("/")


@dataclass(slots=True)
class _RSSHubConfig:
    """Per-ministry RSSHub endpoint description."""

    path: str            # e.g. "/gov/zhengce/zuixin"
    upstream: str        # e.g. "国务院 政策最新"
    domain_hint: str     # "policy"


class _CNPolicyRSSSourceBase:
    """Shared base for ministry-specific RSS sources.

    Subclasses set ``name`` (the cascade key) and ``config``.
    """

    name: str = "stub"
    tier: int = 1               # free key/broker (RSSHub self-host)
    config: _RSSHubConfig | None = None

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
        max_items: int = 30,
    ) -> None:
        # Distinguish "no explicit override" (use env / default) from
        # "explicit empty string" (force-off for tests / dry-run).
        if base_url is None:
            self.base_url = _resolve_base().rstrip("/")
        else:
            self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_items = max_items

    # ---- health probe -----------------------------------------------

    def check(self) -> tuple[str, str]:
        if not self.base_url:
            return "off", "OMNI_RSSHUB_BASE not set; pin RSSHub locally"
        if self.config is None:                          # pragma: no cover
            return "off", "no RSSHub config"
        return "ok", f"endpoint={self.base_url}{self.config.path}"

    # ---- retrieve ---------------------------------------------------

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip() or not self.base_url or self.config is None:
            return []
        endpoint = f"{self.base_url}{self.config.path}"
        try:
            body, _ = http_get_text(
                endpoint,
                timeout=self.timeout,
                accept="application/rss+xml, application/atom+xml, application/xml, */*",
            )
        except RetrievalError:
            return []
        items = _parse_rss_or_atom(body)
        if not items:
            return []

        needle = query.lower()
        records: list[RetrievalRecord] = []
        for item in items[: self.max_items]:
            title = item.title
            description = item.description
            haystack = f"{title}\n{description}".lower()
            if needle and needle not in haystack:
                continue
            records.append(RetrievalRecord(
                source=self.name,
                title=title,
                url=item.link,
                snippet=description[:500],
                score=0.0,
                canonical_id=f"{self.name}:{item.guid}" if item.guid else "",
                metadata={
                    "upstream": self.config.upstream,
                    "broker": self.base_url,
                    "pub_date": item.pub_date,
                    "lang": "zh",
                },
            ))
            if len(records) >= limit:
                break
        return records


@dataclass(slots=True)
class _RSSItem:
    title: str
    link: str
    description: str
    pub_date: str
    guid: str


def _parse_rss_or_atom(body: str) -> list[_RSSItem]:
    """Parse RSS 2.0 or Atom.  Returns [] on any parse error."""

    body = body.strip()
    if not body:
        return []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []

    items: list[_RSSItem] = []

    # RSS 2.0
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = _strip_tags(item.findtext("description") or "")
        pub_date = (item.findtext("pubDate") or "").strip()
        guid = (item.findtext("guid") or link).strip()
        items.append(_RSSItem(title, link, description, pub_date, guid))

    # Atom
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("atom:entry", ns):
        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
        link_el = entry.find("atom:link", ns)
        href = link_el.get("href", "") if link_el is not None else ""
        summary = _strip_tags(entry.findtext("atom:summary", default="", namespaces=ns) or "")
        content = _strip_tags(entry.findtext("atom:content", default="", namespaces=ns) or "")
        description = summary or content
        pub_date = (entry.findtext("atom:updated", default="", namespaces=ns) or
                    entry.findtext("atom:published", default="", namespaces=ns) or "")
        atom_id = (entry.findtext("atom:id", default="", namespaces=ns) or href)
        items.append(_RSSItem(title, href, description, pub_date, atom_id))

    return items


# ---------------------------------------------------------------------------
# Concrete ministry sources
# ---------------------------------------------------------------------------


class GovCnSource(_CNPolicyRSSSourceBase):
    name = "gov_cn"
    config = _RSSHubConfig(
        path="/gov/zhengce/zuixin",
        upstream="国务院 政策最新",
        domain_hint="cn_policy",
    )


class StatsGovCnSource(_CNPolicyRSSSourceBase):
    name = "stats_gov_cn"
    config = _RSSHubConfig(
        path="/stats/news/topnews",
        upstream="国家统计局 重要新闻",
        domain_hint="cn_policy",
    )


class CourtGovCnSource(_CNPolicyRSSSourceBase):
    name = "court_gov_cn"
    config = _RSSHubConfig(
        path="/court/index",
        upstream="最高人民法院 公告",
        domain_hint="cn_policy",
    )


class PBCGovCnSource(_CNPolicyRSSSourceBase):
    name = "pbc_gov_cn"
    config = _RSSHubConfig(
        path="/pbc/goutongjiaoliu",
        upstream="央行 沟通交流",
        domain_hint="cn_policy",
    )


__all__ = [
    "CourtGovCnSource",
    "GovCnSource",
    "PBCGovCnSource",
    "StatsGovCnSource",
]
