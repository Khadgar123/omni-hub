"""projection-* CLI (v0.18-H Projection snapshots + atomic swap)."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser(
        "projection-list",
        help="Iceberg-style overview of every projection: schema_version, "
             "current snapshot, last cursor, stats.",
    )

    rebuild = subparsers.add_parser(
        "projection-rebuild",
        help="Rebuild a projection from ClaimLedger + AuditEventLog and "
             "atomically swap its current snapshot pointer.",
    )
    rebuild.add_argument("--target", required=True,
                          help="projection name (wiki_fts5 | claims_ledger | preference_jsonl | ...)")

    snapshots = subparsers.add_parser(
        "projection-snapshots",
        help="List historic snapshots for a projection.",
    )
    snapshots.add_argument("--target", required=True)
    snapshots.add_argument("--limit", type=int, default=20)

    rollback = subparsers.add_parser(
        "projection-rollback",
        help="Swap the current snapshot pointer back to a prior snapshot_id.",
    )
    rollback.add_argument("--target", required=True)
    rollback.add_argument("--snapshot", required=True)


def _list(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="projection_list", action="list",
            payload={}, risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _rebuild(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="projection_rebuild", action="rebuild",
            payload={"target": args.target},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _snapshots(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="projection_snapshots", action="list",
            payload={"target": args.target, "limit": args.limit},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _rollback(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="projection_rollback", action="rollback",
            payload={"target": args.target, "snapshot": args.snapshot},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


COMMANDS = {
    "projection-list": _list,
    "projection-rebuild": _rebuild,
    "projection-snapshots": _snapshots,
    "projection-rollback": _rollback,
}
