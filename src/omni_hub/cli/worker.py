"""``omni-hub worker --lane <lane>`` — drain the queue, dispatch to an adapter.

The worker is intentionally tiny: it claims one task at a time, hands it
to the right WorkerAdapter, then calls complete/fail on the queue.
Long-running orchestration belongs in the queue table (state machine +
visibility timeout) and in the launchd plist that re-launches us, not in
this loop.
"""

from __future__ import annotations

import argparse
import time
import uuid
from pathlib import Path

from ..queue import TaskQueue
from ..workers import (
    Artifact,
    ClaudeAdapter,
    CodexAdapter,
    WorkerAdapter,
)
from ..workers.builtin import make_builtin_adapter
from ._common import print_json


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "worker",
        help="Drain the AgentJob queue on a single lane and dispatch to its adapter.",
    )
    p.add_argument(
        "--lane", required=True,
        help="python | claude | codex | <other adapter name>",
    )
    p.add_argument(
        "--worker-id",
        help="Stable worker identifier; defaults to a fresh UUID per run.",
    )
    p.add_argument(
        "--max-iterations", type=int, default=0,
        help="Stop after N tasks (default 0 = run until idle, then exit).",
    )
    p.add_argument(
        "--poll-interval-sec", type=float, default=2.0,
        help="Sleep this long when the queue is empty before polling again.",
    )
    p.add_argument(
        "--idle-exit-after-sec", type=int, default=10,
        help="Exit if the queue stays empty for this many seconds (0=loop forever).",
    )
    p.add_argument(
        "--task-timeout-sec", type=int, default=300,
        help="Per-task wall-clock cap passed to the adapter.",
    )


def _make_adapter(lane: str, workspace: Path, worker_id: str) -> WorkerAdapter:
    if lane == "python":
        return make_builtin_adapter(workspace, worker_id=worker_id)
    if lane == "claude":
        return ClaudeAdapter(cwd=workspace, worker_id=worker_id)
    if lane == "codex":
        return CodexAdapter(cwd=workspace, worker_id=worker_id)
    raise SystemExit(
        f"unknown lane: {lane!r}. Built-in lanes: python | claude | codex"
    )


def _handle_worker(args, *, runner, workspace) -> int:
    worker_id = args.worker_id or f"worker-{uuid.uuid4()}"
    queue = TaskQueue(workspace)
    adapter = _make_adapter(args.lane, workspace, worker_id)

    processed = 0
    last_task_at = time.monotonic()

    while True:
        task = queue.claim(lane=args.lane, claimed_by=worker_id)
        if task is None:
            if args.idle_exit_after_sec and (
                time.monotonic() - last_task_at > args.idle_exit_after_sec
            ):
                break
            time.sleep(args.poll_interval_sec)
            continue

        last_task_at = time.monotonic()
        try:
            artifact: Artifact = adapter.run(task, timeout_sec=args.task_timeout_sec)
        except Exception as exc:                            # noqa: BLE001
            queue.fail(task.id, error=f"{type(exc).__name__}: {exc}")
            processed += 1
            continue

        if artifact.error:
            queue.fail(task.id, error=artifact.error)
        else:
            queue.complete(task.id, output=artifact.to_dict())
        processed += 1

        if args.max_iterations and processed >= args.max_iterations:
            break

    print_json({
        "worker_id": worker_id,
        "lane": args.lane,
        "processed": processed,
        "counts_by_state": queue.counts_by_state(),
    })
    return 0


COMMANDS = {"worker": _handle_worker}
