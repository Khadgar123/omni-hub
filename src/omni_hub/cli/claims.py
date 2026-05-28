"""claims-* commands — read-side view over .omni/claims.jsonl."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    listing = subparsers.add_parser(
        "claims-list",
        help="List atomic claims from .omni/claims.jsonl (default: open only).",
    )
    listing.add_argument("--state", default="",
                          choices=["", "approved", "proposed", "conflict",
                                   "superseded", "rejected", "unknown"],
                          help="Filter by review_state")
    listing.add_argument("--domain", default="",
                          help="Filter by domain field")
    listing.add_argument("--include-closed", action="store_true",
                          help="Include claims with t_valid_to set or state ∈ rejected/superseded")
    listing.add_argument("--limit", type=int, default=50)

    show = subparsers.add_parser(
        "claims-show",
        help="Show a claim plus its supersession chain (oldest → newest).",
    )
    show.add_argument("--id", dest="claim_id", required=True)

    subparsers.add_parser(
        "claims-stats",
        help="Aggregate counts: total / open / closed, by_state, by_domain.",
    )


def _list(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="claims_list",
            action="list",
            payload={
                "state": args.state,
                "domain": args.domain,
                "include_closed": bool(args.include_closed),
                "limit": args.limit,
            },
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _show(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="claims_show",
            action="show",
            payload={"claim_id": args.claim_id},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _stats(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="claims_stats",
            action="stats",
            payload={},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "claims-list": _list,
    "claims-show": _show,
    "claims-stats": _stats,
}
