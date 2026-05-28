"""Argilla bridge commands for Proposal review datasets."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import print_json, run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    schema = subparsers.add_parser(
        "argilla-schema",
        help="Print the versioned Argilla dataset settings contract.",
    )
    schema.add_argument("--dataset", default="omni_proposal_review_v1")

    export = subparsers.add_parser(
        "argilla-export-proposals",
        help="Export ProposalStore rows as Argilla-ready JSONL records.",
    )
    export.add_argument("--output", required=True)
    export.add_argument(
        "--state", default="pending",
        choices=["pending", "approved", "rejected", "all"],
        help="Filter by proposal state; 'all' includes every state.",
    )
    export.add_argument("--kind")
    export.add_argument("--limit", type=int, default=100)
    export.add_argument("--dataset", default="omni_proposal_review_v1")
    export.add_argument("--domain", default="general")
    export.add_argument("--skill-id", default="")
    export.add_argument("--skill-version", default="v0")
    export.add_argument(
        "--since-days", type=int, default=0,
        help="Restrict to proposals created within the last N days (0 = no filter).",
    )

    sync = subparsers.add_parser(
        "argilla-sync-feedback",
        help="Sync reviewed Argilla JSONL records back into Proposal + preference stores.",
    )
    sync.add_argument("--input", required=True)
    sync.add_argument("--preference-root", default=".omni/preference")
    sync.add_argument("--domain", default="general")


def _schema(args, *, runner, workspace) -> int:
    from ..harness.argilla_bridge import build_dataset_settings

    print_json(build_dataset_settings(args.dataset))
    return 0


def _export(args, *, runner, workspace) -> int:
    # state="all" => don't forward a state filter (ProposalStore.list treats
    # state=None as "any state").
    payload = {
        "output": args.output,
        "kind": args.kind,
        "limit": args.limit,
        "dataset": args.dataset,
        "domain": args.domain,
        "skill_id": args.skill_id,
        "skill_version": args.skill_version,
        "since_days": args.since_days,
    }
    if args.state != "all":
        payload["state"] = args.state
    return run_and_print(
        runner,
        OperationSpec(
            name="argilla_export_proposals",
            action="export",
            payload=payload,
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _sync(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="argilla_sync_feedback",
            action="sync",
            payload={
                "input": args.input,
                "preference_root": args.preference_root,
                "domain": args.domain,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


COMMANDS = {
    "argilla-schema": _schema,
    "argilla-export-proposals": _export,
    "argilla-sync-feedback": _sync,
}
