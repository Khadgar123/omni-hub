"""Audited Discord probe and evidence-collection commands."""

from __future__ import annotations

import argparse

from ..connectors.discord import rfc2544_fake_ip_media_policy_descriptor
from ..discord_backtest import CURATION_MANIFEST_SHA256
from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


_DEFAULT_TOKEN_FILE = "~/.config/dce/bot-token"


def register(subparsers: argparse._SubParsersAction) -> None:
    probe = subparsers.add_parser(
        "discord-probe",
        help="Verify credential and guild/channel API visibility without retaining bodies.",
    )
    probe.add_argument("--guild-id", required=True)
    probe.add_argument("--channel-id")
    probe.add_argument("--token-file", default=_DEFAULT_TOKEN_FILE)

    collect = subparsers.add_parser(
        "discord-collect",
        help="Collect a resumable Discord evidence run inside this workspace.",
    )
    collect.add_argument("--targets", required=True)
    collect.add_argument("--output-dir", required=True)
    collect.add_argument("--run-id")
    collect.add_argument("--max-pages", type=int)
    collect.add_argument("--no-assets", action="store_true")
    collect.add_argument(
        "--allow-rfc2544-fake-ip",
        action="store_true",
        help="Allow versioned Discord-media fake-IP DNS policy for local TUNs.",
    )
    collect.add_argument("--token-file", default=_DEFAULT_TOKEN_FILE)
    collect.add_argument("--max-asset-bytes", type=int, default=512 * 1024 * 1024)
    collect.add_argument("--asset-chunk-size", type=int, default=64 * 1024)

    shard_plan = subparsers.add_parser(
        "discord-shard-plan",
        help="Write an auditable parent-family Discord shard plan without API access.",
    )
    shard_plan.add_argument("--targets", required=True)
    shard_plan.add_argument("--output-dir", required=True)
    shard_plan.add_argument("--shard-count", type=int, default=4)
    shard_plan.add_argument("--weights")

    merge_audit = subparsers.add_parser(
        "discord-shard-merge-audit",
        help="Verify pinned shard runs and write a logical merge audit without copying evidence.",
    )
    merge_audit.add_argument("--targets", required=True)
    merge_audit.add_argument("--plan", required=True)
    merge_audit.add_argument("--merge-request", required=True)
    merge_audit.add_argument("--output", required=True)

    closure_capture = subparsers.add_parser(
        "discord-shard-closure-capture",
        help="Capture a hash-pinned thread census and common-H message delta.",
    )
    closure_capture.add_argument("--targets", required=True)
    closure_capture.add_argument("--merge-audit", required=True)
    closure_capture.add_argument("--output-dir", required=True)
    closure_capture.add_argument("--t-close", required=True)
    closure_capture.add_argument("--t-close-source-sha256", required=True)
    closure_capture.add_argument("--token-file", default=_DEFAULT_TOKEN_FILE)

    closure_audit = subparsers.add_parser(
        "discord-shard-closure-audit",
        help="Audit a T_close thread census and per-target reverse catch-up.",
    )
    closure_audit.add_argument("--merge-audit", required=True)
    closure_audit.add_argument("--census", required=True)
    closure_audit.add_argument("--head-catchup", required=True)
    closure_audit.add_argument("--t-close", required=True)
    closure_audit.add_argument("--output", required=True)

    blogger_events = subparsers.add_parser(
        "discord-blogger-events-build",
        help="Build redacted blogger trade-event and latest-call derivatives from verified evidence.",
    )
    blogger_events.add_argument("--export-root", required=True)
    blogger_events.add_argument("--closure-audit", required=True)
    blogger_events.add_argument("--output-dir", required=True)
    blogger_events.add_argument("--asof", required=True, help="UTC report cutoff.")

    blogger_inventory = subparsers.add_parser(
        "discord-blogger-inventory-build",
        help="Build a redacted exact-target and parent-family inventory from verified evidence.",
    )
    blogger_inventory.add_argument("--export-root", required=True)
    blogger_inventory.add_argument("--closure-audit", required=True)
    blogger_inventory.add_argument("--targets", required=True)
    blogger_inventory.add_argument("--output", required=True)

    identity_review_freeze = subparsers.add_parser(
        "discord-blogger-identity-review-freeze",
        help="Freeze a complete hash-bound private blogger identity review pack.",
    )
    identity_review_freeze.add_argument("--candidate-pack", required=True)
    identity_review_freeze.add_argument("--reviewed-labels", required=True)
    identity_review_freeze.add_argument("--output", required=True)

    blogger_backtest = subparsers.add_parser(
        "discord-blogger-backtest-run",
        help="Run and atomically publish a hash-bound conservative 1m blogger backtest.",
    )
    blogger_backtest.add_argument("--curation-manifest", required=True)
    blogger_backtest.add_argument(
        "--curation-manifest-sha256", default=CURATION_MANIFEST_SHA256,
        help="Expected SHA-256 commitment (defaults to the reviewed formal curation).",
    )
    blogger_backtest.add_argument("--market-root", required=True)
    blogger_backtest.add_argument("--output-dir", required=True)
    blogger_backtest.add_argument("--fee-bps", type=float, default=4.0)
    blogger_backtest.add_argument("--slippage-bps", type=float, default=4.0)
    blogger_backtest.add_argument("--max-entry-wait-minutes", type=int, default=1440)
    blogger_backtest.add_argument("--timeout-seconds", type=int, default=300)


def _probe(args, *, runner, workspace) -> int:
    del workspace
    payload: dict[str, object] = {
        "guild_id": args.guild_id,
        "token_file": args.token_file,
    }
    if args.channel_id is not None:
        payload["channel_id"] = args.channel_id
    return run_and_print(
        runner,
        OperationSpec(
            name="discord_probe",
            action="probe",
            connector="discord",
            payload=payload,
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _collect(args, *, runner, workspace) -> int:
    del workspace
    fake_ip_policy = (
        rfc2544_fake_ip_media_policy_descriptor()
        if args.allow_rfc2544_fake_ip
        else None
    )
    return run_and_print(
        runner,
        OperationSpec(
            name="discord_collect",
            action="collect",
            connector="discord",
            payload={
                "targets": args.targets,
                "output_dir": args.output_dir,
                "run_id": args.run_id,
                "max_pages": args.max_pages,
                "download_assets": not args.no_assets,
                "allow_rfc2544_fake_ip": args.allow_rfc2544_fake_ip,
                "rfc2544_fake_ip_policy": fake_ip_policy,
                "token_file": args.token_file,
                "max_asset_bytes": args.max_asset_bytes,
                "chunk_size": args.asset_chunk_size,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _shard_plan(args, *, runner, workspace) -> int:
    del workspace
    return run_and_print(
        runner,
        OperationSpec(
            name="discord_shard_plan",
            action="plan_parent_families",
            connector="discord",
            payload={
                "targets": args.targets,
                "output_dir": args.output_dir,
                "shard_count": args.shard_count,
                "weights": args.weights,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _shard_merge_audit(args, *, runner, workspace) -> int:
    del workspace
    return run_and_print(
        runner,
        OperationSpec(
            name="discord_shard_merge_audit",
            action="audit_logical_merge",
            connector="discord",
            payload={
                "targets": args.targets,
                "plan": args.plan,
                "merge_request": args.merge_request,
                "output": args.output,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _shard_closure_audit(args, *, runner, workspace) -> int:
    del workspace
    return run_and_print(
        runner,
        OperationSpec(
            name="discord_shard_closure_audit",
            action="audit_t_close_closure",
            connector="discord",
            payload={
                "merge_audit": args.merge_audit,
                "census": args.census,
                "head_catchup": args.head_catchup,
                "t_close": args.t_close,
                "output": args.output,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _shard_closure_capture(args, *, runner, workspace) -> int:
    del workspace
    return run_and_print(
        runner,
        OperationSpec(
            name="discord_shard_closure_capture",
            action="capture_t_close_closure",
            connector="discord",
            payload={
                "targets": args.targets,
                "merge_audit": args.merge_audit,
                "output_dir": args.output_dir,
                "t_close": args.t_close,
                "t_close_source_sha256": args.t_close_source_sha256,
                "token_file": args.token_file,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _blogger_events_build(args, *, runner, workspace) -> int:
    del workspace
    return run_and_print(
        runner,
        OperationSpec(
            name="discord_blogger_events_build",
            action="build_blogger_events",
            connector="discord",
            payload={
                "export_root": args.export_root,
                "closure_audit": args.closure_audit,
                "output_dir": args.output_dir,
                "asof": args.asof,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _blogger_inventory_build(args, *, runner, workspace) -> int:
    del workspace
    return run_and_print(
        runner,
        OperationSpec(
            name="discord_blogger_inventory_build",
            action="build_blogger_target_inventory",
            connector="discord",
            payload={
                "export_root": args.export_root,
                "closure_audit": args.closure_audit,
                "targets": args.targets,
                "output": args.output,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _blogger_identity_review_freeze(args, *, runner, workspace) -> int:
    del workspace
    return run_and_print(
        runner,
        OperationSpec(
            name="discord_blogger_identity_review_freeze",
            action="freeze_blogger_identity_review",
            connector="discord",
            payload={
                "candidate_pack": args.candidate_pack,
                "reviewed_labels": args.reviewed_labels,
                "output": args.output,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _blogger_backtest_run(args, *, runner, workspace) -> int:
    del workspace
    return run_and_print(
        runner,
        OperationSpec(
            name="discord_blogger_backtest_run",
            action="run_blogger_backtest",
            connector="discord",
            payload={
                "curation_manifest": args.curation_manifest,
                "curation_manifest_sha256": args.curation_manifest_sha256,
                "market_root": args.market_root,
                "output_dir": args.output_dir,
                "fee_bps": args.fee_bps,
                "slippage_bps": args.slippage_bps,
                "max_entry_wait_minutes": args.max_entry_wait_minutes,
                "timeout_seconds": args.timeout_seconds,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


COMMANDS = {
    "discord-probe": _probe,
    "discord-collect": _collect,
    "discord-shard-plan": _shard_plan,
    "discord-shard-merge-audit": _shard_merge_audit,
    "discord-shard-closure-capture": _shard_closure_capture,
    "discord-shard-closure-audit": _shard_closure_audit,
    "discord-blogger-events-build": _blogger_events_build,
    "discord-blogger-inventory-build": _blogger_inventory_build,
    "discord-blogger-identity-review-freeze": (
        _blogger_identity_review_freeze
    ),
    "discord-blogger-backtest-run": _blogger_backtest_run,
}
