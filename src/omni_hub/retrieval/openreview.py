"""OpenReview — peer-review threads for ICLR / NeurIPS / etc. (api2).

The single most differentiated paper asset omni-hub can add: official
reviews, ratings, rebuttals, and the accept/reject decision — none of
which OpenAlex / Semantic Scholar / Crossref carry.  Public REST API at
``api2.openreview.net`` (no key for public venues).

Two interfaces:

* ``retrieve(query)`` — full-text note search, for the cascade (surface
  submissions matching a topic).
* ``forum_thread(forum_id)`` — the STRUCTURED review thread for ONE paper
  (reviews + ratings + decision + a derived acceptance flag).  This is the
  on-demand path reachable from the ``openreview_forum_id`` that
  ResearchFlow already stores — and it yields acceptance status for free.
"""

from __future__ import annotations

import re

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord, http_get_json


API2_BASE = "https://api2.openreview.net"
NOTES_SEARCH = f"{API2_BASE}/notes/search"
NOTES_FORUM = f"{API2_BASE}/notes"


def _v(content: dict, field: str, default: object = "") -> object:
    """Read an OpenReview note content field across API v1/v2.

    API v2 wraps every value as ``{"value": X}``; v1 stores ``X`` directly.
    """

    raw = (content or {}).get(field)
    if isinstance(raw, dict) and "value" in raw:
        return raw["value"]
    return raw if raw is not None else default


def _rating_number(value: object) -> float | None:
    """Parse a leading number from an OpenReview rating, which may be ``8``,
    ``"8: accept, good paper"``, or ``"8"``."""

    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.match(r"\s*(\d+(?:\.\d+)?)", str(value))
    return float(m.group(1)) if m else None


class OpenReviewSource:
    name = "openreview"
    tier = 0          # public venues need no key

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return "ok", "public venues (api2.openreview.net, no key)"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        try:
            data = http_get_json(
                NOTES_SEARCH,
                params={
                    "term": query,
                    "limit": str(min(limit, 25)),
                    "content": "all",
                    "group": "all",
                    "source": "all",
                },
                timeout=self.timeout,
            )
        except RetrievalError:
            return []                                  # fail-soft: cascade continues
        notes = (data.get("notes") or []) if isinstance(data, dict) else []
        records: list[RetrievalRecord] = []
        for note in notes[:limit]:
            if not isinstance(note, dict):
                continue
            content = note.get("content", {})
            title = str(_v(content, "title") or "")
            if not title:
                continue
            forum = str(note.get("forum") or note.get("id") or "")
            abstract = str(_v(content, "abstract") or "")
            venue = str(_v(content, "venue") or "")
            records.append(RetrievalRecord(
                source=self.name,
                title=title,
                url=f"https://openreview.net/forum?id={forum}" if forum else "",
                snippet=(abstract or venue)[:500],
                canonical_id=f"openreview:{forum}" if forum else "",
                metadata={
                    "forum_id": forum,
                    "venue": venue,
                    "authors": _v(content, "authors") or [],
                },
            ))
        return records

    def venue_submissions(
        self, venueid: str, *, limit: int = 100,
    ) -> list[RetrievalRecord]:
        """The full ACCEPTED-paper list for a venue, e.g.
        ``"ICLR.cc/2026/Conference"``.

        OpenReview assigns ``content.venueid`` only to accepted / camera-ready
        submissions, so filtering on it yields the official accepted list —
        the post-conference full dump that the cascade's ``retrieve()``
        term-search cannot enumerate.  Each record carries ``accepted: True``
        + ``venueid`` (and a ``doi`` when present) so the identity-resolution
        engine (``paper_identity.merge_papers``) can fold it into an existing
        arXiv-preprint record instead of duplicating it.
        """
        vid = str(venueid).strip()
        if not vid:
            return []
        try:
            data = http_get_json(
                NOTES_FORUM,
                params={"content.venueid": vid, "limit": str(min(max(limit, 1), 1000))},
                timeout=self.timeout,
            )
        except RetrievalError:
            return []
        notes = (data.get("notes") or []) if isinstance(data, dict) else []
        records: list[RetrievalRecord] = []
        for note in notes[:limit]:
            if not isinstance(note, dict):
                continue
            content = note.get("content", {})
            title = str(_v(content, "title") or "")
            if not title:
                continue
            forum = str(note.get("forum") or note.get("id") or "")
            abstract = str(_v(content, "abstract") or "")
            records.append(RetrievalRecord(
                source=self.name,
                title=title,
                url=f"https://openreview.net/forum?id={forum}" if forum else "",
                snippet=abstract[:500],
                canonical_id=f"openreview:{forum}" if forum else "",
                metadata={
                    "forum_id": forum,
                    "venue": str(_v(content, "venue") or vid),
                    "venueid": vid,
                    "accepted": True,
                    "doi": str(_v(content, "doi") or ""),
                    "authors": _v(content, "authors") or [],
                },
            ))
        return records

    def forum_thread(self, forum_id: str) -> dict | None:
        """Structured review thread for ONE paper: reviews, ratings, the
        decision, and a derived ``accepted`` flag.  Best-effort → ``None``.

        Accepts a bare forum id or a full ``...forum?id=<id>`` URL.
        """

        fid = str(forum_id).strip()
        m = re.search(r"[?&]id=([^&]+)", fid)
        if m:
            fid = m.group(1)
        if not fid:
            return None
        try:
            data = http_get_json(
                NOTES_FORUM, params={"forum": fid}, timeout=self.timeout,
            )
        except RetrievalError:
            return None
        notes = (data.get("notes") or []) if isinstance(data, dict) else []
        title = ""
        reviews: list[dict[str, object]] = []
        decision_text = ""
        for note in notes:
            if not isinstance(note, dict):
                continue
            content = note.get("content", {})
            invitations = note.get("invitations") or (
                [note.get("invitation")] if note.get("invitation") else []
            )
            inv = " ".join(str(i) for i in invitations)
            note_id = str(note.get("id") or "")
            if note_id == fid or "/Submission" in inv:
                title = title or str(_v(content, "title") or "")
            if "Official_Review" in inv or inv.endswith("/Review"):
                reviews.append({
                    "rating": _rating_number(_v(content, "rating")),
                    "confidence": _rating_number(_v(content, "confidence")),
                    "summary": str(
                        _v(content, "summary") or _v(content, "review") or ""
                    )[:1000],
                })
            if "Decision" in inv or "Meta_Review" in inv:
                decision_text = (
                    str(_v(content, "decision") or _v(content, "recommendation") or "")
                    or decision_text
                )
        ratings = [r["rating"] for r in reviews if r["rating"] is not None]
        avg = round(sum(ratings) / len(ratings), 2) if ratings else None
        accepted = ("accept" in decision_text.lower()) if decision_text else None
        return {
            "forum_id": fid,
            "title": title,
            "url": f"https://openreview.net/forum?id={fid}",
            "n_reviews": len(reviews),
            "ratings": ratings,
            "avg_rating": avg,
            "reviews": reviews,
            "decision": decision_text,
            "accepted": accepted,
        }
