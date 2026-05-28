"""Xiaohongshu (RED) retrieval via the ``xhs`` CLI subprocess.

Hard constraint: this module MUST NOT contain any XHS x-s / x-t signing
logic.  All signing lives upstream in ``Cloxl/xhshow`` (pinned via
``agent-harness/forks/xhshow``) and reaches us through
``jackwener/xiaohongshu-cli`` (pinned, ``pipx``-installed as the
``xhs`` binary).  When XHS rotates signing, we update xhshow — not this
file.

Auth: ``xhs login`` paste-cookie ritual writes ``~/.xhs-cli/cookies.json``.
We do not store cookies in env or pass them through omni-hub.

Bridge contract: ``XiaohongshuSource.retrieve(query)`` shells out to
``xhs search <query> --json``, parses the JSON, returns
:class:`RetrievalRecord` instances.  Missing binary returns ``[]`` and
records a health-check warning rather than crashing the cascade.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord


class XiaohongshuSource:
    """XHS search.  Requires the ``xhs`` CLI on PATH (see
    ``agent-harness/forks/xiaohongshu-cli`` PIN).
    """

    name = "xiaohongshu"
    tier = 2          # paste-cookie ritual + pinned fork install required

    def __init__(
        self,
        *,
        binary: str = "xhs",
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.binary = binary
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if shutil.which(self.binary) is None:
            return "off", (
                f"`{self.binary}` not on PATH. "
                "PIN agent-harness/forks/xiaohongshu-cli + "
                "pipx install ./forks/xiaohongshu-cli"
            )
        # Best effort: probe status without spending a search.
        try:
            result = subprocess.run(
                [self.binary, "status", "--json"],
                capture_output=True, text=True,
                timeout=min(self.timeout, 5),
            )
        except subprocess.TimeoutExpired:
            return "warn", "`xhs status` timed out"
        except Exception as exc:                            # noqa: BLE001
            return "error", f"{type(exc).__name__}: {exc}"
        if result.returncode != 0:
            return "warn", f"`xhs status` rc={result.returncode}"
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return "warn", "non-JSON status output"
        if payload.get("logged_in"):
            return "ok", "xhs logged in"
        return "off", "xhs CLI present but not logged in (`xhs login`)"

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
            return []          # health probe already reports the gap

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

        records: list[RetrievalRecord] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            note_id = str(item.get("note_id", "") or item.get("id", ""))
            title = str(item.get("title", "") or item.get("desc", "")[:60])
            desc = str(item.get("desc", ""))
            author = (item.get("user") or {}).get("nickname", "")
            url = str(item.get("url", ""))
            likes = int(item.get("liked_count", 0) or 0)
            records.append(RetrievalRecord(
                source=self.name,
                title=title or f"@{author}",
                url=url,
                snippet=desc[:500],
                score=float(likes),
                canonical_id=f"xhs:note:{note_id}" if note_id else "",
                metadata={
                    "note_id": note_id,
                    "author": author,
                    "likes": likes,
                    "comments": item.get("comment_count", 0),
                    "collects": item.get("collected_count", 0),
                    "lang": "zh",
                },
            ))
        return records
