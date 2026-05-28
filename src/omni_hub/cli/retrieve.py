"""``retrieve`` and ``fetch-url`` commands — Retrieval Plane CLI surface.

* ``retrieve --query Q --domain D``: federated cascade across the free
  vertical KBs (OpenAlex / Semantic Scholar / arXiv / Wikipedia / GDELT)
  plus Jina Reader for URL queries.  Domain-aware via DEFAULT_DOMAIN_CASCADES.

* ``fetch-url --url U``: single URL → markdown via Jina Reader, falling
  back to the existing ``capture_url`` urllib path if Jina fails or is
  unavailable.  Use this for forwarded links (SPA-heavy targets that
  pre-hydration urllib can't read).
"""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    retrieve = subparsers.add_parser(
        "retrieve",
        help="Federated retrieval across OpenAlex / Wikipedia / arXiv / GDELT etc.",
    )
    retrieve.add_argument("--query", required=True)
    retrieve.add_argument(
        "--domain", default="default",
        help=(
            "Domain profile (engineering | research | photography | fashion | "
            "chat_relationships | finance | policy | international_relations | "
            "ai_progress | default)"
        ),
    )
    retrieve.add_argument(
        "--sources",
        help="Comma-separated source override (skip cascade defaults).",
    )
    retrieve.add_argument("--per-source-limit", type=int, default=5)
    retrieve.add_argument("--total-limit", type=int, default=20)
    retrieve.add_argument(
        "--fusion", choices=["concat", "rrf"], default="concat",
        help=(
            "Cross-source ranking. ``rrf`` = Reciprocal Rank Fusion; "
            "``concat`` = cascade-order (legacy default)."
        ),
    )
    retrieve.add_argument(
        "--grader", choices=["heuristic"], default=None,
        help=(
            "CRAG-style grader applied post-fusion. ``heuristic`` drops "
            "obvious junk (paywall stubs, 404 pages, zero-overlap)."
        ),
    )
    retrieve.add_argument(
        "--cache", action="store_true",
        help="Use the SQLite TTL cache under .omni/retrieval_cache.sqlite3.",
    )
    retrieve.add_argument(
        "--persist-evidence", action="store_true",
        help=(
            "Write evidence.jsonl + sources.json + run_manifest.json under "
            ".omni/retrieval/<run_id>/ for replay/HITL review."
        ),
    )
    retrieve.add_argument(
        "--run-id", default="",
        help="Pin a custom run_id (default: timestamp + hex) — useful for "
             "linking evidence to a queue task.",
    )
    retrieve.add_argument(
        "--reranker", choices=["none", "cohere", "voyage"], default="none",
        help=(
            "Optional cross-encoder rerank tail applied after fusion + grader. "
            "Voyage rerank-2.5 (VOYAGE_API_KEY) is Anthropic's 2026 recommendation; "
            "Cohere Rerank 4 (COHERE_API_KEY) the previous default."
        ),
    )

    fetch = subparsers.add_parser(
        "fetch-url",
        help="Fetch a single URL via Jina Reader (JS-rendered markdown).",
    )
    fetch.add_argument("--url", required=True)
    fetch.add_argument(
        "--no-reader", action="store_true",
        help="Skip Jina Reader; use only the urllib path (capture-url behaviour).",
    )
    fetch.add_argument(
        "--use-trafilatura", action="store_true",
        help=(
            "Also run trafilatura over the fetched HTML for boilerplate-"
            "stripped extraction. Requires trafilatura on PATH (see "
            "src/omni_hub/connectors/trafilatura_bridge.py for install)."
        ),
    )

    subparsers.add_parser(
        "retrieve-doctor",
        help="Probe every registered retrieval source's health (Agent-Reach pattern).",
    )


def _retrieve(args, *, runner, workspace) -> int:
    payload: dict[str, object] = {
        "query": args.query,
        "domain": args.domain,
        "per_source_limit": args.per_source_limit,
        "total_limit": args.total_limit,
        "fusion": args.fusion,
        "use_cache": bool(args.cache),
        "persist_evidence": bool(args.persist_evidence),
    }
    if args.sources:
        payload["sources"] = [s.strip() for s in args.sources.split(",") if s.strip()]
    if args.grader:
        payload["grader"] = args.grader
    if args.reranker and args.reranker != "none":
        payload["reranker"] = args.reranker
    if args.run_id:
        payload["run_id"] = args.run_id
    return run_and_print(
        runner,
        OperationSpec(
            name="retrieve_cascade",
            action="retrieve",
            payload=payload,
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _fetch(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="fetch_url_reader",
            action="fetch",
            payload={
                "url": args.url,
                "use_reader": not args.no_reader,
                "use_trafilatura": bool(args.use_trafilatura),
            },
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _doctor(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="retrieve_doctor",
            action="health",
            payload={},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "retrieve": _retrieve,
    "fetch-url": _fetch,
    "retrieve-doctor": _doctor,
}
