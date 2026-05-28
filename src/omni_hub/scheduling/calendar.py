"""CalendarStore — iCal-format calendar (v0.32).

Stores events under ``vault/users/<user_id>/calendar/<YYYY-MM>.ics`` so
the user can sync the monthly file to their phone via any CalDAV /
WebDAV / file-sync client.  Stdlib-only RFC 5545 parser + writer
(enough to handle ``VEVENT`` + line-folding + escaping; we do NOT
implement RRULE expansion in v0.32 — recurring events store the rule
string verbatim and surface as a TODO for the user).
"""

from __future__ import annotations

import re
import secrets
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


_LINE_FOLD = "\r\n "
_LINE_BREAK_RE = re.compile(r"\r?\n")
_CRLF = "\r\n"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_uid() -> str:
    return f"omni-{secrets.token_hex(8)}@omni-hub.local"


def _format_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt_utc = dt.astimezone(UTC)
    return dt_utc.strftime("%Y%m%dT%H%M%SZ")


def _parse_dt(value: str) -> datetime:
    """Parse iCal UTC ``YYYYMMDDTHHMMSSZ`` or floating local."""

    value = value.strip()
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    if "T" in value and len(value) == 15:
        # local floating — treat as UTC for storage stability
        return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    # all-day "YYYYMMDD"
    return datetime.strptime(value, "%Y%m%d").replace(tzinfo=UTC)


def _escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def _unescape(text: str) -> str:
    return (text or "").replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _fold(line: str) -> str:
    """RFC 5545 line folding at 75 octets."""

    line = line.replace("\n", "\\n")
    if len(line) <= 75:
        return line
    parts = [line[:75]]
    remaining = line[75:]
    while remaining:
        parts.append(remaining[:74])
        remaining = remaining[74:]
    return _LINE_FOLD.join(parts)


def _unfold(text: str) -> list[str]:
    """Reverse RFC 5545 line folding."""

    out: list[str] = []
    for raw in _LINE_BREAK_RE.split(text):
        if raw.startswith((" ", "\t")) and out:
            out[-1] += raw[1:]
        else:
            out.append(raw)
    return [ln for ln in out if ln.strip()]


class EventKind(str, Enum):
    VEVENT = "VEVENT"
    VTODO = "VTODO"


class EventStatus(str, Enum):
    TENTATIVE = "TENTATIVE"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


@dataclass(slots=True)
class CalendarEvent:
    user_id: str
    uid: str
    summary: str
    start: datetime
    end: datetime
    kind: EventKind = EventKind.VEVENT
    status: EventStatus = EventStatus.CONFIRMED
    description: str = ""
    location: str = ""
    categories: list[str] = field(default_factory=list)
    rrule: str = ""                     # stored verbatim; not expanded in v0.32
    created_at: datetime = field(default_factory=_utcnow)
    last_modified: datetime = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def duration_minutes(self) -> int:
        delta: timedelta = self.end - self.start
        return int(delta.total_seconds() // 60)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["start"] = self.start.isoformat()
        data["end"] = self.end.isoformat()
        data["created_at"] = self.created_at.isoformat()
        data["last_modified"] = self.last_modified.isoformat()
        data["kind"] = self.kind.value
        data["status"] = self.status.value
        return data

    def to_ical(self) -> str:
        lines = [f"BEGIN:{self.kind.value}", f"UID:{self.uid}",
                 f"SUMMARY:{_escape(self.summary)}"]
        if self.kind is EventKind.VEVENT:
            lines.append(f"DTSTART:{_format_dt(self.start)}")
            lines.append(f"DTEND:{_format_dt(self.end)}")
        else:                                       # VTODO
            lines.append(f"DTSTART:{_format_dt(self.start)}")
            lines.append(f"DUE:{_format_dt(self.end)}")
        lines.append(f"DTSTAMP:{_format_dt(self.created_at)}")
        lines.append(f"LAST-MODIFIED:{_format_dt(self.last_modified)}")
        lines.append(f"STATUS:{self.status.value}")
        if self.description:
            lines.append(f"DESCRIPTION:{_escape(self.description)}")
        if self.location:
            lines.append(f"LOCATION:{_escape(self.location)}")
        if self.categories:
            lines.append(f"CATEGORIES:{_escape(','.join(self.categories))}")
        if self.rrule:
            lines.append(f"RRULE:{self.rrule}")
        if self.metadata:
            lines.append(f"X-OMNI-METADATA:{_escape(__import__('json').dumps(self.metadata))}")
        lines.append(f"END:{self.kind.value}")
        return _CRLF.join(_fold(line) for line in lines)


class CalendarStore:
    """Per-user iCal-on-disk calendar."""

    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace).resolve()

    def _user_root(self, user_id: str) -> Path:
        return self.workspace / "vault" / "users" / user_id / "calendar"

    def _month_file(self, user_id: str, when: datetime) -> Path:
        return self._user_root(user_id) / f"{when.strftime('%Y-%m')}.ics"

    # ---- CRUD ---------------------------------------------------

    def add_event(
        self,
        *,
        user_id: str,
        summary: str,
        start: datetime,
        end: datetime,
        description: str = "",
        location: str = "",
        categories: Iterable[str] = (),
        kind: EventKind = EventKind.VEVENT,
        status: EventStatus = EventStatus.CONFIRMED,
        rrule: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> CalendarEvent:
        event = CalendarEvent(
            user_id=user_id, uid=_new_uid(),
            summary=summary, start=start, end=end,
            description=description, location=location,
            categories=list(categories),
            kind=kind, status=status, rrule=rrule,
            metadata=dict(metadata or {}),
        )
        self._append(event)
        return event

    def list_events(
        self,
        user_id: str,
        *,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> list[CalendarEvent]:
        root = self._user_root(user_id)
        if not root.exists():
            return []
        out: list[CalendarEvent] = []
        for monthly in sorted(root.glob("*.ics")):
            out.extend(self._parse_file(monthly, user_id))
        if window_start:
            out = [e for e in out if e.end >= window_start]
        if window_end:
            out = [e for e in out if e.start <= window_end]
        return sorted(out, key=lambda e: e.start)

    def overview(self, user_id: str) -> dict[str, Any]:
        events = self.list_events(user_id)
        confirmed = sum(1 for e in events if e.status is EventStatus.CONFIRMED)
        tentative = sum(1 for e in events if e.status is EventStatus.TENTATIVE)
        cancelled = sum(1 for e in events if e.status is EventStatus.CANCELLED)
        return {
            "total": len(events),
            "by_status": {
                "confirmed": confirmed,
                "tentative": tentative,
                "cancelled": cancelled,
            },
            "next_event": events[0].to_dict() if events else None,
        }

    # ---- import / export ---------------------------------------

    def import_ics(self, user_id: str, raw_body: str) -> list[CalendarEvent]:
        """Append every VEVENT / VTODO from a pasted .ics blob.

        Returns the list of imported events.  Existing UIDs collide and
        raise (no implicit overwrite — operator-explicit).
        """

        events = self._parse_text(raw_body, user_id)
        existing_uids = {e.uid for e in self.list_events(user_id)}
        for event in events:
            if event.uid in existing_uids:
                raise ValueError(f"UID {event.uid!r} already present")
            self._append(event)
        return events

    def export_ics(self, user_id: str) -> str:
        events = self.list_events(user_id)
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
                 "PRODID:-//omni-hub//v0.32//EN"]
        for event in events:
            lines.append(event.to_ical())
        lines.append("END:VCALENDAR")
        return _CRLF.join(lines)

    # ---- internals --------------------------------------------

    def _append(self, event: CalendarEvent) -> None:
        target = self._month_file(event.user_id, event.start)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(
                _CRLF.join(["BEGIN:VCALENDAR", "VERSION:2.0",
                            "PRODID:-//omni-hub//v0.32//EN",
                            event.to_ical(),
                            "END:VCALENDAR"]),
                encoding="utf-8",
            )
            return
        # Insert before the closing END:VCALENDAR line.
        body = target.read_text(encoding="utf-8")
        idx = body.rfind("END:VCALENDAR")
        if idx == -1:
            target.write_text(body + _CRLF + event.to_ical(), encoding="utf-8")
            return
        target.write_text(
            body[:idx] + event.to_ical() + _CRLF + body[idx:],
            encoding="utf-8",
        )

    def _parse_file(self, path: Path, user_id: str) -> list[CalendarEvent]:
        return self._parse_text(path.read_text(encoding="utf-8"), user_id)

    def _parse_text(self, text: str, user_id: str) -> list[CalendarEvent]:
        lines = _unfold(text)
        events: list[CalendarEvent] = []
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith(("BEGIN:VEVENT", "BEGIN:VTODO")):
                kind = EventKind.VEVENT if "VEVENT" in line else EventKind.VTODO
                event_data: dict[str, str] = {"kind": kind.value}
                metadata: dict[str, Any] = {}
                i += 1
                while i < len(lines) and not lines[i].startswith(
                    ("END:VEVENT", "END:VTODO"),
                ):
                    if ":" in lines[i]:
                        key, _, value = lines[i].partition(":")
                        key = key.split(";", 1)[0]
                        if key == "X-OMNI-METADATA":
                            try:
                                metadata = __import__("json").loads(_unescape(value))
                            except Exception:                       # noqa: BLE001
                                metadata = {}
                        else:
                            event_data[key] = value
                    i += 1
                try:
                    events.append(self._row_to_event(event_data, kind, user_id, metadata))
                except (KeyError, ValueError):
                    pass    # skip malformed entries; never crash a parse
            i += 1
        return events

    @staticmethod
    def _row_to_event(
        row: dict[str, str], kind: EventKind, user_id: str,
        metadata: dict[str, Any],
    ) -> CalendarEvent:
        uid = row.get("UID") or _new_uid()
        summary = _unescape(row.get("SUMMARY", ""))
        if kind is EventKind.VEVENT:
            start = _parse_dt(row["DTSTART"])
            end = _parse_dt(row["DTEND"])
        else:
            start = _parse_dt(row.get("DTSTART") or row["DUE"])
            end = _parse_dt(row.get("DUE") or row["DTSTART"])
        status_raw = row.get("STATUS", "CONFIRMED")
        try:
            status = EventStatus(status_raw)
        except ValueError:
            status = EventStatus.CONFIRMED
        created_at = _parse_dt(row["DTSTAMP"]) if "DTSTAMP" in row else _utcnow()
        last_modified = _parse_dt(row["LAST-MODIFIED"]) if "LAST-MODIFIED" in row else created_at
        categories_raw = _unescape(row.get("CATEGORIES", ""))
        categories = [c.strip() for c in categories_raw.split(",") if c.strip()]
        return CalendarEvent(
            user_id=user_id, uid=uid,
            summary=summary, start=start, end=end,
            kind=kind, status=status,
            description=_unescape(row.get("DESCRIPTION", "")),
            location=_unescape(row.get("LOCATION", "")),
            categories=categories,
            rrule=row.get("RRULE", ""),
            created_at=created_at, last_modified=last_modified,
            metadata=metadata,
        )


__all__ = ["CalendarEvent", "CalendarStore", "EventKind", "EventStatus"]
