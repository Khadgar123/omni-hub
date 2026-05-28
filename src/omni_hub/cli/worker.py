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

from ..proposals import Proposal, ProposalStore
from ..queue import LeaseLost, TaskQueue
from ..workers import (
    Artifact,
    ClaudeAdapter,
    CodexAdapter,
    WorkerAdapter,
)
from ..workers.builtin import make_builtin_adapter
from ._common import print_json


# Lanes whose successful artifacts must land as a pending Proposal[T]
# before the task is marked done.  Python (builtin) lane is exempt because
# its work is already an audited OperationRunner write.
_GATED_LANES = {"claude", "codex", "openhands"}


def _artifact_to_proposal(artifact: Artifact, *, lane: str) -> Proposal:
    raw = artifact.data if isinstance(artifact.data, dict) else {}
    text = str(raw.get("text", "") or "")
    title = (text.splitlines()[0][:80] if text else f"{lane} generation")
    summary = text[:280]
    return Proposal(
        kind="generation",
        title=title,
        summary=summary,
        confidence=0.5,
        suggested_action="review_generation",
        source_task_id=str(artifact.task_id) if artifact.task_id else None,
        payload={
            "artifact_id": artifact.artifact_id,
            "text": text,
            "model": raw.get("model"),
            "session_id": raw.get("session_id"),
            "tokens_in": artifact.tokens_in,
            "tokens_out": artifact.tokens_out,
            "cost_usd": artifact.cost_usd,
            "duration_ms": artifact.duration_ms,
            "worker_lane": artifact.worker_lane,
            "worker_id": artifact.worker_id,
        },
    )


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
    proposal_store = ProposalStore(workspace)
    adapter = _make_adapter(args.lane, workspace, worker_id)
    gated = args.lane in _GATED_LANES

    processed = 0
    proposals_made = 0
    lease_losses = 0
    last_task_at = time.monotonic()

    def _safe_fail(task_id: int, error: str, lease_epoch: int) -> None:
        nonlocal lease_losses
        try:
            queue.fail(
                task_id, error=error,
                claimed_by=worker_id, lease_epoch=lease_epoch,
            )
        except LeaseLost:
            lease_losses += 1

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
        current_epoch = task.lease_epoch       # snapshot fencing token
        try:
            artifact: Artifact = adapter.run(task, timeout_sec=args.task_timeout_sec)
        except Exception as exc:                            # noqa: BLE001
            _safe_fail(task.id, f"{type(exc).__name__}: {exc}", current_epoch)
            processed += 1
            continue

        proposal: Proposal | None = None
        try:
            if artifact.error:
                _safe_fail(task.id, artifact.error, current_epoch)
            else:
                output = artifact.to_dict()
                output["lease_epoch"] = current_epoch
                output["fencing_suffix"] = task.fencing_suffix()
                # Gated lanes (agent workers) write a pending Proposal[T]
                # that the human must approve via propose-approve.  The
                # proposal_id is surfaced in the task output so callers
                # can link back.
                if gated:
                    proposal = _artifact_to_proposal(artifact, lane=args.lane)
                    proposal_store.store(proposal, write_card=False)
                    output["proposal_id"] = proposal.proposal_id
                    output["proposal_state"] = proposal.state
                queue.complete(
                    task.id, output=output,
                    claimed_by=worker_id, lease_epoch=current_epoch,
                )
                if proposal is not None:
                    proposals_made += 1
        except LeaseLost:
            # Another worker reclaimed this task while we were running it.
            # Drop the result — the new holder will produce its own.  If
            # we already staged a gated proposal, close it so stale output
            # never appears as a pending human decision.
            if proposal is not None:
                proposal_store.reject(
                    proposal.proposal_id,
                    reason="lease_lost",
                    decided_by=worker_id,
                )
            lease_losses += 1
        processed += 1

        if args.max_iterations and processed >= args.max_iterations:
            break

    print_json({
        "worker_id": worker_id,
        "lane": args.lane,
        "processed": processed,
        "proposals_made": proposals_made,
        "lease_losses": lease_losses,
        "counts_by_state": queue.counts_by_state(),
    })
    return 0


COMMANDS = {"worker": _handle_worker}
