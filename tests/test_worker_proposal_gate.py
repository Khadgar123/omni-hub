"""Regression for F2 — agent-lane (claude/codex) worker output MUST land
as a pending Proposal[T], not a silent task-done write."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

REPO_ROOT = Path(__file__).resolve().parents[1]

from omni_hub.cli import main
from omni_hub.proposals import ProposalStore
from omni_hub.queue import LeaseLost, TaskQueue
from omni_hub.workers import Artifact, ClaudeAdapter


def _run_cli(workspace: Path, argv: list[str]) -> dict:
    buffer = StringIO()
    original = REPO_ROOT
    try:
        os.chdir(workspace)
        with redirect_stdout(buffer):
            exit_code = main(argv)
    finally:
        os.chdir(original)
    payload = json.loads(buffer.getvalue())
    payload["__exit"] = exit_code
    return payload


def _fake_emit(payload: dict | str) -> list[str]:
    """A command_prefix that ignores all CLI args and prints fixed JSON."""

    body = payload if isinstance(payload, str) else json.dumps(payload)
    return [sys.executable, "-c", f"print({body!r})"]


class WorkerProposalGateTests(unittest.TestCase):
    def test_claude_lane_artifact_lands_as_pending_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            queue = TaskQueue(workspace)
            queue.enqueue(
                lane="claude",
                packet={"goal": "Summarise the buffer cache results."},
            )

            # Inject a fake ClaudeAdapter whose subprocess prints fixed JSON.
            adapter = ClaudeAdapter(
                command_prefix=_fake_emit({
                    "result": "Buffer cache hit rate rose to 92% [1].",
                    "session_id": "sid-test",
                    "model": "claude-opus-4-1",
                    "usage": {"input_tokens": 12, "output_tokens": 34},
                    "total_cost_usd": 0.0123,
                }),
                worker_id="test-claude",
            )

            with patch("omni_hub.cli.worker._make_adapter", return_value=adapter):
                result = _run_cli(workspace, [
                    "worker", "--lane", "claude",
                    "--idle-exit-after-sec", "1",
                    "--poll-interval-sec", "0.05",
                ])

            self.assertEqual(result["__exit"], 0)
            self.assertEqual(result["processed"], 1)
            self.assertEqual(result["proposals_made"], 1)

            # propose-list must surface the pending generation proposal.
            store = ProposalStore(workspace, create=False)
            proposals = store.list(kind="generation")
            self.assertEqual(len(proposals), 1)
            p = proposals[0]
            self.assertEqual(p.state, "pending")
            self.assertEqual(p.payload["model"], "claude-opus-4-1")
            self.assertEqual(p.payload["tokens_in"], 12)
            self.assertIn("buffer cache", p.summary.lower())

            # Task output must surface the proposal_id for traceability.
            done_tasks = queue.list(state="done")
            self.assertEqual(len(done_tasks), 1)
            self.assertEqual(
                done_tasks[0].output["proposal_id"], p.proposal_id,
            )
            self.assertEqual(done_tasks[0].output["proposal_state"], "pending")

    def test_python_lane_does_not_gate_through_proposal(self) -> None:
        """Built-in Python lane is exempt — its work already audited."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _run_cli(workspace, [
                "task-enqueue", "--lane", "python",
                "--packet-json", '{"operation":"memory_stats","payload":{},"kind":"text"}',
                "--idempotency-key", "py-1",
            ])
            result = _run_cli(workspace, [
                "worker", "--lane", "python",
                "--idle-exit-after-sec", "1",
                "--poll-interval-sec", "0.05",
            ])
            self.assertEqual(result["processed"], 1)
            self.assertEqual(result["proposals_made"], 0)
            store = ProposalStore(workspace, create=False)
            self.assertEqual(len(store.list(kind="generation")), 0)

    def test_failed_claude_artifact_does_not_create_proposal(self) -> None:
        """Only successful artifacts gate through Proposal; errors just fail the task."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            TaskQueue(workspace).enqueue(lane="claude", packet={"goal": "x"})

            adapter = ClaudeAdapter(
                command_prefix=[
                    sys.executable, "-c",
                    "import sys; print('boom', file=sys.stderr); sys.exit(2)",
                ],
                worker_id="test-claude",
            )
            with patch("omni_hub.cli.worker._make_adapter", return_value=adapter):
                result = _run_cli(workspace, [
                    "worker", "--lane", "claude",
                    "--idle-exit-after-sec", "1",
                    "--poll-interval-sec", "0.05",
                ])
            self.assertEqual(result["proposals_made"], 0)
            store = ProposalStore(workspace, create=False)
            self.assertEqual(len(store.list(kind="generation")), 0)

    def test_lease_lost_does_not_leave_pending_generation_proposal(self) -> None:
        """If a stale worker loses its lease after generation, the candidate
        must not stay in the human approval queue."""

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            TaskQueue(workspace).enqueue(lane="claude", packet={"goal": "x"})

            adapter = ClaudeAdapter(
                command_prefix=_fake_emit({
                    "result": "Stale result should not be pending.",
                    "session_id": "sid-stale",
                    "model": "claude-opus-4-1",
                }),
                worker_id="test-claude",
            )
            with (
                patch("omni_hub.cli.worker._make_adapter", return_value=adapter),
                patch(
                    "omni_hub.cli.worker.TaskQueue.complete",
                    side_effect=LeaseLost("lease lost"),
                ),
            ):
                result = _run_cli(workspace, [
                    "worker", "--lane", "claude",
                    "--idle-exit-after-sec", "1",
                    "--poll-interval-sec", "0.05",
                ])

            self.assertEqual(result["lease_losses"], 1)
            self.assertEqual(result["proposals_made"], 0)
            store = ProposalStore(workspace, create=False)
            pending = store.list(kind="generation", state="pending")
            self.assertEqual(pending, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
