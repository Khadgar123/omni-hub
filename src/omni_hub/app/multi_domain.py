"""Multi-domain orchestrator — WS2 of the 2026-05-29 refactor.

The overlap problem this solves: a query spanning several domains used to
route to a single top-1 domain (losing the others) or risk N domain skills
each re-running their own retrieval over the same query.

The fix is the **orchestrator-worker** pattern (Anthropic's multi-agent
research shape): a lead step routes the query to N domains via the existing
LLM-free ``TaskRouter.route_multi``, then fans out the SHARED retrieval
primitive (one ``Cascade`` instance) once per domain with that domain's
cascade config.  Workers never overlap — each is scoped to its own domain —
and each carries an explicit delegation contract (objective + scope
boundary) so a downstream synthesizer knows exactly what each slice covers.

Gather-only: this does NOT synthesize into the wiki.  Synthesis + any
persistent claim still go through ``Proposal[T]`` (HR#5).  The output is a
structured, citation-ready bundle a single synthesis step can consume.

Composes existing primitives (the same ``Cascade`` the ``retrieve`` op
uses) — no new heavy deps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .task_router import TaskRouter


@dataclass(slots=True)
class DomainWorkResult:
    """One domain's slice of a multi-domain query."""

    domain: str                        # == DomainRoute.skill_id
    objective: str                     # the explicit delegation contract
    confidence: float = 0.0
    records: list[dict[str, Any]] = field(default_factory=list)
    sources_tried: list[str] = field(default_factory=list)
    sources_succeeded: list[str] = field(default_factory=list)
    coverage_ok: bool = True
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "objective": self.objective,
            "confidence": self.confidence,
            "records": self.records,
            "sources_tried": self.sources_tried,
            "sources_succeeded": self.sources_succeeded,
            "coverage_ok": self.coverage_ok,
            "error": self.error,
        }


@dataclass(slots=True)
class MultiDomainBundle:
    query: str
    primary_domain: str
    is_multi_domain: bool = False
    domains: list[DomainWorkResult] = field(default_factory=list)
    coverage_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "primary_domain": self.primary_domain,
            "is_multi_domain": self.is_multi_domain,
            "domain_count": len(self.domains),
            "record_count": sum(len(d.records) for d in self.domains),
            "coverage_warnings": self.coverage_warnings,
            "domains": [d.to_dict() for d in self.domains],
        }


def _delegation_objective(query: str, domain: str) -> str:
    """Explicit per-worker contract (Anthropic: vague tasks -> duplicated work)."""

    return (
        f"Retrieve evidence for '{query}' strictly within the {domain} domain. "
        f"Use only the {domain} cascade sources; do not stray into other "
        f"domains (a sibling worker covers those). Return cited records only."
    )


def _record_to_dict(rec: Any) -> dict[str, Any]:
    if isinstance(rec, dict):
        return rec
    to_dict = getattr(rec, "to_dict", None)
    return to_dict() if callable(to_dict) else {"value": str(rec)}


def orchestrate(
    workspace: Path | str,
    query: str,
    *,
    max_domains: int = 4,
    min_ratio: float = 0.5,
    per_source_limit: int = 5,
    total_limit: int = 12,
    fusion: str = "rrf",
    router: TaskRouter | None = None,
    cascade: Any | None = None,
) -> MultiDomainBundle:
    """Route ``query`` to N domains and fan out one cascade.retrieve per domain.

    Pure orchestration over the shared ``Cascade`` primitive — one instance,
    called once per routed domain with that domain's name (so it picks up
    that domain's cascade).  ``cascade`` is injectable for testing.
    """

    query = query.strip()
    if not query:
        raise ValueError("orchestrate requires a non-empty query")

    workspace_root = Path(workspace).resolve()
    router = router or TaskRouter()

    from ..channels.base import InboundMessage

    inbound = InboundMessage.new(channel="cli", sender="orchestrator", body=query)
    decision = router.route_multi(inbound, min_ratio=min_ratio, max_domains=max_domains)

    if cascade is None:
        from ..retrieval import Cascade, builtin_sources
        cascade = Cascade(builtin_sources())

    bundle = MultiDomainBundle(
        query=query,
        primary_domain=decision.primary_skill_id,
        is_multi_domain=decision.is_multi_domain,
    )

    for route in decision.domains:
        domain = route.skill_id
        objective = _delegation_objective(query, domain)
        try:
            result = cascade.retrieve(
                query,
                domain=domain,
                per_source_limit=per_source_limit,
                total_limit=total_limit,
                fusion=fusion,  # type: ignore[arg-type]
            )
        except Exception as exc:                                   # noqa: BLE001
            bundle.domains.append(
                DomainWorkResult(
                    domain=domain, objective=objective,
                    confidence=route.confidence, error=str(exc),
                )
            )
            bundle.coverage_warnings.append(f"{domain}: retrieval failed ({exc})")
            continue

        tried = list(getattr(result, "sources_tried", []) or [])
        succeeded = list(getattr(result, "sources_succeeded", []) or [])
        records = [_record_to_dict(r) for r in getattr(result, "records", []) or []]

        # Coverage assertion (review doc §1.2 under-sourcing guard): a query
        # that planned K sources but landed < ceil(K/2) is silently degraded.
        planned = max(len(tried), 1)
        needed = math.ceil(planned * 0.5)
        coverage_ok = len(succeeded) >= needed
        if not coverage_ok:
            bundle.coverage_warnings.append(
                f"{domain}: only {len(succeeded)}/{planned} sources returned "
                f"(needed >= {needed}); results may be under-sourced"
            )

        bundle.domains.append(
            DomainWorkResult(
                domain=domain,
                objective=objective,
                confidence=route.confidence,
                records=records,
                sources_tried=tried,
                sources_succeeded=succeeded,
                coverage_ok=coverage_ok,
            )
        )

    return bundle


__all__ = ["orchestrate", "MultiDomainBundle", "DomainWorkResult"]
