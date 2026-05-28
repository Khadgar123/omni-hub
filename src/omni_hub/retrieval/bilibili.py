"""Bilibili open search (v0.20).

Uses Bilibili's anonymous web search endpoint
``api.bilibili.com/x/web-interface/wbi/search/all/v2`` — no auth required,
but Bilibili added WBI (web-bridge interface) request signing in 2023 so
the endpoint occasionally returns ``-412 risk control``.  For omni-hub's
personal-use cadence this works fine; if the user hits sustained rate
limit, the proper fix is to pin ``bilibili-api-python`` under
``agent-harness/forks/bilibili-api/`` and call it via subprocess — same
pattern as ``xhs``.

Output covers all six Bilibili result types (video / bili_user / article /
live / live_room / mediaplus); v0.20 keeps the connector simple and
folds video + article (the two types that actually carry useful text)
into RetrievalRecord.
"""

from __future__ import annotations

import re
import time
from typing import Any

from .base import (
    DEFAULT_TIMEOUT_SEC,
    RetrievalError,
    RetrievalRecord,
    http_get_json,
)


_SEARCH_URL = "https://api.bilibili.com/x/web-interface/wbi/search/all/v2"
_FALLBACK_URL = "https://api.bilibili.com/x/web-interface/search/all/v2"


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_em(text: str) -> str:
    """Bilibili wraps matched terms in ``<em class="keyword">…</em>``."""

    return _HTML_TAG_RE.sub("", text or "")


class BilibiliSource:
    """Bilibili open search.  Tier-0 (no key) with fallback to non-WBI
    endpoint if signing fails."""

    name = "bilibili"
    tier = 0

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        # Cheap probe — search for "1" and check the response shape.
        try:
            payload = http_get_json(
                _FALLBACK_URL,
                params={"keyword": "1", "page": 1},
                timeout=min(self.timeout, 5),
                headers=self._headers(),
            )
        except RetrievalError as exc:
            return "warn", f"bilibili probe failed: {exc}"
        if not isinstance(payload, dict):
            return "warn", "non-dict response"
        code = payload.get("code")
        if code == 0:
            return "ok", "bilibili reachable"
        if code == -412:
            return "warn", "bilibili risk-control (try again later)"
        return "warn", f"bilibili code={code}"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 10,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []

        # Try the WBI endpoint first; on signing errors fall back to the
        # non-WBI v2 endpoint that still works for anonymous reads.
        payload: dict[str, Any] | None = None
        for url in (_WBI_URL_OR_FALLBACK :=  [_FALLBACK_URL]):
            # v0.20 ships only the no-signing fallback. WBI signing is best
            # done via the pinned upstream lib; see module docstring.
            try:
                payload = http_get_json(
                    url,
                    params={"keyword": query, "page": 1},
                    timeout=self.timeout,
                    headers=self._headers(),
                )
            except RetrievalError:
                continue
            if isinstance(payload, dict) and payload.get("code") == 0:
                break
            payload = None

        if not payload:
            return []

        data = payload.get("data") or {}
        result_groups = data.get("result") or []
        if not isinstance(result_groups, list):
            return []

        records: list[RetrievalRecord] = []
        for group in result_groups:
            if not isinstance(group, dict):
                continue
            kind = str(group.get("result_type", ""))
            if kind not in {"video", "article"}:
                continue
            items = group.get("data") or []
            if not isinstance(items, list):
                continue
            for item in items[:limit]:
                if not isinstance(item, dict):
                    continue
                rec = self._build_record(kind, item)
                if rec is not None:
                    records.append(rec)
                    if len(records) >= limit:
                        return records
        return records

    def _build_record(self, kind: str, item: dict[str, Any]) -> RetrievalRecord | None:
        if kind == "video":
            bvid = str(item.get("bvid", ""))
            title = _strip_em(item.get("title") or "")
            desc = _strip_em(item.get("description") or "")
            author = str(item.get("author", ""))
            play = int(item.get("play") or 0)
            url = f"https://www.bilibili.com/video/{bvid}" if bvid else str(item.get("arcurl", ""))
            return RetrievalRecord(
                source=self.name,
                title=title,
                url=url,
                snippet=desc[:500],
                score=float(play),
                canonical_id=f"bili:bvid:{bvid}" if bvid else "",
                metadata={
                    "bvid": bvid,
                    "kind": kind,
                    "author": author,
                    "play": play,
                    "danmaku": item.get("danmaku"),
                    "duration": item.get("duration"),
                    "pubdate": item.get("pubdate"),
                    "lang": "zh",
                },
            )
        if kind == "article":
            aid = str(item.get("id", ""))
            title = _strip_em(item.get("title") or "")
            desc = _strip_em(item.get("desc") or "")
            author_obj = item.get("author") or {}
            author = author_obj.get("name") if isinstance(author_obj, dict) else ""
            views = int(item.get("view") or 0)
            url = f"https://www.bilibili.com/read/cv{aid}" if aid else ""
            return RetrievalRecord(
                source=self.name,
                title=title,
                url=url,
                snippet=desc[:500],
                score=float(views),
                canonical_id=f"bili:article:{aid}" if aid else "",
                metadata={
                    "article_id": aid,
                    "kind": kind,
                    "author": author,
                    "views": views,
                    "lang": "zh",
                },
            )
        return None

    def _headers(self) -> dict[str, str]:
        # Bilibili's web endpoints expect a Referer + a session-like cookie.
        # Anonymous requests work but a Referer header keeps the API
        # happier and avoids the 412 risk-control more often.
        return {
            "Referer": "https://search.bilibili.com/",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }


__all__ = ["BilibiliSource"]
