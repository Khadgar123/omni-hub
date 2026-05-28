"""Tests for ClaudeAdapter + CodexAdapter (Φ1-T4).

These tests do NOT shell out to the real ``claude`` or ``codex`` binaries.
A fake ``python -c "print(...)"`` command stands in for the binary so the
subprocess + parsing path is exercised end-to-end on machines that don't
have Claude Code or Codex installed.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.queue import Task
from omni_hub.workers import ClaudeAdapter, CodexAdapter


def _python_emits(payload: dict | str) -> list[str]:
    """Return a command_prefix that pretends to be ``claude``/``codex``.

    The Python one-liner ignores all CLI args appended by the adapter and
    just prints the desired JSON (or text) to stdout.
    """

    body = payload if isinstance(payload, str) else json.dumps(payload)
    # repr() escapes the string for safe embedding in a python -c snippet
    return [sys.executable, "-c", f"print({body!r})"]


def _failing_command(stderr: str = "boom") -> list[str]:
    return [
        sys.executable, "-c",
        f"import sys; print({stderr!r}, file=sys.stderr); sys.exit(2)",
    ]


def _task(packet: dict, task_id: int = 1) -> Task:
    return Task(id=task_id, lane="claude", packet=packet)


class ClaudeAdapterTests(unittest.TestCase):
    def test_happy_path_parses_result_and_usage(self) -> None:
        adapter = ClaudeAdapter(
            command_prefix=_python_emits({
                "result": "Compiled successfully.",
                "session_id": "sid-1",
                "model": "claude-opus-4-1",
                "usage": {"input_tokens": 12, "output_tokens": 34},
                "total_cost_usd": 0.0123,
            }),
        )
        artifact = adapter.run(_task({"goal": "test", "audience": "dev"}))
        self.assertIsNone(artifact.error)
        self.assertEqual(artifact.kind, "generation")
        self.assertEqual(artifact.data["text"], "Compiled successfully.")
        self.assertEqual(artifact.data["session_id"], "sid-1")
        self.assertEqual(artifact.tokens_in, 12)
        self.assertEqual(artifact.tokens_out, 34)
        self.assertAlmostEqual(artifact.cost_usd, 0.0123)
        self.assertEqual(artifact.worker_lane, "claude")

    def test_invalid_json_returns_error_artifact(self) -> None:
        adapter = ClaudeAdapter(command_prefix=_python_emits("not-json"))
        artifact = adapter.run(_task({"goal": "x"}))
        self.assertEqual(artifact.error, "invalid json")
        self.assertIn("not-json", artifact.data["detail"])

    def test_non_zero_exit_returns_error_artifact(self) -> None:
        adapter = ClaudeAdapter(command_prefix=_failing_command("denied"))
        artifact = adapter.run(_task({"goal": "x"}))
        self.assertEqual(artifact.error, "non-zero exit")
        self.assertIn("denied", artifact.data["detail"])

    def test_missing_binary_returns_error_not_crash(self) -> None:
        adapter = ClaudeAdapter(
            command_prefix=["/definitely/does/not/exist/claude-binary"],
        )
        artifact = adapter.run(_task({"goal": "x"}))
        self.assertEqual(artifact.error, "claude binary missing")

    def test_timeout_returns_error_artifact(self) -> None:
        adapter = ClaudeAdapter(
            command_prefix=[
                sys.executable, "-c",
                "import time; time.sleep(5)",
            ],
        )
        artifact = adapter.run(_task({"goal": "x"}), timeout_sec=1)
        self.assertEqual(artifact.error, "timeout")
        self.assertIn("SIGKILL", artifact.data["detail"])

    def test_prompt_falls_back_to_packet_json_when_no_fields(self) -> None:
        # Adapter must not crash on a sparse packet — it serializes it.
        adapter = ClaudeAdapter(
            command_prefix=_python_emits({"result": "ok", "usage": {}}),
        )
        artifact = adapter.run(_task({}))
        self.assertIsNone(artifact.error)


class CodexAdapterTests(unittest.TestCase):
    def test_happy_path_picks_last_jsonl_object(self) -> None:
        # Codex `--json` typically emits JSONL; adapter should pick the last
        # complete object as the final result.
        adapter = CodexAdapter(
            command_prefix=[
                sys.executable, "-c",
                "print('{\"event\": \"start\"}'); "
                "print('{\"output_text\": \"done\", \"usage\": {\"input_tokens\": 5, \"output_tokens\": 7}}')",
            ],
        )
        artifact = adapter.run(_task({"goal": "fix test"}))
        self.assertIsNone(artifact.error)
        self.assertEqual(artifact.data["text"], "done")
        self.assertEqual(artifact.tokens_in, 5)
        self.assertEqual(artifact.tokens_out, 7)
        self.assertEqual(artifact.worker_lane, "codex")

    def test_missing_binary_returns_error(self) -> None:
        adapter = CodexAdapter(
            command_prefix=["/definitely/does/not/exist/codex-binary"],
        )
        artifact = adapter.run(_task({"goal": "x"}))
        self.assertEqual(artifact.error, "codex binary missing")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
