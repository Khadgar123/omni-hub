"""EvalRunner — execute an EvalPack against a candidate (v0.41).

Per the v0.41 design doc, every run persists into ``.omni/eval_runs.sqlite3``
so the flywheel can compute per-pack trend lines (does v0.2 beat v0.1
on the retained cases?).
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..judge import HeuristicJudge, JudgeRequest, JudgeVerdict, LLMJudge
from .store import EvalCase, EvalClass, EvalPack, EvalStore


RUNS_DB_REL = ".omni/eval_runs.sqlite3"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _new_run_id() -> str:
    return f"er_{secrets.token_hex(6)}"


# Pass thresholds per eval class (per Anthropic 2026-01 + project review):
#   capability — low bar (room to improve), composite >= 0.55
#   regression — high bar (must keep working), composite >= 0.85
#   calibration — rubric-fit, composite >= 0.70
_PASS_THRESHOLDS = {
    EvalClass.CAPABILITY:  0.55,
    EvalClass.REGRESSION:  0.85,
    EvalClass.CALIBRATION: 0.70,
}


@dataclass(slots=True)
class CaseResult:
    case_id: str
    eval_class: str
    passed: bool
    composite_score: float
    judge_verdict: dict[str, Any]
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvalRun:
    run_id: str
    pack_id: str
    judge_name: str
    composite_score: float                     # mean across cases
    pass_rate: float                            # fraction of cases passed
    pass_rate_by_class: dict[str, float] = field(default_factory=dict)
    per_case_results: list[CaseResult] = field(default_factory=list)
    skill_version: str = ""
    started_at: str = field(default_factory=_utcnow)
    finished_at: str = ""
    trace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["per_case_results"] = [c.to_dict() for c in self.per_case_results]
        return data


class EvalRunner:
    """Drives a single eval-pack run.

    Caller supplies a ``candidate_fn(case) -> str`` that produces the
    skill's candidate answer for each EvalCase (the runner doesn't know
    how the answer is produced — it could be a stub, a context-pack
    lookup, or a claude-lane task).  Heuristic judge by default; pass
    ``judge="llm"`` for the LLMJudge fallback chain.
    """

    def __init__(
        self,
        *,
        workspace: Path | str = ".",
        judge: str = "heuristic",
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.judge_name = judge
        self._judge = LLMJudge() if judge == "llm" else HeuristicJudge()
        self.db_path = self.workspace / RUNS_DB_REL
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def run(
        self,
        pack: EvalPack,
        *,
        candidate_fn=None,
        skill_version: str = "",
        trace_id: str = "",
        include_holdout: bool = False,
    ) -> EvalRun:
        """Run ``pack`` against ``candidate_fn``.

        Default candidate_fn returns the case's ``expected`` field — this
        makes "do the eval primitives work?" testable without an LLM.
        """

        if candidate_fn is None:
            candidate_fn = _default_echo_candidate

        store = EvalStore(self.workspace)
        cases = store.list_cases(pack, include_holdout=include_holdout)
        results: list[CaseResult] = []
        by_class: dict[str, list[bool]] = {}
        for case in cases:
            candidate = candidate_fn(case)
            request = JudgeRequest(
                domain=case.domain,
                candidate=candidate,
                reference=case.expected,
                rubric=case.rubric_weights or {},
                trace_id=trace_id,
            )
            verdict: JudgeVerdict = self._judge.evaluate(request)
            threshold = _PASS_THRESHOLDS[case.eval_class]
            passed = verdict.composite >= threshold
            results.append(CaseResult(
                case_id=case.case_id,
                eval_class=case.eval_class.value,
                passed=passed,
                composite_score=verdict.composite,
                judge_verdict=verdict.to_dict(),
                rationale=verdict.rationale,
            ))
            by_class.setdefault(case.eval_class.value, []).append(passed)

        composite_mean = (
            sum(r.composite_score for r in results) / len(results)
            if results else 0.0
        )
        pass_rate = (
            sum(1 for r in results if r.passed) / len(results) if results else 0.0
        )
        pass_rate_by_class = {
            cls: (sum(1 for p in passes if p) / len(passes)) if passes else 0.0
            for cls, passes in by_class.items()
        }
        run = EvalRun(
            run_id=_new_run_id(),
            pack_id=pack.pack_id,
            judge_name=self.judge_name,
            composite_score=round(composite_mean, 4),
            pass_rate=round(pass_rate, 4),
            pass_rate_by_class={k: round(v, 4) for k, v in pass_rate_by_class.items()},
            per_case_results=results,
            skill_version=skill_version,
            finished_at=_utcnow(),
            trace_id=trace_id,
        )
        self._persist(run)
        return run

    # ---- persistence -------------------------------------------

    def list_runs(self, *, pack_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        sql = (
            "SELECT run_id, pack_id, judge_name, composite_score, pass_rate, "
            "       skill_version, started_at, finished_at, trace_id "
            "FROM eval_runs"
        )
        params: tuple = ()
        if pack_id:
            sql += " WHERE pack_id = ? "
            params = (pack_id,)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params = params + (limit,)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def _persist(self, run: EvalRun) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO eval_runs "
                "(run_id, pack_id, judge_name, composite_score, pass_rate, "
                " pass_rate_by_class, skill_version, started_at, finished_at, "
                " trace_id, verdict_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run.run_id, run.pack_id, run.judge_name,
                 run.composite_score, run.pass_rate,
                 json.dumps(run.pass_rate_by_class, ensure_ascii=False),
                 run.skill_version, run.started_at, run.finished_at,
                 run.trace_id,
                 json.dumps(run.to_dict(), ensure_ascii=False)),
            )
            conn.commit()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA busy_timeout = 30000;

                CREATE TABLE IF NOT EXISTS eval_runs (
                    run_id              TEXT PRIMARY KEY,
                    pack_id             TEXT NOT NULL,
                    judge_name          TEXT NOT NULL,
                    composite_score     REAL NOT NULL,
                    pass_rate           REAL NOT NULL,
                    pass_rate_by_class  TEXT DEFAULT '{}',
                    skill_version       TEXT DEFAULT '',
                    started_at          TEXT NOT NULL,
                    finished_at         TEXT NOT NULL,
                    trace_id            TEXT DEFAULT '',
                    verdict_json        TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_eval_runs_pack
                    ON eval_runs(pack_id, started_at DESC);
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        from .._storage import connect_sqlite_store
        return connect_sqlite_store(self.db_path)


def _default_echo_candidate(case: EvalCase) -> str:
    """No-LLM placeholder candidate: echo the expected for capability /
    regression cases, return empty for calibration so callers see the
    fallback path."""

    if case.eval_class in (EvalClass.CAPABILITY, EvalClass.REGRESSION):
        return case.expected
    return case.question


__all__ = ["CaseResult", "EvalRun", "EvalRunner"]
