"""Domain-aware retrieval cascade.

Maps a query plus a domain profile to an ordered list of sources to
consult; merges their results, deduplicates by URL (and ``canonical_id``
when set), returns a single ranked list of :class:`RetrievalRecord`.

The cascade is **best-effort**: a source failing (network, rate-limit,
auth) is logged into ``CascadeResult.errors`` but does not abort the
cascade — we still return whatever the working sources gave us.

Fusion strategies (`fusion=` kwarg on :meth:`Cascade.retrieve`):

* ``"concat"`` (legacy default) — preserve per-source order, concatenate
  in cascade order, dedup. This is what omni-hub shipped in v0.9 part 1.
* ``"rrf"`` (new, 2026 universal default) — Reciprocal Rank Fusion across
  sources: ``score = Σ 1/(k + rank_i)`` with ``k=60``.  Cross-source
  comparable; a record appearing in 2 sources ranks above one in 1.
  Matches LangChain ``EnsembleRetriever`` and Perplexity stage-1 fusion.

Per-domain defaults match the project's ``agent-harness/domain-profiles.json``
keys plus two synthetic profiles (``ai_progress``, ``default``) that
roll up the most common cross-domain queries.

Concurrency: source calls fan out with ``ThreadPoolExecutor`` (stdlib,
no async required since each connector's HTTP path is blocking
``urllib``).  3-5× wall-clock reduction on multi-source domains.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, TYPE_CHECKING

from .base import RetrievalError, RetrievalRecord, RetrievalSource

if TYPE_CHECKING:
    from .cache import TTLCache


RRF_K = 60                    # Cormack 2009 + LangChain EnsembleRetriever default


# Per-domain source list — order = cascade order.  Sources missing from
# the registered set land in ``CascadeResult.errors`` so the caller can
# see what wasn't tried.
DEFAULT_DOMAIN_CASCADES: dict[str, list[str]] = {
    # The harness's 8 canonical domains — v0.10 expanded source maps per
    # the SOTA scan (Anthropic financial-services lift, ACLED, HF Daily
    # Papers, defuddle-pattern Tier 0/1 sources).  Sources requiring auth
    # are listed; the cascade fail-soft-skips when their env vars unset.
    "engineering": [
        "brave_search", "crossref", "openalex", "arxiv", "wikidata", "wikipedia",
    ],
    "research": [
        "crossref", "openalex", "semantic_scholar", "arxiv", "wikidata", "wikipedia",
    ],
    "photography":           ["unsplash", "pexels", "wikipedia"],
    "fashion":               ["wikipedia"],            # snapshot-only via vault
    "chat_relationships":    [],                       # purely reactive
    "finance": [
        "edgar", "fred", "crossref", "wikidata", "openalex", "wikipedia",
    ],
    "policy":                [
        "federal_register", "regulations_gov", "congress_gov",
        "brave_search", "gdelt", "wikidata", "wikipedia",
    ],
    "international_relations": [
        "acled", "gdelt", "world_bank", "imf",
        "brave_search", "wikidata", "wikipedia",
    ],
    # Synthetic
    "ai_progress": [
        "hf_daily_papers", "arxiv", "crossref", "openalex",
        "brave_search", "wikidata", "wikipedia",
    ],
    "default": [
        "wikidata", "wikipedia", "brave_search", "crossref", "openalex", "gdelt",
    ],
    # Tier-2 social-media domains (paid/broker/pinned-fork) — opt-in via
    # `--domain` rather than appearing in `default`, so casual queries
    # don't burn budget or hit Chinese platforms unintentionally.
    "social_en":             ["x_twitter", "gdelt"],
    "social_zh":             ["xiaohongshu", "wechat_mp"],
}


FusionMode = Literal["rrf", "concat"]
Grader = Callable[[str, RetrievalRecord], str]    # returns "correct"|"ambiguous"|"incorrect"


@dataclass(slots=True)
class CascadeResult:
    """Combined cascade output — records + per-source diagnostics."""

    query: str
    domain: str
    records: list[RetrievalRecord] = field(default_factory=list)
    sources_tried: list[str] = field(default_factory=list)
    sources_succeeded: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    fusion: str = "concat"
    graded_dropped: int = 0                   # how many records the grader removed

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "domain": self.domain,
            "fusion": self.fusion,
            "count": len(self.records),
            "records": [r.to_dict() for r in self.records],
            "sources_tried": self.sources_tried,
            "sources_succeeded": self.sources_succeeded,
            "graded_dropped": self.graded_dropped,
            "errors": self.errors,
        }


class Cascade:
    """Routes a query through a domain-specific stack of sources."""

    def __init__(
        self,
        sources: dict[str, RetrievalSource],
        *,
        cascades: dict[str, list[str]] | None = None,
        cache: "TTLCache | None" = None,
    ) -> None:
        self.sources = dict(sources)
        self.cascades = dict(cascades) if cascades is not None else dict(DEFAULT_DOMAIN_CASCADES)
        self.cache = cache

    def register(self, source: RetrievalSource) -> None:
        self.sources[source.name] = source

    def cascade_for(self, domain: str) -> list[str]:
        return self.cascades.get(domain, self.cascades.get("default", []))

    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        *,
        domain: str = "default",
        per_source_limit: int = 5,
        total_limit: int = 20,
        sources: list[str] | None = None,
        fusion: FusionMode = "concat",
        timeout: float = 15.0,
        grader: Grader | None = None,
    ) -> CascadeResult:
        """Run the cascade for ``domain`` (or an explicit ``sources`` list).

        Calls every adapter in parallel with a ``ThreadPoolExecutor`` and
        a hard ``timeout`` wall-clock cap.  Per-source failures are caught
        and recorded in ``errors``.  Final ordering follows ``fusion``:

        * ``"concat"`` keeps cascade order (legacy, deterministic)
        * ``"rrf"`` uses Reciprocal Rank Fusion across sources

        ``grader`` (CRAG-style, optional) is called on every surviving
        record post-fusion; records graded ``"incorrect"`` are dropped.
        Default ``None`` preserves the v0.9 part 1 behaviour.
        """

        plan = sources if sources is not None else self.cascade_for(domain)
        result = CascadeResult(query=query, domain=domain, fusion=fusion)

        per_source_records: dict[str, list[RetrievalRecord]] = {}

        # Note unknown sources up front so the diagnostic surface is complete
        # even when nothing runs.
        runnable: list[tuple[str, RetrievalSource]] = []
        for source_name in plan:
            adapter = self.sources.get(source_name)
            if adapter is None:
                result.errors.append({
                    "source": source_name,
                    "error": "source not registered",
                })
                continue
            runnable.append((source_name, adapter))
            result.sources_tried.append(source_name)

        if not runnable:
            return result

        # Cache hits short-circuit the source call entirely; only sources
        # without a fresh cache entry are dispatched to the pool.
        deferred: list[tuple[str, RetrievalSource]] = []
        cache_hits = 0
        for name, adapter in runnable:
            cached = self.cache.get(name, query, domain) if self.cache else None
            if cached is not None:
                per_source_records[name] = cached
                result.sources_succeeded.append(name)
                cache_hits += 1
            else:
                deferred.append((name, adapter))
        if cache_hits:
            result.errors.append({
                "source": "_cache",
                "info": f"{cache_hits} cache hit(s); skipped network",
            })

        # Parallel fan-out.  Each future returns either records or an Exception
        # (we capture it in the result, never re-raise from the worker thread).
        if not deferred:
            future_to_name: dict[Future[list[RetrievalRecord]], str] = {}
            completed: list[Future[list[RetrievalRecord]]] = []
        else:
            pool = ThreadPoolExecutor(max_workers=max(len(deferred), 1))
            future_to_name = {
                pool.submit(self._call_one, adapter, query, per_source_limit, domain): name
                for name, adapter in deferred
            }
            try:
                completed = list(as_completed(future_to_name, timeout=timeout))
            except TimeoutError:
                # Pick up whatever finished; the rest get a timeout error
                completed = [f for f in future_to_name if f.done()]
                for f, name in future_to_name.items():
                    if not f.done():
                        result.errors.append({
                            "source": name,
                            "error": f"timed out after {timeout}s",
                        })
                        f.cancel()
            finally:
                pool.shutdown(wait=False)

            for future in completed:
                name = future_to_name[future]
                try:
                    records = future.result()
                except RetrievalError as exc:
                    result.errors.append({"source": name, "error": str(exc)})
                    continue
                except Exception as exc:                  # noqa: BLE001
                    result.errors.append({
                        "source": name,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    continue
                for rec in records:
                    if not rec.domain:
                        rec.domain = domain
                per_source_records[name] = records
                result.sources_succeeded.append(name)
                if self.cache:
                    try:
                        self.cache.put(name, query, domain, records)
                    except Exception:                     # noqa: BLE001
                        # cache write errors must never break retrieval
                        pass

        # Preserve plan order in sources_tried so RRF respects it as a tiebreak.
        ordered_records_lists = [
            per_source_records.get(name, [])
            for name in result.sources_tried
            if name in per_source_records
        ]

        if fusion == "rrf":
            fused = reciprocal_rank_fusion(ordered_records_lists)
        else:
            fused = []
            for lst in ordered_records_lists:
                fused.extend(lst)

        fused = dedup_records(fused)

        if grader is not None:
            kept: list[RetrievalRecord] = []
            for rec in fused:
                try:
                    verdict = grader(query, rec)
                except Exception:                          # noqa: BLE001
                    verdict = "ambiguous"
                if verdict == "incorrect":
                    result.graded_dropped += 1
                    continue
                kept.append(rec)
            fused = kept

        if len(fused) > total_limit:
            fused = fused[:total_limit]
        result.records = fused
        # Stable id for citation rendering (R1, R2, …)
        for idx, rec in enumerate(result.records, start=1):
            rec.cite_id = f"R{idx}"
        return result

    # ------------------------------------------------------------------

    @staticmethod
    def _call_one(
        adapter: RetrievalSource,
        query: str,
        per_source_limit: int,
        domain: str,
    ) -> list[RetrievalRecord]:
        return list(adapter.retrieve(query, limit=per_source_limit, domain=domain))


# ---------------------------------------------------------------------------
# Fusion + dedup helpers
# ---------------------------------------------------------------------------


def reciprocal_rank_fusion(
    record_lists: Iterable[list[RetrievalRecord]],
    *,
    k: int = RRF_K,
) -> list[RetrievalRecord]:
    """RRF: score(rec) = Σ_lists 1 / (k + rank_in_list).

    Records appearing in multiple sources surface to the top.  Identity
    for the score sum is by ``canonical_id`` if set, else ``url``, else
    ``(source, title)``.  Ties broken by first-appearance order so output
    is deterministic.
    """

    rrf_scores: dict[str, float] = {}
    representative: dict[str, RetrievalRecord] = {}
    first_seen: dict[str, int] = {}
    seen_counter = 0

    for lst in record_lists:
        for rank, rec in enumerate(lst, start=1):
            key = _identity_key(rec)
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (k + rank)
            if key not in representative:
                representative[key] = rec
                first_seen[key] = seen_counter
                seen_counter += 1

    # Surface the RRF score on the record for downstream callers.
    out: list[RetrievalRecord] = []
    for key, rec in representative.items():
        rec.score = rrf_scores[key]
        out.append(rec)

    out.sort(key=lambda r: (-rrf_scores[_identity_key(r)], first_seen[_identity_key(r)]))
    return out


def dedup_records(records: Iterable[RetrievalRecord]) -> list[RetrievalRecord]:
    """Dedup preferring canonical_id, falling back to URL.

    Earlier occurrences win — caller can sort first if rank matters.
    A blank canonical_id and blank URL together → never dedup'd
    (treated as unique).
    """

    seen: set[str] = set()
    out: list[RetrievalRecord] = []
    for rec in records:
        # canonical id is the strong key when present
        if rec.canonical_id:
            cid = f"cid::{rec.canonical_id}"
            if cid in seen:
                continue
            seen.add(cid)
        if rec.url:
            uid = f"url::{rec.url}"
            if uid in seen:
                continue
            seen.add(uid)
        out.append(rec)
    return out


def _identity_key(rec: RetrievalRecord) -> str:
    if rec.canonical_id:
        return f"cid::{rec.canonical_id}"
    if rec.url:
        return f"url::{rec.url}"
    return f"st::{rec.source}::{rec.title}"
