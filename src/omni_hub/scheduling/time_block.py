"""TimeBlockPlanner — deterministic priority+duration solver (v0.32).

Given:

* a list of :class:`PersonalTask` items with ``estimated_minutes`` +
  ``priority`` + (optional) ``due_at``,
* a list of busy :class:`CalendarEvent` (already-confirmed events that
  cannot be moved),
* a "working window" (start hour, end hour, weekdays),

the planner places tasks into free slots, sorted by:

1. tasks with imminent ``due_at`` first
2. then by ``priority`` (1 = highest)
3. then by ``created_at``

It's deterministic (no LLM call) and idempotent — same input always
yields the same plan.  Motion / Reclaim use a fancier MILP solver
underneath; for v0.32 the greedy slot-fitter is enough for personal
use (~50 tasks/week).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any, Iterable

from .calendar import CalendarEvent
from .tasks import PersonalTask, TaskStatus


@dataclass(slots=True)
class PlannedBlock:
    """One task placement decision."""

    task_id: str
    title: str
    start: datetime
    end: datetime
    estimated_minutes: int
    priority: int
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["start"] = self.start.isoformat()
        data["end"] = self.end.isoformat()
        return data


class TimeBlockPlanner:
    """Greedy slot-fitter."""

    DEFAULT_WORK_START = time(hour=9)
    DEFAULT_WORK_END = time(hour=18)
    DEFAULT_WEEKDAYS = frozenset({0, 1, 2, 3, 4})        # Mon-Fri

    def __init__(
        self,
        *,
        work_start: time = DEFAULT_WORK_START,
        work_end: time = DEFAULT_WORK_END,
        weekdays: Iterable[int] = DEFAULT_WEEKDAYS,
        min_gap_minutes: int = 5,
    ) -> None:
        if work_end <= work_start:
            raise ValueError("work_end must be after work_start")
        self.work_start = work_start
        self.work_end = work_end
        self.weekdays = frozenset(weekdays)
        self.min_gap = timedelta(minutes=max(0, int(min_gap_minutes)))

    # ---- public -------------------------------------------------

    def plan(
        self,
        tasks: list[PersonalTask],
        events: list[CalendarEvent],
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[PlannedBlock]:
        """Place open tasks into free slots in ``[window_start, window_end)``.

        Tasks already ``done`` / ``cancelled`` are skipped.  Tasks that
        can't fit (estimate longer than any free slot, or no free slot
        before due_at) are returned as :class:`PlannedBlock` with
        ``note='unfittable'`` so the caller can surface them.
        """

        tasks_to_place = [
            t for t in tasks if t.status in (TaskStatus.OPEN, TaskStatus.IN_PROGRESS)
        ]
        tasks_to_place.sort(key=self._task_sort_key)

        busy = self._busy_slots(events, window_start, window_end)
        free = self._invert_busy(busy, window_start, window_end)
        out: list[PlannedBlock] = []
        for task in tasks_to_place:
            duration = timedelta(minutes=task.estimated_minutes)
            placement = self._pick_slot(free, duration, task)
            if placement is None:
                out.append(PlannedBlock(
                    task_id=task.task_id, title=task.title,
                    start=window_start, end=window_start,
                    estimated_minutes=task.estimated_minutes,
                    priority=task.priority,
                    note="unfittable",
                ))
                continue
            slot_idx, slot_start = placement
            slot_end = slot_start + duration
            out.append(PlannedBlock(
                task_id=task.task_id, title=task.title,
                start=slot_start, end=slot_end,
                estimated_minutes=task.estimated_minutes,
                priority=task.priority,
            ))
            # Consume the slot.
            self._consume_slot(free, slot_idx, slot_start, slot_end)
        return out

    # ---- internals ---------------------------------------------

    @staticmethod
    def _task_sort_key(t: PersonalTask) -> tuple[float, int, str]:
        # Most-imminent due first (empty due → infinity), then priority,
        # then created_at.
        due_score = 9999999999.0
        if t.due_at:
            try:
                due_score = datetime.fromisoformat(
                    t.due_at.replace("Z", "+00:00"),
                ).timestamp()
            except ValueError:
                pass
        return (due_score, t.priority, t.created_at)

    def _busy_slots(
        self,
        events: list[CalendarEvent],
        window_start: datetime,
        window_end: datetime,
    ) -> list[tuple[datetime, datetime]]:
        busy: list[tuple[datetime, datetime]] = []
        for event in events:
            if event.end <= window_start or event.start >= window_end:
                continue
            s = max(event.start, window_start)
            e = min(event.end, window_end)
            busy.append((s, e))
        busy.sort()
        # Merge overlaps.
        merged: list[tuple[datetime, datetime]] = []
        for s, e in busy:
            if merged and s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        return merged

    def _invert_busy(
        self,
        busy: list[tuple[datetime, datetime]],
        window_start: datetime,
        window_end: datetime,
    ) -> list[tuple[datetime, datetime]]:
        """Return the per-day free intervals (clipped to work hours)."""

        free: list[tuple[datetime, datetime]] = []
        cursor = window_start
        day = cursor.date()
        while day <= window_end.date():
            day_start = datetime.combine(
                day, self.work_start, tzinfo=window_start.tzinfo or UTC,
            )
            day_end = datetime.combine(
                day, self.work_end, tzinfo=window_start.tzinfo or UTC,
            )
            day_start = max(day_start, window_start)
            day_end = min(day_end, window_end)
            if day.weekday() not in self.weekdays or day_end <= day_start:
                day = day + timedelta(days=1)
                continue
            # Walk this day's free intervals.
            ptr = day_start
            for s, e in busy:
                if e <= day_start or s >= day_end:
                    continue
                if s > ptr:
                    free.append((ptr, min(s, day_end)))
                ptr = max(ptr, min(e, day_end))
            if ptr < day_end:
                free.append((ptr, day_end))
            day = day + timedelta(days=1)
        free.sort()
        return [(s, e) for s, e in free if (e - s) > timedelta(0)]

    def _pick_slot(
        self,
        free: list[tuple[datetime, datetime]],
        duration: timedelta,
        task: PersonalTask,
    ) -> tuple[int, datetime] | None:
        """Return (slot_index, start_dt) or None if no fit before due_at."""

        due: datetime | None = None
        if task.due_at:
            try:
                due = datetime.fromisoformat(task.due_at.replace("Z", "+00:00"))
            except ValueError:
                due = None
        for idx, (s, e) in enumerate(free):
            if due and s >= due:
                continue
            available = e - s
            if available >= duration:
                return idx, s
        return None

    def _consume_slot(
        self,
        free: list[tuple[datetime, datetime]],
        slot_idx: int,
        slot_start: datetime,
        slot_end: datetime,
    ) -> None:
        original_start, original_end = free[slot_idx]
        # Replace the consumed slot with the remainder (if any).
        new_start = slot_end + self.min_gap
        if new_start < original_end:
            free[slot_idx] = (new_start, original_end)
        else:
            free.pop(slot_idx)


__all__ = ["PlannedBlock", "TimeBlockPlanner"]
