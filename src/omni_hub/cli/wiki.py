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

    propose = subparsers.add_parser("wiki-propose-research")
    propose.add_argument("--source", required=True, choices=["researchflow", "paperbite"])
    propose.add_argument("--path", required=True)
    propose.add_argument("--domain", default="research")

    apply = subparsers.add_parser("wiki-apply-proposal")
    apply.add_argument("--proposal", required=True)

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

    lint = subparsers.add_parser(
        "wiki-lint",
        help="Run the six Karpathy wiki-lint rules and emit lint_finding Proposals.",
    )
    lint.add_argument("--domain", default="",
                       help="Pin to a single domain folder; default = all pages")
    lint.add_argument("--rule", action="append", default=None,
                       choices=["contradiction", "stale_fact", "orphan_page",
                                "missing_concept", "broken_cross_ref", "data_gap"],
                       help="Restrict to specific rule(s); default = all six")
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
            payload={"query": args.query, "limit": args.limit},
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
    return run_and_print(
        runner,
        OperationSpec(
            name="wiki_supersede",
            action="supersede",
            payload={
                "new_claim_id": args.new_claim_id,
                "old_claim_id": args.old_claim_id,
                "reason": args.reason,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
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
    "wiki-lint": _lint,
    "wiki-supersede": _supersede,
    "wiki-conflict-resolve": _conflict_resolve,
    "context-pack-build": _context_pack,
}
