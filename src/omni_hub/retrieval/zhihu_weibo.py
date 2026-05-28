"""Zhihu + Weibo broker-pattern sources (v0.20 stubs).

Both Zhihu and Weibo require login-walled APIs.  Mirroring the XHS /
WeChat MP pattern, omni-hub keeps a stub here that the cascade can
import unconditionally; the actual subprocess broker lives under
``agent-harness/integrations/<platform>/`` and is invoked via a CLI
binary on PATH.  When the broker isn't installed, ``retrieve()`` returns
``[]`` and ``check()`` reports the gap.

Upstream broker pins (planned):

* Zhihu:  ``agent-harness/integrations/zhihu/`` — wraps the ``zhihu-py3``
  / ``zhihu-oauth`` family, exposes a ``zhihu search <q> --json`` CLI.
* Weibo:  ``agent-harness/integrations/weibo/`` — wraps the legacy
  weibo open API (or a CSS-based scraper for personal use only),
  exposes a ``weibo search <q> --json`` CLI.

Personal-use only; no scraping at scale, no commercial republishing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord


class _BrokerStubSource:
    """Shared broker stub.  Subclasses set ``name`` + ``binary``."""

    name: str = "stub"
    tier: int = 2
    binary: str = ""
    harness_path: str = ""

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if not self.binary:                                  # pragma: no cover
            return "off", "no broker binary configured"
        if shutil.which(self.binary) is None:
            return "off", (
                f"`{self.binary}` not on PATH. "
                f"Pin {self.harness_path} + install its CLI shim."
            )
        try:
            result = subprocess.run(
                [self.binary, "status", "--json"],
                capture_output=True, text=True,
                timeout=min(self.timeout, 5),
            )
        except subprocess.TimeoutExpired:
            return "warn", f"`{self.binary} status` timed out"
        except Exception as exc:                             # noqa: BLE001
            return "error", f"{type(exc).__name__}: {exc}"
        if result.returncode != 0:
            return "warn", f"`{self.binary} status` rc={result.returncode}"
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return "warn", "non-JSON status output"
        if payload.get("logged_in"):
            return "ok", f"{self.binary} logged in"
        return "off", f"{self.binary} present but not logged in"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        if shutil.which(self.binary) is None:
            return []
        try:
            result = subprocess.run(
                [self.binary, "search", query, "--limit", str(limit), "--json"],
                capture_output=True, text=True, timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return []
        except Exception:                                    # noqa: BLE001
            return []
        if result.returncode != 0:
            return []
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return []
        items = payload.get("results", []) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        return [r for r in (self._build(item) for item in items[:limit]) if r is not None]

    def _build(self, item: Any) -> RetrievalRecord | None:                  # pragma: no cover
        raise NotImplementedError


class ZhihuSource(_BrokerStubSource):
    """Zhihu broker stub (v0.20).

    Expected broker JSON shape::

        [{
            "answer_id": "...",
            "question": "...",
            "question_id": "...",
            "author": "...",
            "url": "...",
            "excerpt": "...",
            "voteup_count": 0
        }, ...]
    """

    name = "zhihu"
    tier = 2
    binary = "zhihu"
    harness_path = "agent-harness/integrations/zhihu/"

    def _build(self, item: Any) -> RetrievalRecord | None:
        if not isinstance(item, dict):
            return None
        answer_id = str(item.get("answer_id", "") or item.get("id", ""))
        question = str(item.get("question") or "")
        excerpt = str(item.get("excerpt") or "")
        author = str(item.get("author", ""))
        votes = int(item.get("voteup_count", 0) or 0)
        url = str(item.get("url", ""))
        return RetrievalRecord(
            source=self.name,
            title=question or f"@{author}",
            url=url,
            snippet=excerpt[:500],
            score=float(votes),
            canonical_id=f"zhihu:answer:{answer_id}" if answer_id else "",
            metadata={
                "answer_id": answer_id,
                "question_id": item.get("question_id"),
                "author": author,
                "voteup_count": votes,
                "lang": "zh",
            },
        )


class WeiboSource(_BrokerStubSource):
    """Weibo broker stub (v0.20).

    Expected broker JSON shape::

        [{
            "mid": "...",
            "user": {"screen_name": "..."},
            "text": "...",
            "url": "...",
            "reposts_count": 0,
            "attitudes_count": 0
        }, ...]
    """

    name = "weibo"
    tier = 2
    binary = "weibo"
    harness_path = "agent-harness/integrations/weibo/"

    def _build(self, item: Any) -> RetrievalRecord | None:
        if not isinstance(item, dict):
            return None
        mid = str(item.get("mid", "") or item.get("id", ""))
        text = str(item.get("text") or item.get("text_raw") or "")
        user = item.get("user") or {}
        screen_name = user.get("screen_name", "") if isinstance(user, dict) else ""
        reposts = int(item.get("reposts_count", 0) or 0)
        likes = int(item.get("attitudes_count", 0) or 0)
        url = str(item.get("url", ""))
        return RetrievalRecord(
            source=self.name,
            title=(text[:80] or f"@{screen_name}"),
            url=url,
            snippet=text[:500],
            score=float(likes + reposts * 2),
            canonical_id=f"weibo:mid:{mid}" if mid else "",
            metadata={
                "mid": mid,
                "user": screen_name,
                "reposts_count": reposts,
                "attitudes_count": likes,
                "lang": "zh",
            },
        )


__all__ = ["WeiboSource", "ZhihuSource"]
