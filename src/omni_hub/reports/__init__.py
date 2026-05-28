"""Daily / weekly / monthly markdown reports built on top of memory.

These are templates that consume:
- the local memory store (``omni_hub.memory.MemoryStore``)
- the preference store (``omni_hub.harness.preference``)
- the redundancy proposals (``omni_hub.harness.redundancy``)

Reports are pure markdown — no external dependencies.  The CLI subcommands
write to ``vault/40_Reports/<period>/<date>.md`` by default so they land in
your existing Obsidian vault.
"""

from .core import (
    ReportContext,
    build_daily,
    build_monthly,
    build_weekly,
    default_output_path,
)

__all__ = [
    "ReportContext",
    "build_daily",
    "build_monthly",
    "build_weekly",
    "default_output_path",
]
