"""Application Plane — orchestration over Knowledge + Skill Planes (v0.19).

The Application Plane is **above** Skills.  It does NOT call LLMs directly;
it composes existing skills (via OperationRunner) and existing knowledge
projections (via knowledge_plane / projection registry) into user-facing
flows: daily/weekly/monthly reports, conversational task routing,
multi-step task planning.

For v0.19 we ship two skeletons:

* :class:`ReportOrchestrator` — cross-skill 日/周/月报 by aggregating
  ClaimLedger stats + lint findings + preference deltas + workflow runs
  (NO LLM call — pure markdown rollup).
* :class:`TaskRouter` — keyword-heuristic classification of an InboundMessage
  into one of the 19 skill domains, returning a routing decision plus a
  ready-to-go context pack (the actual answer is generated downstream
  by claude/codex workers behind ``Proposal[T]``).

Both consume the unified ``Channel`` / ``InboundMessage`` types from
``omni_hub.channels`` so Interface Plane → Application Plane is a clean
boundary.
"""

from __future__ import annotations

from .report_orchestrator import (
    NarrativeRequest,
    ReportOrchestrator,
    ReportPeriod,
    ReportSection,
    ReportSummary,
)
from .intent_router import AppIntentRouter, AppRouteDecision
from .task_router import (
    AppIntent,
    ConversationTurn,
    RoutingDecision,
    TaskRouter,
)

__all__ = [
    "AppIntent",
    "AppIntentRouter",
    "AppRouteDecision",
    "ConversationTurn",
    "NarrativeRequest",
    "ReportOrchestrator",
    "ReportPeriod",
    "ReportSection",
    "ReportSummary",
    "RoutingDecision",
    "TaskRouter",
]
