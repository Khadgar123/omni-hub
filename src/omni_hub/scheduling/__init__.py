"""Scheduling plane (v0.32).

Three components:

* :class:`CalendarStore` — iCal-format calendar; parses + writes
  ``VEVENT`` blocks per RFC 5545.  Stdlib-only: pure-Python folder
  parser + serialiser.
* :class:`PersonalTaskStore` — SQLite-backed personal task list (NOT
  the worker ``TaskQueue``).  Has due / priority / category /
  status, can be promoted onto the calendar.
* :class:`TimeBlockPlanner` — deterministic priority + duration
  solver that places unscheduled tasks into free calendar slots
  (Motion / Reclaim pattern, without the LLM).

Multi-user: every store accepts ``user_id`` (defaults to the
project owner via :data:`omni_hub.users.DEFAULT_USER_HANDLE`).
"""

from __future__ import annotations

from .calendar import (
    CalendarEvent,
    CalendarStore,
    EventKind,
    EventStatus,
)
from .tasks import (
    PersonalTask,
    PersonalTaskStore,
    TaskCategory,
    TaskStatus,
)
from .time_block import (
    PlannedBlock,
    TimeBlockPlanner,
)

__all__ = [
    "CalendarEvent",
    "CalendarStore",
    "EventKind",
    "EventStatus",
    "PersonalTask",
    "PersonalTaskStore",
    "PlannedBlock",
    "TaskCategory",
    "TaskStatus",
    "TimeBlockPlanner",
]
