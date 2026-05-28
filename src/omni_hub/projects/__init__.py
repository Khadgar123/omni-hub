"""Projects plane (v0.34).

A **Project** is a high-level user goal (e.g. "ship the v0.40 release",
"write the ACE paper", "refactor the cascade module") that decomposes
into multiple worker tasks and stretches over days / weeks.  Distinct
from:

* :class:`omni_hub.scheduling.PersonalTask` — single todo item, no
  decomposition.
* :class:`omni_hub.queue.TaskQueue` — atomic worker units of work.

A project's life:

    pending  →  planning  →  in_progress  →  done | cancelled
       │           │              │
       │           │              ├── ↑ N worker tasks dispatched via TaskQueue
       │           └── claude lane writes plan + sub-task list →
       └── user invokes project-plan
              Proposal(kind="project_plan") → human approves

Per Devin / Cursor / Aider architect-mode patterns (2026 Q2 SOTA): a
planner agent decomposes by dependency tree; worker agents fan out;
PR-level review (not keystroke) gates everything.
"""

from __future__ import annotations

from .store import (
    Project,
    ProjectStatus,
    ProjectStore,
    SubTask,
)

__all__ = [
    "Project",
    "ProjectStatus",
    "ProjectStore",
    "SubTask",
]
