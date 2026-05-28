"""Worker adapter pool: TaskPacket → Artifact.

A worker adapter pulls a ``Task`` from the queue, runs whatever computation
or external process it owns (a Python operation, a Claude Code subprocess,
a Codex CLI subprocess, an OpenHands container, …) and returns an
``Artifact`` that captures the result, the cost, and enough provenance for
the harness flywheel.

Adapters are deliberately the only place the queue layer interacts with
the outside world — the queue itself never invokes models or shells.
"""

from ..queue import LeaseLost
from .base import (
    Artifact,
    ArtifactKind,
    WorkerAdapter,
    WorkerError,
    WorkerTimeout,
    new_artifact_id,
)
from .builtin import BuiltinAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter

__all__ = [
    "Artifact",
    "ArtifactKind",
    "BuiltinAdapter",
    "ClaudeAdapter",
    "CodexAdapter",
    "LeaseLost",
    "WorkerAdapter",
    "WorkerError",
    "WorkerTimeout",
    "new_artifact_id",
]
