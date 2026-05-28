"""task-enqueue / task-claim / task-complete / task-fail / task-list."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    enq = subparsers.add_parser(
        "task-enqueue",
        help="Enqueue a TaskPacket onto the agent job queue.",
    )
    enq.add_argument("--lane", required=True,
                     help="python | claude | codex | <future-adapter>")
    enq.add_argument("--packet-file",
                     help="JSON file with the TaskPacket payload.")
    enq.add_argument("--packet-json",
                     help="Inline JSON for the TaskPacket payload.")
    enq.add_argument("--domain-profile", default="")
    enq.add_argument("--idempotency-key",
                     help="Stable key; second enqueue with same key returns existing task.")
    enq.add_argument("--max-attempts", type=int, default=3)

    claim = subparsers.add_parser(
        "task-claim",
        help="Atomically claim the next pending (or stale-claimed) task on a lane.",
    )
    claim.add_argument("--lane", required=True)
    claim.add_argument("--worker-id",
                       help="Worker identifier; defaults to a fresh UUID.")
    claim.add_argument("--visibility-timeout-sec", type=int, default=600)

    complete = subparsers.add_parser(
        "task-complete",
        help="Mark a claimed task as done; optional --output-json captures the result.",
    )
    complete.add_argument("--id", type=int, required=True)
    complete.add_argument("--output-json", default="")

    fail = subparsers.add_parser(
        "task-fail",
        help="Mark a task as failed; reschedules with backoff until max_attempts.",
    )
    fail.add_argument("--id", type=int, required=True)
    fail.add_argument("--error", required=True)
    fail.add_argument("--backoff-base-sec", type=int, default=60)
    fail.add_argument("--backoff-cap-sec", type=int, default=3600)

    listp = subparsers.add_parser(
        "task-list",
        help="List tasks (filter by state/lane).",
    )
    listp.add_argument("--state",
                       choices=["pending", "claimed", "done", "failed", "dead"])
    listp.add_argument("--lane")
    listp.add_argument("--limit", type=int, default=50)

    tick = subparsers.add_parser(
        "schedule-tick",
        help="Enqueue the canonical set of recurring tasks for a period.",
    )
    tick.add_argument("--period", required=True,
                      choices=["daily", "weekly", "monthly"])
    tick.add_argument("--anchor",
                      help="Anchor date YYYY-MM-DD; defaults to today.")

    subparsers.add_parser(
        "task-stats",
        help="Queue observability snapshot — depth per lane/state, oldest "
             "pending age, claim→done latency percentiles, attempts "
             "distribution, dead count.",
    )


def _load_packet(args) -> dict:
    if args.packet_file:
        return json.loads(Path(args.packet_file).read_text(encoding="utf-8"))
    if args.packet_json:
        return json.loads(args.packet_json)
    return {}


def _enqueue(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="enqueue_task",
            action="enqueue",
            payload={
                "lane": args.lane,
                "packet": _load_packet(args),
                "domain_profile": args.domain_profile,
                "idempotency_key": args.idempotency_key,
                "max_attempts": args.max_attempts,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _claim(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="claim_task",
            action="claim",
            payload={
                "lane": args.lane,
                "claimed_by": args.worker_id,
                "visibility_timeout_sec": args.visibility_timeout_sec,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _complete(args, *, runner, workspace) -> int:
    output = json.loads(args.output_json) if args.output_json else None
    return run_and_print(
        runner,
        OperationSpec(
            name="complete_task",
            action="complete",
            payload={"task_id": args.id, "output": output},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _fail(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="fail_task",
            action="fail",
            payload={
                "task_id": args.id,
                "error": args.error,
                "backoff_base_sec": args.backoff_base_sec,
                "backoff_cap_sec": args.backoff_cap_sec,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _list(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="list_tasks",
            action="list",
            payload={"state": args.state, "lane": args.lane, "limit": args.limit},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _schedule_tick(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="schedule_tick",
            action="tick",
            payload={"period": args.period, "anchor": args.anchor},
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _stats(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="task_stats",
            action="stats",
            payload={},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "task-enqueue": _enqueue,
    "task-claim": _claim,
    "task-complete": _complete,
    "task-fail": _fail,
    "task-list": _list,
    "task-stats": _stats,
    "schedule-tick": _schedule_tick,
}
