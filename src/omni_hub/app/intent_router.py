"""AppIntentRouter — explicit 2-level router (v0.40).

The v0.39 :class:`TaskRouter` returns ``selected_skill_id`` (domain) AND
``app_intents`` (intent) in one decision, but doesn't make the precedence
explicit.  The 2026-05-28 (round 4) review asked for:

    "做 app_intent_router：先判功能，再判领域；覆盖 PPT、项目、日程、转发、
     金融、报告、聊天"

So this module composes TaskRouter into a 2-level pipeline:

    AppIntentRouter
       │
       ├── 1. classify INTENT (what does the user want done?)
       │       schedule / task / report / pptx / project /
       │       inbox / finance_op / chat
       │
       ├── 2. classify DOMAIN (what subject matter?)
       │       research / engineering / finance / cn_policy / ...
       │       (delegated to TaskRouter for the legacy keyword + intent map)
       │
       └── 3. resolve FOUNDATION TOOLS (what primitives compose the answer?)
               retrieve / context-pack / wiki-ingest / propose-approve / ...

Returns :class:`AppRouteDecision`, a richer-than-RoutingDecision payload
with the three axes spelled out so callers can route to the right
functional skill without re-running the TaskRouter heuristics.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..channels.base import InboundMessage
from .task_router import (
    AppIntent,
    RoutingDecision,
    TaskRouter,
)


# ---------------------------------------------------------------------------
# Foundation-tools mapping per (intent, domain) combo
# ---------------------------------------------------------------------------


# Per-intent default foundation tool list.  Callers can override.
_INTENT_TOOLS: dict[str, list[str]] = {
    "schedule":   ["context-pack", "calendar-add"],
    "task":       ["task-add"],
    "report":     ["context-pack", "wiki-search", "claims-show"],
    "pptx":       ["context-pack", "wiki-search"],
    "project":    ["context-pack"],
    "inbox":      ["url-capture", "wiki-propose-research"],
    "finance_op": ["context-pack", "propose-approve"],
    "chat":       ["context-pack", "wiki-search"],
}

# When no app intent fires, the 2-level router still surfaces the domain
# tools so the caller can build a context pack + answer.
_DEFAULT_TOOLS = ["context-pack", "wiki-search"]


@dataclass(slots=True)
class AppRouteDecision:
    """The 2-level router's verdict for one InboundMessage."""

    inbound_trace_id: str
    # Level 1 — what the user wants done
    primary_intent: str = ""                # "schedule" | "report" | "pptx" | ... | ""
    intent_confidence: float = 0.0
    secondary_intents: list[str] = field(default_factory=list)
    # Level 2 — subject matter
    domain: str = ""
    domain_confidence: float = 0.0
    # Level 3 — foundation primitives the functional skill should compose
    foundation_tools: list[str] = field(default_factory=list)
    # The recommended next operation (intent's canonical builtin)
    next_operation: str = ""
    next_payload: dict[str, Any] = field(default_factory=dict)
    # Audit + UI
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AppIntentRouter:
    """2-level router: intent → domain → foundation tools.

    Composition over inheritance: an instance owns a :class:`TaskRouter`
    and uses it for the domain + intent keyword maps.  This class
    encodes the *precedence* — the v0.40 review's correction.
    """

    def __init__(self, *, task_router: TaskRouter | None = None) -> None:
        self.task_router = task_router or TaskRouter()

    def route(self, inbound: InboundMessage) -> AppRouteDecision:
        # Lean on TaskRouter for the heavy keyword scoring.
        td: RoutingDecision = self.task_router.route(inbound)

        primary_intent = td.primary_intent
        secondary = [a.intent for a in td.app_intents[1:4]]

        # Foundation tool list: per-intent default, fall back to default
        # read tools when no intent fires.
        tools = list(_INTENT_TOOLS.get(primary_intent, _DEFAULT_TOOLS))

        # When the intent overrides, recommended_operation already points
        # at the right functional builtin (calendar_add / pptx_build / ...).
        # When no intent fires, fall back to context_pack_build.
        intent_confidence = (
            td.app_intents[0].confidence if td.app_intents else 0.0
        )

        note_parts = []
        if primary_intent:
            note_parts.append(
                f"intent={primary_intent} (conf {intent_confidence:.2f})"
            )
        note_parts.append(f"domain={td.selected_skill_id} (conf {td.confidence:.2f})")
        if secondary:
            note_parts.append(f"also detected: {', '.join(secondary)}")

        return AppRouteDecision(
            inbound_trace_id=inbound.trace_id,
            primary_intent=primary_intent,
            intent_confidence=intent_confidence,
            secondary_intents=secondary,
            domain=td.selected_skill_id,
            domain_confidence=td.confidence,
            foundation_tools=tools,
            next_operation=td.recommended_operation,
            next_payload=td.recommended_payload,
            note=" · ".join(note_parts),
        )


__all__ = ["AppIntentRouter", "AppRouteDecision"]
