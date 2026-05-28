"""Generic RSS / Atom feed reader.

Designed for high-signal individual / company blogs that don't expose a
search API but do publish a feed:

* karpathy.github.io/feed.xml
* lilianweng.github.io/feed.xml
* blog.samaltman.com/posts.atom
* anthropic.com/news/rss.xml
* openai.com/blog/rss.xml
* simonwillison.net/atom/everything/

Unlike the other connectors, RSS is *URL-scoped* — you ask "give me the
latest items from this feed" rather than "search for this query".  The
``retrieve`` method takes the feed URL as ``query``; a query-substring
filter is then applied to titles+descriptions when ``query`` contains a
``"|term"`` suffix (e.g. ``https://x.com/feed|llama``).

Hard constraint: stdlib only — uses ``xml.etree.ElementTree`` for parsing.
No ``feedparser`` dependency.
"""

from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord


# Common XML namespaces in Atom/RSS feeds.
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _url_hash(url: str) -> str:
    return hashlib.sha1(url.split("#", 1)[0].encode("utf-8")).hexdigest()[:16]


class RSSSource:
    """Generic RSS / Atom reader.

    Pass the feed URL as the ``query`` argument; the connector fetches
    and parses, returning the latest N entries.  Optional substring
    filter via ``<url>|<term>`` suffix.
    """

    name = "rss"
    tier = 0          # no auth required

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return "ok", "generic RSS/Atom parser (stdlib XML)"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 10,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        # Optional substring filter: "<url>|<term>"
        url, _, term = query.partition("|")
        url = url.strip()
        term = term.strip().lower()
        if not url.startswith(("http://", "https://")):
            raise RetrievalError(f"RSSSource requires a feed URL, got: {url!r}")

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "omni-hub/0.42 RSS reader",
                "Accept": "application/rss+xml, application/atom+xml, application/xml",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                xml_bytes = resp.read()
        except urllib.error.HTTPError as exc:
            raise RetrievalError(f"rss {url} HTTP {exc.code}: {exc.reason}") from exc
        except Exception as exc:                                  # noqa: BLE001
            raise RetrievalError(f"rss {url} {type(exc).__name__}: {exc}") from exc

        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            raise RetrievalError(f"rss {url} parse error: {exc}") from exc

        records = self._parse_atom(root) if root.tag.endswith("}feed") else self._parse_rss(root)

        if term:
            records = [
                r for r in records
                if term in r.title.lower() or term in r.snippet.lower()
            ]
        return records[:limit]

    # ---- format parsers -----------------------------------------

    def _parse_atom(self, root: ET.Element) -> list[RetrievalRecord]:
        out: list[RetrievalRecord] = []
        for entry in root.findall("atom:entry", _NS):
            title = (entry.findtext("atom:title", default="", namespaces=_NS) or "").strip()
            summary = (
                entry.findtext("atom:summary", default="", namespaces=_NS)
                or entry.findtext("atom:content", default="", namespaces=_NS)
                or ""
            )
            published = (
                entry.findtext("atom:published", default="", namespaces=_NS)
                or entry.findtext("atom:updated", default="", namespaces=_NS)
                or ""
            )
            link_el = entry.find("atom:link[@rel='alternate']", _NS) or entry.find("atom:link", _NS)
            href = link_el.attrib.get("href", "") if link_el is not None else ""
            out.append(self._make_record(title, summary, href, published))
        return out

    def _parse_rss(self, root: ET.Element) -> list[RetrievalRecord]:
        out: list[RetrievalRecord] = []
        channel = root.find("channel")
        if channel is None:
            return out
        for item in channel.findall("item"):
            title = (item.findtext("title", default="") or "").strip()
            desc = (
                item.findtext("description", default="")
                or item.findtext("content:encoded", default="", namespaces=_NS)
                or ""
            )
            link = (item.findtext("link", default="") or "").strip()
            pub = (
                item.findtext("pubDate", default="")
                or item.findtext("dc:date", default="", namespaces=_NS)
                or ""
            )
            out.append(self._make_record(title, desc, link, pub))
        return out

    def _make_record(self, title: str, body: str, url: str, pub_date: str) -> RetrievalRecord:
        clean = _strip_html(body)[:600]
        return RetrievalRecord(
            source=self.name,
            title=title,
            url=url,
            snippet=clean,
            score=0.0,
            canonical_id=f"rss:{_url_hash(url)}" if url else "",
            metadata={
                "published": pub_date,
                "feed_origin": url.rsplit("/", 1)[0] if url else "",
            },
        )


__all__ = ["RSSSource"]
