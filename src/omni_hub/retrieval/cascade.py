"""Domain-aware retrieval cascade.

Maps a query plus a domain profile to an ordered list of sources to
consult; merges their results, deduplicates by URL, returns a single
ranked list of :class:`RetrievalRecord`.

The cascade is **best-effort**: a source failing (network, rate-limit,
auth) is logged into the record list as an error metadata entry but
does not abort the cascade — we still return whatever the working
sources gave us.  This mirrors how the harness ensemble tolerates
partial model failures.

Per-domain defaults match the project's ``agent-harness/domain-profiles.json``
keys plus two synthetic profiles (``ai_progress``, ``default``) that
roll up the most common cross-domain queries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import RetrievalError, RetrievalRecord, RetrievalSource, normalize_records


# Per-domain source list — order = cascade order, first hit wins on
# duplicates.  Sources missing from the registered set are skipped silently.
DEFAULT_DOMAIN_CASCADES: dict[str, list[str]] = {
    # The harness's 8 canonical domains
    "engineering":           ["openalex", "arxiv", "wikipedia"],
    "research":              ["openalex", "semantic_scholar", "arxiv", "wikipedia"],
    "photography":           ["wikipedia"],            # mostly reactive, forwarded links
    "fashion":               ["wikipedia"],            # same
    "chat_relationships":    [],                       # purely reactive
    "finance":               ["gdelt", "wikipedia"],
    "policy":                ["gdelt", "wikipedia"],
    "international_relations": ["gdelt", "wikipedia"],
    # Synthetic
    "ai_progress":           ["arxiv", "openalex", "wikipedia"],
    "default":               ["wikipedia", "openalex", "gdelt"],
}


@dataclass(slots=True)
class CascadeResult:
    """Combined cascade output — records + per-source diagnostics."""

    query: str
    domain: str
    records: list[RetrievalRecord] = field(default_factory=list)
    sources_tried: list[str] = field(default_factory=list)
    sources_succeeded: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "domain": self.domain,
            "count": len(self.records),
            "records": [r.to_dict() for r in self.records],
            "sources_tried": self.sources_tried,
            "sources_succeeded": self.sources_succeeded,
            "errors": self.errors,
        }


class Cascade:
    """Routes a query through a domain-specific stack of sources."""

    def __init__(
        self,
        sources: dict[str, RetrievalSource],
        *,
        cascades: dict[str, list[str]] | None = None,
    ) -> None:
        self.sources = dict(sources)
        self.cascades = dict(cascades) if cascades is not None else dict(DEFAULT_DOMAIN_CASCADES)

    def register(self, source: RetrievalSource) -> None:
        self.sources[source.name] = source

    def cascade_for(self, domain: str) -> list[str]:
        return self.cascades.get(domain, self.cascades.get("default", []))

    def retrieve(
        self,
        query: str,
        *,
        domain: str = "default",
        per_source_limit: int = 5,
        total_limit: int = 20,
        sources: list[str] | None = None,
    ) -> CascadeResult:
        """Run the cascade for ``domain`` (or an explicit ``sources`` list).

        Always returns a :class:`CascadeResult` — per-source failures land
        in ``errors`` instead of raising, so caller still gets a partial
        answer from whoever succeeded.
        """

        plan = sources if sources is not None else self.cascade_for(domain)
        result = CascadeResult(query=query, domain=domain)

        for source_name in plan:
            adapter = self.sources.get(source_name)
            if adapter is None:
                result.errors.append({
                    "source": source_name,
                    "error": "source not registered",
                })
                continue
            result.sources_tried.append(source_name)
            try:
                records = adapter.retrieve(
                    query, limit=per_source_limit, domain=domain,
                )
            except RetrievalError as exc:
                result.errors.append({"source": source_name, "error": str(exc)})
                continue
            except Exception as exc:                       # noqa: BLE001
                result.errors.append({
                    "source": source_name,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            for rec in records:
                rec.domain = domain or rec.domain
            result.records.extend(records)
            result.sources_succeeded.append(source_name)

        result.records = normalize_records(result.records, dedup_by_url=True)
        if len(result.records) > total_limit:
            result.records = result.records[:total_limit]
        return result
