"""Headless OpenHands worker adapter (engineering agent lane).

OpenHands (formerly OpenDevin) is a production-grade engineering agent
platform with an immutable event log + Docker sandbox.  We use it as the
"long engineering task" lane, complementing Claude/Codex for "short
reasoning + tool-use" tasks.

This adapter follows the same shape as :class:`ClaudeAdapter` /
:class:`CodexAdapter`: subprocess + JSON parsing + SIGKILL on timeout.
Real OpenHands invocations vary widely across releases (CLI, REST API,
Docker container), so the adapter exposes ``command_prefix`` and
``extra_args`` so callers can adapt to whatever they have installed
without editing this file.

Default invocation assumes the ``openhands`` CLI v0.34+ has a JSON-output
mode similar to ``codex exec --json``.  If your install differs, swap the
prefix at construction time::

    adapter = OpenHandsAdapter(
        command_prefix=["docker", "run", "-i",
                        "ghcr.io/all-hands-ai/openhands:0.34",
                        "headless"],
        extra_args=("--json", "--sandbox", "read-only"),
    )

The lane name is ``openhands``; it is in ``_GATED_LANES`` so any
successful artifact lands as a pending Proposal[T] for human approval.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from ..queue import Task
from .base import Artifact, GENERATION, new_artifact_id


class OpenHandsAdapter:
    """Run a TaskPacket through OpenHands' headless mode."""

    name = "openhands"
    lane = "openhands"

    def __init__(
        self,
        *,
        command_prefix: list[str] | None = None,
        cwd: Path | str | None = None,
        extra_args: tuple[str, ...] = ("--json", "--sandbox", "read-only"),
        env: dict[str, str] | None = None,
        worker_id: str = "openhands-worker",
    ) -> None:
        self.command_prefix = (
            list(command_prefix) if command_prefix else ["openhands", "exec"]
        )
        self.cwd = Path(cwd) if cwd is not None else None
        self.extra_args = tuple(extra_args)
        self.env = env
        self.worker_id = worker_id

    # ------------------------------------------------------------------

    def _render_prompt(self, packet: dict[str, Any]) -> str:
        if isinstance(packet.get("prompt"), str) and packet["prompt"]:
            return str(packet["prompt"])
        if isinstance(packet.get("goal"), str) and packet["goal"]:
            return str(packet["goal"])
        return json.dumps(packet, ensure_ascii=False)

    def _build_command(self, prompt: str) -> list[str]:
        return list(self.command_prefix) + list(self.extra_args) + [prompt]

    def run(self, task: Task, *, timeout_sec: int = 300) -> Artifact:
        cmd = self._build_command(self._render_prompt(task.packet))
        env = None
        if self.env:
            env = {**os.environ, **self.env}

        start = time.time()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
                cwd=str(self.cwd) if self.cwd else None,
                env=env,
            )
        except subprocess.TimeoutExpired:
            elapsed_ms = int((time.time() - start) * 1000)
            return self._error_artifact(
                task, "timeout", elapsed_ms,
                detail=f"openhands exceeded {timeout_sec}s; sent SIGKILL",
            )
        except FileNotFoundError as exc:
            return self._error_artifact(
                task, "openhands binary missing", 0,
                detail=f"{' '.join(shlex.quote(c) for c in cmd[:1])}: {exc}",
            )

        elapsed_ms = int((time.time() - start) * 1000)

        if proc.returncode != 0:
            return self._error_artifact(
                task, "non-zero exit", elapsed_ms,
                detail=(proc.stderr or "")[:500],
                stdout=proc.stdout,
            )

        result = self._parse_output(proc.stdout)
        usage = result.get("usage", {}) if isinstance(result, dict) else {}
        text = ""
        patch = ""
        if isinstance(result, dict):
            text = (
                result.get("output_text")
                or result.get("text")
                or result.get("result")
                or ""
            )
            patch = result.get("patch") or result.get("diff") or ""

        return Artifact(
            artifact_id=new_artifact_id(),
            kind=GENERATION,
            data={
                "text": text,
                "patch": patch,
                "raw": result,
            },
            task_id=task.id,
            worker_lane=self.lane,
            worker_id=self.worker_id,
            duration_ms=elapsed_ms,
            tokens_in=int(usage.get("input_tokens", 0) or 0),
            tokens_out=int(usage.get("output_tokens", 0) or 0),
            cost_usd=float(result.get("total_cost_usd", 0.0) or 0.0)
            if isinstance(result, dict) else 0.0,
        )

    # ------------------------------------------------------------------

    def _parse_output(self, stdout: str) -> Any:
        """OpenHands JSONL streaming output — pick the last complete object."""

        last: Any = {}
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
        return last

    def _error_artifact(
        self,
        task: Task,
        error: str,
        duration_ms: int,
        *,
        detail: str = "",
        stdout: str = "",
    ) -> Artifact:
        return Artifact(
            artifact_id=new_artifact_id(),
            kind=GENERATION,
            data={"stdout": stdout, "detail": detail},
            task_id=task.id,
            worker_lane=self.lane,
            worker_id=self.worker_id,
            duration_ms=duration_ms,
            error=error,
        )
