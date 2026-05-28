"""Inbox plane (v0.33).

ForwardedContentRouter classifies an :class:`omni_hub.channels.InboundMessage`
into one of five typed handlers and produces a structured
:class:`InboxDecision` (no side effects — the caller dispatches).

Classifier categories:

* ``url``             — single web URL → suggest capture-url + wiki-propose
* ``pdf``             — PDF link or file:// → vault/raw ingest
* ``calendar_invite`` — .ics body or attachment → CalendarStore.import_ics
* ``task``            — task-language (with-due / verb) → PersonalTaskStore.add
* ``wiki``            — fallback → wiki-propose-research

Stdlib regex + heuristic, deterministic.  v0.40+ swaps to LLM-as-Classifier
when available.
"""

from __future__ import annotations

from .router import (
    ForwardedContentRouter,
    InboxCategory,
    InboxDecision,
)

__all__ = [
    "ForwardedContentRouter",
    "InboxCategory",
    "InboxDecision",
]
