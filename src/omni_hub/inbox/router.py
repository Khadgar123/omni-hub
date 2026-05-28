"""ForwardedContentRouter — classify + dispatch (v0.33)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from ..channels.base import InboundMessage


# ---------------------------------------------------------------------------
# Detection patterns (stdlib regex, no external libs)
# ---------------------------------------------------------------------------


_URL_PATTERN = re.compile(
    r"(?P<url>https?://[^\s<>'\")]+|file://[^\s<>'\")]+)"
)


_PDF_PATTERN = re.compile(r"\.pdf(\?|#|$)", re.IGNORECASE)


_ICAL_HEADER_PATTERN = re.compile(r"^BEGIN:VCALENDAR\b", re.MULTILINE)


# Task-language: verb + (optional) datetime token.  Lightweight; v0.40+
# swap to LLM classifier.
_TASK_VERB_PATTERNS = (
    re.compile(r"\b(remind me|todo|task)\b", re.IGNORECASE),
    re.compile(r"(?:记得|帮我|周末|明天|今晚|今天)\s*(?:[^\n]{1,80})"),
    re.compile(r"(?:before|by)\s+(?:tomorrow|next|monday|tuesday|wednesday|"
               r"thursday|friday|saturday|sunday)", re.IGNORECASE),
)


_DATE_HINT_PATTERN = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}|"
    r"tomorrow|today|tonight|next\s+(?:week|month)|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)


class InboxCategory(str, Enum):
    URL = "url"
    PDF = "pdf"
    CALENDAR_INVITE = "calendar_invite"
    TASK = "task"
    WIKI = "wiki"
    EMPTY = "empty"


@dataclass(slots=True)
class InboxDecision:
    inbound_trace_id: str
    category: InboxCategory
    confidence: float
    extracted: dict[str, Any] = field(default_factory=dict)
    recommended_operation: str = ""
    recommended_payload: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    classified_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        return data


class ForwardedContentRouter:
    """Heuristic inbox classifier.

    All methods are pure (no side effects).  The caller decides whether
    to actually run the recommended operation — keeps inbox classification
    decoupled from execution, matching the v0.27 ``TaskRouter`` pattern.
    """

    DEFAULT_DOMAIN_FOR_URL = "research"

    def classify(
        self,
        inbound: InboundMessage,
        *,
        default_user_id: str = "",
        default_domain: str = "",
    ) -> InboxDecision:
        body = (inbound.body or "").strip()
        subject = (inbound.subject or "").strip()
        haystack = f"{body}\n{subject}".strip()

        if not haystack:
            return InboxDecision(
                inbound_trace_id=inbound.trace_id,
                category=InboxCategory.EMPTY,
                confidence=0.0,
                note="empty body + subject",
            )

        # 1) .ics — strongest signal: explicit VCALENDAR header
        if _ICAL_HEADER_PATTERN.search(haystack):
            return self._decide_calendar(inbound, haystack, default_user_id)

        # 2) URL detection — first URL wins; if it's a PDF, classify as PDF
        url_match = _URL_PATTERN.search(haystack)
        if url_match:
            url = url_match.group("url")
            if _PDF_PATTERN.search(url):
                return self._decide_pdf(inbound, url, default_user_id)
            return self._decide_url(inbound, url, default_domain)

        # 3) task-language: verb + optional date hint
        if self._looks_like_task(haystack):
            return self._decide_task(inbound, haystack, default_user_id)

        # 4) fallback → wiki ingest
        return self._decide_wiki(inbound, haystack, default_domain)

    # ---- per-category builders ----------------------------------

    def _decide_calendar(
        self, inbound: InboundMessage, body: str, user_id: str,
    ) -> InboxDecision:
        return InboxDecision(
            inbound_trace_id=inbound.trace_id,
            category=InboxCategory.CALENDAR_INVITE,
            confidence=0.95,
            extracted={"ics_body": body},
            recommended_operation="calendar_import_ics",
            recommended_payload={"user_id": user_id, "ics_body": body},
            note=".ics VCALENDAR header detected",
        )

    def _decide_pdf(
        self, inbound: InboundMessage, url: str, user_id: str,
    ) -> InboxDecision:
        return InboxDecision(
            inbound_trace_id=inbound.trace_id,
            category=InboxCategory.PDF,
            confidence=0.9,
            extracted={"url": url},
            recommended_operation="capture_url",
            recommended_payload={
                "url": url,
                "note": f"pdf forwarded via {inbound.channel}",
            },
            note="PDF link detected — capture-url + vault/raw ingest",
        )

    def _decide_url(
        self, inbound: InboundMessage, url: str, domain: str,
    ) -> InboxDecision:
        return InboxDecision(
            inbound_trace_id=inbound.trace_id,
            category=InboxCategory.URL,
            confidence=0.85,
            extracted={"url": url},
            recommended_operation="capture_url",
            recommended_payload={
                "url": url,
                "note": f"forwarded via {inbound.channel}",
            },
            note=(
                f"web URL detected; suggest follow-up "
                f"`wiki-propose-research --source forwarded --domain "
                f"{domain or self.DEFAULT_DOMAIN_FOR_URL}` after capture"
            ),
        )

    def _decide_task(
        self, inbound: InboundMessage, body: str, user_id: str,
    ) -> InboxDecision:
        date_match = _DATE_HINT_PATTERN.search(body)
        date_hint = date_match.group(0) if date_match else ""
        return InboxDecision(
            inbound_trace_id=inbound.trace_id,
            category=InboxCategory.TASK,
            confidence=0.7,
            extracted={
                "title": body[:80].strip(),
                "date_hint": date_hint,
            },
            recommended_operation="personal_task_add",
            recommended_payload={
                "user_id": user_id,
                "title": body[:80].strip(),
                "description": body if len(body) > 80 else "",
                "priority": 3,
                "estimated_minutes": 30,
                "category": "other",
                "due_at_hint": date_hint,
            },
            note=("task-language detected" +
                  (f" with date hint {date_hint!r}" if date_hint else "")),
        )

    def _decide_wiki(
        self, inbound: InboundMessage, body: str, domain: str,
    ) -> InboxDecision:
        return InboxDecision(
            inbound_trace_id=inbound.trace_id,
            category=InboxCategory.WIKI,
            confidence=0.4,
            extracted={"text": body[:1000]},
            recommended_operation="wiki_propose_research",
            recommended_payload={
                "source": "forwarded",
                "domain": domain or "research",
                "path": "",                # caller writes vault/raw first
                "body_preview": body[:1000],
            },
            note="fallback — propose as wiki draft",
        )

    # ---- detection helper --------------------------------------

    @staticmethod
    def _looks_like_task(haystack: str) -> bool:
        if any(p.search(haystack) for p in _TASK_VERB_PATTERNS):
            return True
        # Date hint AND short body (< 200 chars) — likely a reminder.
        if _DATE_HINT_PATTERN.search(haystack) and len(haystack) < 200:
            return True
        return False


__all__ = ["ForwardedContentRouter", "InboxCategory", "InboxDecision"]
