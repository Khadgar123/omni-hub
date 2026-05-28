"""Anthropic Memory Tool surface — `memory_20250818` parity.

Anthropic's Memory Tool (https://platform.claude.com/docs/en/agents-and-tools/
tool-use/memory-tool) is the client-side memory contract Claude 4.x and
Claude 5.x agents use.  The agent emits one of six commands; the client
runs them against a sandboxed `/memories` directory.

This module exposes the same six commands against ``vault/memory/`` so any
agent that already speaks the standard tool contract gets a working backend
without writing custom integration code.

Commands (verbatim from Anthropic spec):

    view(path, view_range?)          — read a file or directory
    create(path, file_text)          — create or overwrite a file
    str_replace(path, old_str, new_str) — exact-match find/replace
    insert(path, insert_line, insert_text) — insert at given line number
    delete(path)                     — delete file or directory
    rename(old_path, new_path)       — rename within the memory root

Path policy
-----------

* Paths MUST start with ``/memories`` (Anthropic convention).
* The leading ``/memories`` is mapped to ``vault/memory/`` under the
  workspace.
* Path traversal (``..``, absolute escapes) is rejected at the surface
  layer — every command resolves against the memory root and refuses any
  result that lands outside it.

Stored under ``vault/memory/`` so the existing `vault/` is the single
filesystem-of-truth: Anthropic-managed memory clients see the same files
as the Karpathy wiki layer.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._storage import safe_workspace_path


MEMORY_ROOT_REL = "vault/memory"
MEMORY_PATH_PREFIX = "/memories"
TOOL_VERSION = "memory_20250818"


class MemoryToolError(Exception):
    """Raised on any policy or filesystem failure surfaced to the agent."""


@dataclass(slots=True)
class MemoryToolResponse:
    ok: bool
    command: str
    path: str
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tool": TOOL_VERSION,
            "command": self.command,
            "path": self.path,
            **self.detail,
        }


class MemoryTool:
    """Filesystem-backed implementation of Anthropic's ``memory_20250818``.

    Construct with a workspace path; call commands as methods.  All
    commands return ``MemoryToolResponse``; failures raise
    ``MemoryToolError`` so the OperationRunner can record the policy
    decision uniformly.
    """

    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace).resolve()
        self.memory_root = safe_workspace_path(self.workspace, MEMORY_ROOT_REL)
        self.memory_root.mkdir(parents=True, exist_ok=True)

    # ---- spec commands --------------------------------------------

    def view(
        self,
        path: str,
        *,
        view_range: tuple[int, int] | None = None,
    ) -> MemoryToolResponse:
        target = self._resolve(path, must_exist=True)
        if target.is_dir():
            entries = sorted(p.name for p in target.iterdir())
            return MemoryToolResponse(
                ok=True, command="view", path=path,
                detail={"type": "directory", "entries": entries},
            )
        text = target.read_text(encoding="utf-8")
        lines = text.splitlines()
        if view_range is None:
            body = text
            shown_lines = (1, len(lines))
        else:
            start, end = view_range
            if start < 1:
                raise MemoryToolError(f"view_range start must be >= 1, got {start}")
            # end == -1 means "to end of file" per Anthropic spec.
            actual_end = len(lines) if end == -1 else end
            if actual_end < start:
                raise MemoryToolError(
                    f"view_range end ({actual_end}) must be >= start ({start})"
                )
            sliced = lines[start - 1 : actual_end]
            body = "\n".join(sliced)
            shown_lines = (start, actual_end)
        # Anthropic format: prefix each line with "<n>: " for editing
        # reliability; the agent expects that shape.
        annotated_lines = []
        start_idx = shown_lines[0]
        body_lines = body.splitlines() if body else [""]
        for i, line in enumerate(body_lines):
            annotated_lines.append(f"{start_idx + i:>6}\t{line}")
        return MemoryToolResponse(
            ok=True, command="view", path=path,
            detail={
                "type": "file",
                "view_range": list(shown_lines),
                "total_lines": len(lines),
                "annotated": "\n".join(annotated_lines),
            },
        )

    def create(self, path: str, file_text: str) -> MemoryToolResponse:
        target = self._resolve(path, must_exist=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file_text, encoding="utf-8")
        return MemoryToolResponse(
            ok=True, command="create", path=path,
            detail={"bytes": target.stat().st_size},
        )

    def str_replace(
        self,
        path: str,
        old_str: str,
        new_str: str,
    ) -> MemoryToolResponse:
        target = self._resolve(path, must_exist=True)
        if not target.is_file():
            raise MemoryToolError(f"str_replace target is not a file: {path}")
        text = target.read_text(encoding="utf-8")
        count = text.count(old_str)
        if count == 0:
            raise MemoryToolError(
                f"str_replace: old_str not found in {path}"
            )
        if count > 1:
            raise MemoryToolError(
                f"str_replace: old_str matches {count} times in {path}; "
                "must be unique (Anthropic spec)"
            )
        new_text = text.replace(old_str, new_str, 1)
        target.write_text(new_text, encoding="utf-8")
        return MemoryToolResponse(
            ok=True, command="str_replace", path=path,
            detail={"bytes_before": len(text), "bytes_after": len(new_text)},
        )

    def insert(
        self,
        path: str,
        insert_line: int,
        insert_text: str,
    ) -> MemoryToolResponse:
        target = self._resolve(path, must_exist=True)
        if not target.is_file():
            raise MemoryToolError(f"insert target is not a file: {path}")
        lines = target.read_text(encoding="utf-8").splitlines()
        if insert_line < 0 or insert_line > len(lines):
            raise MemoryToolError(
                f"insert_line {insert_line} out of bounds [0, {len(lines)}]"
            )
        new_lines = lines[:insert_line] + insert_text.splitlines() + lines[insert_line:]
        target.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return MemoryToolResponse(
            ok=True, command="insert", path=path,
            detail={"insert_line": insert_line, "new_line_count": len(new_lines)},
        )

    def delete(self, path: str) -> MemoryToolResponse:
        target = self._resolve(path, must_exist=True)
        if target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)
        return MemoryToolResponse(
            ok=True, command="delete", path=path,
            detail={"removed_type": "file" if target.suffix else "directory"},
        )

    def rename(self, old_path: str, new_path: str) -> MemoryToolResponse:
        src = self._resolve(old_path, must_exist=True)
        dst = self._resolve(new_path, must_exist=False)
        if dst.exists():
            raise MemoryToolError(f"rename target already exists: {new_path}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return MemoryToolResponse(
            ok=True, command="rename", path=new_path,
            detail={"from": old_path, "to": new_path},
        )

    # ---- path policy ----------------------------------------------

    def _resolve(self, path: str, *, must_exist: bool) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise MemoryToolError("path must be a non-empty string")
        normalized = path.strip()
        if not normalized.startswith(MEMORY_PATH_PREFIX):
            raise MemoryToolError(
                f"path must start with {MEMORY_PATH_PREFIX!r} (got {path!r})"
            )
        relative = normalized[len(MEMORY_PATH_PREFIX):].lstrip("/")
        # Reject traversal — every segment must be a plain identifier.
        # Use raw string split so any `..` / `.` literal anywhere in the
        # path is caught (Path.parts collapses some shapes on certain
        # platforms; splitting is unambiguous).
        for part in relative.split("/"):
            if part in {"", "..", "."}:
                # empty segment = double slash, also reject
                if part == "":
                    continue
                raise MemoryToolError(
                    f"path segment {part!r} is not allowed (no traversal)"
                )
            if part.startswith("/") or part.startswith("\\"):
                raise MemoryToolError(
                    f"path segment {part!r} is not allowed (no absolute escape)"
                )
        target = (self.memory_root / relative).resolve() if relative else self.memory_root
        # Final defence: refuse anything outside memory_root after resolve.
        try:
            target.relative_to(self.memory_root)
        except ValueError:
            raise MemoryToolError(f"path escapes memory root: {path}")
        if must_exist and not target.exists():
            raise MemoryToolError(f"path does not exist: {path}")
        return target


def dispatch(
    workspace: Path | str,
    command: str,
    *,
    arguments: dict[str, Any],
) -> MemoryToolResponse:
    """Single-entry dispatcher mirroring how MCP / Claude desktop sends
    the agent's tool call: ``{command, ...args}`` → response.
    """

    tool = MemoryTool(workspace)
    command = command.strip().lower()
    if command == "view":
        view_range = arguments.get("view_range")
        if view_range is not None:
            if not (isinstance(view_range, (list, tuple)) and len(view_range) == 2):
                raise MemoryToolError(
                    "view_range must be a 2-tuple [start, end] or [start, -1]"
                )
            view_range = (int(view_range[0]), int(view_range[1]))
        return tool.view(str(arguments["path"]), view_range=view_range)
    if command == "create":
        return tool.create(str(arguments["path"]), str(arguments.get("file_text", "")))
    if command == "str_replace":
        return tool.str_replace(
            str(arguments["path"]),
            str(arguments["old_str"]),
            str(arguments.get("new_str", "")),
        )
    if command == "insert":
        return tool.insert(
            str(arguments["path"]),
            int(arguments["insert_line"]),
            str(arguments.get("insert_text", "")),
        )
    if command == "delete":
        return tool.delete(str(arguments["path"]))
    if command == "rename":
        return tool.rename(
            str(arguments["old_path"]),
            str(arguments["new_path"]),
        )
    raise MemoryToolError(
        f"unknown memory tool command {command!r}; expected one of "
        "view|create|str_replace|insert|delete|rename"
    )
