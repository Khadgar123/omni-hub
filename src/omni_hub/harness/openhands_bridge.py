"""OpenHands bridge with graceful fallback.

OpenHands is a heavyweight enterprise engineering agent.  Until the fork is
pinned and a runtime is configured locally, the harness only needs the *task
specification* contract — what we'd ask OpenHands to do.  This module gives
us a deterministic, testable surface:

- ``EngineeringTask`` — what to dispatch (issue text, target repo, success
  criteria).
- ``run(task)`` — invokes OpenHands when present, else returns a structured
  "DispatchSpec" record that another tool (or a human) can execute.

The harness loop treats both outcomes the same way: a ``GenerationRecord``
with one ``Candidate`` per attempt, downstream judges/preference/compile
still apply.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .models import Candidate, GenerationRecord


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _openhands_available() -> bool:
    try:
        import openhands  # type: ignore[import-not-found]  # noqa: F401
        return True
    except Exception:
        return False


@dataclass(slots=True)
class EngineeringTask:
    task_id: str
    repo_path: str
    issue_title: str
    issue_body: str
    success_criteria: list[str] = field(default_factory=list)
    max_iterations: int = 25
    branch_prefix: str = "harness/"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class DispatchResult:
    backend: str                 # "openhands" | "stub"
    task_id: str
    repo_path: str
    started_at: str
    finished_at: str | None = None
    branch: str = ""
    patch: str = ""
    notes: str = ""
    success: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def run(task: EngineeringTask) -> DispatchResult:
    """Dispatch an engineering task; OpenHands path or testable stub."""

    started = _utcnow()
    branch = f"{task.branch_prefix}{task.task_id[:8]}"

    if _openhands_available():  # pragma: no cover — exercised once installed
        # Real wire-up lands here once openhands fork is pinned.  For now we
        # mark the dispatch but do not actually mutate the working tree.
        return DispatchResult(
            backend="openhands",
            task_id=task.task_id,
            repo_path=task.repo_path,
            started_at=started,
            finished_at=_utcnow(),
            branch=branch,
            patch="",
            notes="OpenHands available; full integration TODO once fork pinned.",
            success=False,
        )

    return DispatchResult(
        backend="stub",
        task_id=task.task_id,
        repo_path=task.repo_path,
        started_at=started,
        finished_at=_utcnow(),
        branch=branch,
        patch="",
        notes=(
            "OpenHands not installed. This is a dispatch spec — supply it to "
            "SWE-agent (`make harness-update` then run inside "
            "`agent-harness/swe-agent`) or open it manually."
        ),
        success=False,
    )


def dispatch_as_generation_record(task: EngineeringTask) -> GenerationRecord:
    """Wrap a single dispatch attempt as a one-candidate GenerationRecord
    so the standard judge/preference flow still applies."""

    result = run(task)
    record = GenerationRecord(task_id=task.task_id)
    cand = Candidate(
        model=f"engineering-agent:{result.backend}",
        text=result.patch or result.notes,
    )
    if not result.success:
        cand.failure_tags.append("no_patch")
    record.candidates.append(cand)
    return record
