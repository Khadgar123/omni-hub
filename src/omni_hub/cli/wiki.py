"""Karpathy-style wiki / context-pack commands."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser("wiki-init")
    subparsers.add_parser("wiki-status")

    search = subparsers.add_parser("wiki-search")
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--include-closed", action="store_true",
                         help="Include pages with review_state=rejected/superseded "
                              "or t_valid_to in the past (default: filter them out).")
    search.add_argument("--backend", default="auto",
                         choices=["auto", "fts5", "substring"],
                         help="Search backend: auto (default), fts5 (force), substring (legacy)")

    reindex = subparsers.add_parser(
        "wiki-reindex",
        help="Drop + rebuild the FTS5 sidecar from every page under vault/wiki/.",
    )
    reindex.add_argument("--force", action="store_true",
                          help="No-op for now; reserved for future incremental modes")

    render = subparsers.add_parser(
        "wiki-render",
        help=(
            "Rebuild synthesis pages AS PROJECTIONS of claims (WS1: claims are "
            "the single source of truth).  Pages are byte-identical rebuilds, so "
            "vault/wiki/syntheses is disposable."
        ),
    )
    render.add_argument("--path", default="",
                         help="Rebuild a single page (vault/wiki/syntheses/<slug>.md); "
                              "omit to rebuild all synthesis pages")

    subparsers.add_parser(
        "wiki-doctor",
        help=(
            "One-stop integrity probe: layout / 12 domain schemas / FTS5 freshness "
            "/ claims.jsonl validity / supersede graph (cycles + dangling) / index.md "
            "dead links / orphan SKILL.md (skill registry sync)."
        ),
    )

    graph = subparsers.add_parser(
        "wiki-graph",
        help=(
            "Query the GraphRAG-style projection (v0.18-J).  --node returns "
            "co-cited neighbours (local mode);  --community returns a "
            "Leiden-style community summary (global mode)."
        ),
    )
    graph.add_argument("--node", default="",
                        help="Node id (canonical_id or entity slug)")
    graph.add_argument("--community", default="",
                        help="Community id (returned by --node lookup)")
    graph.add_argument("--limit", type=int, default=20)

    propose = subparsers.add_parser("wiki-propose-research")
    propose.add_argument("--source", required=True, choices=["researchflow", "paperbite"])
    propose.add_argument("--path", required=True)
    propose.add_argument("--domain", default="research")

    apply = subparsers.add_parser("wiki-apply-proposal")
    apply.add_argument("--proposal", required=True)
    apply.add_argument("--preview", action="store_true",
                       help="Plan-only: return typed ProjectionDiff, write nothing (Pulumi/Terraform pattern)")

    ingest = subparsers.add_parser(
        "wiki-ingest",
        help="Bridge a retrieval cascade run into a wiki_update Proposal (Karpathy Ingest).",
    )
    ingest.add_argument("--run-id", required=True,
                         help=".omni/retrieval/<run_id> evidence directory")
    ingest.add_argument("--domain", default="",
                         help="Override the cascade domain (defaults to manifest.domain)")
    ingest.add_argument("--title", default="",
                         help="Override page title (defaults to manifest.query)")
    ingest.add_argument("--max-records", type=int, default=20)

    log = subparsers.add_parser(
        "wiki-log",
        help="Append a manual audit entry to vault/wiki/log.md.",
    )
    log.add_argument("--op", required=True,
                      choices=["ingest", "apply", "lint", "supersede",
                                "conflict-resolve", "manual"])
    log.add_argument("--summary", required=True)
    log.add_argument("--source", default="")

    dream = subparsers.add_parser(
        "wiki-dream",
        help=(
            "Offline consolidation pass — local-first dual of Anthropic Dreaming. "
            "Scans recent retrieval evidence + raw + claims and proposes consolidations."
        ),
    )
    dream.add_argument("--since-days", type=int, default=7,
                        help="Window to scan (0 = full history; default = 7)")
    dream.add_argument("--rule", action="append", default=None,
                        choices=["cluster_canonical", "statement_cluster",
                                 "raw_orphan", "stale_active"])
    dream.add_argument("--persist", action="store_true",
                        help="Write each finding as Proposal(kind=wiki_dream)")
    dream.add_argument("--no-state-update", action="store_true",
                        help="Don't advance .omni/wiki_dream_state.json (for dry-run audits)")

    lint = subparsers.add_parser(
        "wiki-lint",
        help="Run the six Karpathy wiki-lint rules and emit lint_finding Proposals.",
    )
    lint.add_argument("--domain", default="",
                       help="Pin to a single domain folder; default = all pages")
    lint.add_argument("--rule", action="append", default=None,
                       choices=["contradiction", "stale_fact", "orphan_page",
                                "missing_concept", "broken_cross_ref", "data_gap",
                                "cross_ref_asymmetry", "abandoned_page"],
                       help="Restrict to specific rule(s); default = all eight (v0.17 added cross_ref_asymmetry + abandoned_page)")
    lint.add_argument("--stale-after-days", type=int, default=30)
    lint.add_argument("--persist", action="store_true",
                       help="Write each finding as Proposal(kind=lint_finding)")

    supersede = subparsers.add_parser(
        "wiki-supersede",
        help="Close an old claim's t_valid_to window and link the new claim's supersedes chain.",
    )
    supersede.add_argument("--new", dest="new_claim_id", required=True)
    supersede.add_argument("--old", dest="old_claim_id", required=True)
    supersede.add_argument("--reason", default="")
    supersede.add_argument("--expected-version", type=int, default=None,
                            help="Optimistic concurrency token (claim_ledger_version at read time)")
    supersede.add_argument("--preview", action="store_true",
                            help="Plan-only ProjectionDiff (v0.18-A)")

    resolve = subparsers.add_parser(
        "wiki-conflict-resolve",
        help="Apply a decision to a contradiction lint_finding proposal.",
    )
    resolve.add_argument("--proposal", required=True)
    resolve.add_argument("--decision", required=True,
                          choices=["keep_both", "reject_old", "reject_new", "supersede"])
    resolve.add_argument("--new", dest="new_claim_id", default="")
    resolve.add_argument("--old", dest="old_claim_id", default="")
    resolve.add_argument("--reason", default="")

    pack = subparsers.add_parser("context-pack-build")
    pack.add_argument("--query", required=True)
    pack.add_argument("--domain", default="research")
    pack.add_argument("--wiki-limit", type=int, default=6)
    pack.add_argument("--research-limit", type=int, default=6)
    pack.add_argument("--persist", action="store_true")
    pack.add_argument("--tier", default="standard",
                       choices=["minimal", "standard", "expanded"],
                       help="Progressive disclosure tier: minimal=frontmatter, "
                            "standard=+snippet, expanded=+body excerpt")
    pack.add_argument("--include-closed", action="store_true",
                       help="Include superseded/rejected wiki pages (default: filter out)")


def _init(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_init",
            action="init",
            payload={},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _status(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_status",
            action="status",
            payload={},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _search(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_search",
            action="search",
            payload={
                "query": args.query,
                "limit": args.limit,
                "include_closed": bool(args.include_closed),
                "backend": args.backend,
            },
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _reindex(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_reindex",
            action="reindex",
            payload={},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _render(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_render",
            action="render",
            payload={"path": getattr(args, "path", "")},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _graph(args, *, runner, workspace) -> int:
    payload: dict[str, object] = {"limit": args.limit}
    if args.community:
        payload["community"] = args.community
    elif args.node:
        payload["node"] = args.node
    else:
        raise SystemExit("--node or --community required")
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_graph_query", action="query",
            payload=payload, risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _doctor(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_doctor",
            action="doctor",
            payload={},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _propose_research(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_propose_research",
            action="write_proposal",
            payload={
                "source": args.source,
                "path": args.path,
                "domain": args.domain,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _apply_proposal(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_apply_proposal",
            action="apply",
            payload={"proposal": args.proposal},
            risk_level=RiskLevel.LOCAL_WRITE,
            dry_run=bool(args.preview),
        ),
    )


def _ingest(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_ingest",
            action="ingest",
            payload={
                "run_id": args.run_id,
                "domain": args.domain,
                "title": args.title,
                "max_records": args.max_records,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _log(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_log_append",
            action="append",
            payload={
                "op": args.op,
                "summary": args.summary,
                "source": args.source,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _dream(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_dream",
            action="dream",
            payload={
                "since_days": args.since_days,
                "rules": list(args.rule) if args.rule else None,
                "persist": bool(args.persist),
                "update_state": not args.no_state_update,
            },
            risk_level=RiskLevel.LOCAL_WRITE if args.persist else RiskLevel.READ_ONLY,
        ),
    )


def _lint(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_lint",
            action="lint",
            payload={
                "domain": args.domain,
                "rules": list(args.rule) if args.rule else None,
                "stale_after_days": args.stale_after_days,
                "persist": bool(args.persist),
            },
            risk_level=RiskLevel.LOCAL_WRITE if args.persist else RiskLevel.READ_ONLY,
        ),
    )


def _supersede(args, *, runner, workspace) -> int:
    payload: dict[str, object] = {
        "new_claim_id": args.new_claim_id,
        "old_claim_id": args.old_claim_id,
        "reason": args.reason,
    }
    if args.expected_version is not None:
        payload["expected_version"] = args.expected_version
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_supersede",
            action="supersede",
            payload=payload,
            risk_level=RiskLevel.LOCAL_WRITE,
            dry_run=bool(args.preview),
        ),
    )


def _conflict_resolve(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_conflict_resolve",
            action="resolve",
            payload={
                "proposal_id": args.proposal,
                "decision": args.decision,
                "new_claim_id": args.new_claim_id,
                "old_claim_id": args.old_claim_id,
                "reason": args.reason,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _context_pack(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="context_pack_build",
            action="build",
            payload={
                "query": args.query,
                "domain": args.domain,
                "wiki_limit": args.wiki_limit,
                "research_limit": args.research_limit,
                "persist": args.persist,
                "tier": args.tier,
                "include_closed": bool(args.include_closed),
            },
            risk_level=RiskLevel.LOCAL_WRITE if args.persist else RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "wiki-init": _init,
    "wiki-status": _status,
    "wiki-search": _search,
    "wiki-propose-research": _propose_research,
    "wiki-apply-proposal": _apply_proposal,
    "wiki-ingest": _ingest,
    "wiki-log": _log,
    "wiki-dream": _dream,
    "wiki-lint": _lint,
    "wiki-reindex": _reindex,
    "wiki-render": _render,
    "wiki-doctor": _doctor,
    "wiki-graph": _graph,
    "wiki-supersede": _supersede,
    "wiki-conflict-resolve": _conflict_resolve,
    "context-pack-build": _context_pack,
}
