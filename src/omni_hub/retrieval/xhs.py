"""Xiaohongshu (RED) retrieval — MediaCrawler SNAPSHOT primary, legacy CLI fallback.

XHS is the highest-risk source in the stack: 小红书 is actively litigating
scrapers and 2026-Q1 risk control is strict.  So omni-hub does NOT scrape XHS
itself and does NOT invoke a crawler synchronously in the cascade.  Instead the
operator runs the unified self-hosted broker **MediaCrawler**
(``agent-harness/forks/mediacrawler``) on a schedule with a BURNER account, low
volume; MediaCrawler writes note JSON to a snapshot dir.  This connector only
READS those snapshots and filters by query — pure stdlib, no signing, no
browser, no account inside omni-hub.

Snapshot dir: ``MEDIACRAWLER_DATA`` env (default
``agent-harness/forks/mediacrawler/data``).  When no snapshot dir is present,
this connector falls back to the legacy ``xhs`` CLI
(``jackwener/xiaohongshu-cli``) so existing setups keep working.

Hard rules (see manifest ``mediacrawler``): burner account only; low-volume
snapshot mode (NOT cascade hot path); never commercialize/redistribute.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord


def _snapshot_root() -> Path:
    env = os.environ.get("MEDIACRAWLER_DATA", "").strip()
    if env:
        return Path(env)
    # src/omni_hub/retrieval/xhs.py -> repo root = parents[3]
    return Path(__file__).resolve().parents[3] / "agent-harness/forks/mediacrawler/data"


def _count(v: object) -> float:
    """XHS counts arrive as ints OR strings like ``"1.2万"`` / ``"3.4k"``."""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip()
    if not s:
        return 0.0
    mult = 10000.0 if ("万" in s or s.lower().endswith("w")) else (
        1000.0 if "k" in s.lower() else 1.0
    )
    m = re.search(r"[\d.]+", s)
    return float(m.group()) * mult if m else 0.0


def _mc_note_to_record(note: dict) -> RetrievalRecord | None:
    """MediaCrawler XHS note dict -> RetrievalRecord (tolerant to field variants).
    Uses the same ``xhs:note:<id>`` canonical_id as the legacy CLI path so the
    two dedup together."""
    if not isinstance(note, dict):
        return None
    nid = str(note.get("note_id") or note.get("id") or "")
    title = str(note.get("title") or "").strip()
    desc = str(note.get("desc") or note.get("description") or "").strip()
    if not (title or desc):
        return None
    url = str(note.get("note_url") or note.get("url") or (
        f"https://www.xiaohongshu.com/explore/{nid}" if nid else ""
    ))
    u = note.get("user")
    author = str(
        note.get("nickname")
        or (u.get("nickname") if isinstance(u, dict) else "")
        or ""
    )
    likes = _count(note.get("liked_count"))
    return RetrievalRecord(
        source="xiaohongshu",
        title=title or desc[:60] or f"@{author}",
        url=url,
        snippet=(desc or title)[:500],
        score=likes,
        canonical_id=f"xhs:note:{nid}" if nid else "",
        metadata={
            "note_id": nid,
            "author": author,
            "likes": likes,
            "comments": _count(note.get("comment_count")),
            "collects": _count(note.get("collected_count")),
            "tags": note.get("tag_list") or note.get("tags") or [],
            "image_list": note.get("image_list") or [],
            "time": note.get("time") or note.get("last_update_time") or "",
            "lang": "zh",
            "snapshot": "mediacrawler",
        },
    )


class XiaohongshuSource:
    """XHS search via MediaCrawler snapshots (primary) or the ``xhs`` CLI
    (fallback).  Snapshot mode is the recommended path — see module docstring."""

    name = "xiaohongshu"
    tier = 2          # burner account + self-hosted broker / cookie ritual

    def __init__(
        self,
        *,
        binary: str = "xhs",
        data_dir: str | Path | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.binary = binary
        self.data_dir = Path(data_dir) if data_dir else _snapshot_root()
        self.timeout = timeout

    def _has_snapshots(self) -> bool:
        return self.data_dir.exists() and any(self.data_dir.rglob("*.json"))

    def check(self) -> tuple[str, str]:
        # Primary: MediaCrawler snapshot dir (the recommended broker).
        if self.data_dir.exists():
            n = sum(1 for _ in self.data_dir.rglob("*.json"))
            if n:
                return "ok", f"MediaCrawler snapshots: {n} file(s) at {self.data_dir}"
        # Fallback: legacy xhs CLI.
        if shutil.which(self.binary) is None:
            return "off", (
                f"no MediaCrawler snapshot dir ({self.data_dir}); "
                f"`{self.binary}` not on PATH. Run MediaCrawler (burner account) "
                "-> set MEDIACRAWLER_DATA, or pipx install xiaohongshu-cli."
            )
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
            return "ok", "xhs CLI logged in (legacy fallback)"
        return "off", "xhs CLI present but not logged in (`xhs login`)"

    def _from_snapshots(self, query: str, limit: int) -> list[RetrievalRecord]:
        q = (query or "").strip().lower()
        records: list[RetrievalRecord] = []
        seen: set[str] = set()
        for jf in sorted(self.data_dir.rglob("*.json")):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            notes = data if isinstance(data, list) else (
                data.get("data") or data.get("results") or data.get("notes") or []
            )
            if not isinstance(notes, list):
                continue
            for note in notes:
                rec = _mc_note_to_record(note)
                if rec is None:
                    continue
                hay = (
                    rec.title + " " + rec.snippet + " "
                    + " ".join(str(t) for t in (rec.metadata or {}).get("tags", []))
                ).lower()
                if q and q not in hay:
                    continue
                key = rec.canonical_id or rec.url
                if key in seen:
                    continue
                seen.add(key)
                records.append(rec)
                if len(records) >= limit:
                    return records
        return records

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []

        # Primary: read MediaCrawler snapshots (no network/browser/account here).
        if self._has_snapshots():
            return self._from_snapshots(query, limit)

        # Fallback: legacy `xhs` CLI (jackwener/xiaohongshu-cli).
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
