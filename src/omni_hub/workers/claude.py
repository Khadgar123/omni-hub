"""Headless Claude Code worker adapter.

Invokes ``claude -p --output-format json`` as a subprocess, parses the
result block, and wraps it as a ``generation`` Artifact.  Hard timeout uses
``subprocess.run(timeout=...)`` (which sends SIGKILL after the grace
period) — adapters running long tasks should set ``timeout_sec`` to match
the worker's visibility timeout.

Defaults are conservative: ``--permission-mode plan`` and
``--allowedTools Read`` — any write the headless invocation wants to do
must come back as a Proposal that the human approves later.  Override the
defaults at construction time if a lane needs more freedom.

For testing without the ``claude`` binary, pass ``command_prefix`` with a
fake binary (e.g. a Python one-liner that prints fixed JSON).
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
from .base import Artifact, GENERATION, WorkerError, WorkerTimeout, new_artifact_id


_DEFAULT_ALLOWED_TOOLS = ("Read",)


class ClaudeAdapter:
    """Run a TaskPacket through ``claude -p``."""

    name = "claude-print"
    lane = "claude"

    def __init__(
        self,
        *,
        command_prefix: list[str] | None = None,
        cwd: Path | str | None = None,
        allowed_tools: tuple[str, ...] = _DEFAULT_ALLOWED_TOOLS,
        disallowed_tools: tuple[str, ...] = (),
        permission_mode: str = "plan",
        max_turns: int = 5,
        mcp_config_path: str | None = None,
        env: dict[str, str] | None = None,
        worker_id: str = "claude-worker",
    ) -> None:
        self.command_prefix = list(command_prefix) if command_prefix else ["claude"]
        self.cwd = Path(cwd) if cwd is not None else None
        self.allowed_tools = tuple(allowed_tools)
        self.disallowed_tools = tuple(disallowed_tools)
        self.permission_mode = permission_mode
        self.max_turns = max_turns
        self.mcp_config_path = mcp_config_path
        self.env = env
        self.worker_id = worker_id

    # ------------------------------------------------------------------

    def _render_prompt(self, packet: dict[str, Any]) -> str:
        """Map a TaskPacket-shaped dict into a single prompt string.

        Accepts any of:
            - ``packet["prompt"]`` (already-rendered prompt)
            - the harness TaskPacket fields (goal, audience, ...)
        """

        if isinstance(packet.get("prompt"), str) and packet["prompt"]:
            return str(packet["prompt"])

        parts: list[str] = []
        if packet.get("goal"):
            parts.append(f"Goal: {packet['goal']}")
        if packet.get("audience"):
            parts.append(f"Audience: {packet['audience']}")
        if packet.get("sources_required"):
            parts.append(
                "Sources to cite: " + ", ".join(packet["sources_required"])
            )
        if packet.get("claims_to_cover"):
            parts.append(
                "Claims to cover: " + ", ".join(packet["claims_to_cover"])
            )
        if packet.get("constraints"):
            parts.append(f"Constraints: {json.dumps(packet['constraints'], ensure_ascii=False)}")
        if not parts:
            # Fallback — serialize the whole packet
            return json.dumps(packet, ensure_ascii=False)
        return "\n".join(parts)

    def _build_command(self, prompt: str) -> list[str]:
        cmd = list(self.command_prefix) + [
            "-p", prompt,
            "--output-format", "json",
            "--permission-mode", self.permission_mode,
            "--max-turns", str(self.max_turns),
        ]
        if self.allowed_tools:
            cmd += ["--allowedTools", ",".join(self.allowed_tools)]
        if self.disallowed_tools:
            cmd += ["--disallowedTools", ",".join(self.disallowed_tools)]
        if self.mcp_config_path:
            cmd += ["--mcp-config", self.mcp_config_path]
        return cmd

    def run(self, task: Task, *, timeout_sec: int = 300) -> Artifact:
        prompt = self._render_prompt(task.packet)
        cmd = self._build_command(prompt)

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
                detail=f"claude exceeded {timeout_sec}s; sent SIGKILL",
            )
        except FileNotFoundError as exc:
            return self._error_artifact(
                task, "claude binary missing", 0,
                detail=f"{' '.join(shlex.quote(c) for c in cmd[:1])}: {exc}",
            )

        elapsed_ms = int((time.time() - start) * 1000)

        if proc.returncode != 0:
            return self._error_artifact(
                task, "non-zero exit", elapsed_ms,
                detail=(proc.stderr or "")[:500],
                stdout=proc.stdout,
            )

        try:
            result = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError as exc:
            return self._error_artifact(
                task, "invalid json", elapsed_ms,
                detail=f"{exc}: {proc.stdout[:300]}",
            )

        if not isinstance(result, dict):
            return self._error_artifact(
                task, "json not an object", elapsed_ms, detail=str(result)[:300],
            )

        usage = result.get("usage", {}) or {}
        return Artifact(
            artifact_id=new_artifact_id(),
            kind=GENERATION,
            data={
                "text": result.get("result", "") or result.get("content", ""),
                "session_id": result.get("session_id"),
                "model": result.get("model"),
                "tools_used": result.get("tools_used", []),
                "raw": result,
            },
            task_id=task.id,
            worker_lane=self.lane,
            worker_id=self.worker_id,
            duration_ms=elapsed_ms,
            tokens_in=int(usage.get("input_tokens", 0) or 0),
            tokens_out=int(usage.get("output_tokens", 0) or 0),
            cost_usd=float(result.get("total_cost_usd", 0.0) or 0.0),
        )

    # ------------------------------------------------------------------

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
