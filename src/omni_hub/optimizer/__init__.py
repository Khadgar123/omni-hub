"""Optimizer contracts for DSPy/GEPA-style skill evolution.

This layer is deliberately dependency-free.  Real optimizers such as DSPy
GEPA, MIPRO, or BootstrapFewShot can plug in later, but the control plane
needs stable local records first: skill versions, dataset splits, eval gates,
and optimization runs.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


PASSED = "passed"
FAILED = "failed"
NEEDS_REVIEW = "needs_review"
VALID_GATE_DECISIONS = {PASSED, FAILED, NEEDS_REVIEW}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return str(uuid4())


@dataclass(slots=True)
class DatasetSplit:
    """Dataset sizes used by one optimizer run."""

    train_count: int = 0
    dev_count: int = 0
    holdout_count: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DatasetSplit":
        return cls(
            train_count=int(data.get("train_count", 0)),
            dev_count=int(data.get("dev_count", 0)),
            holdout_count=int(data.get("holdout_count", 0)),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        for key, value in self.to_dict().items():
            if value < 0:
                errors.append(f"{key} must be >= 0")
        return errors


@dataclass(slots=True)
class EvalGate:
    """Holdout gate that protects skill releases from optimizer overfit."""

    metric_thresholds: dict[str, float] = field(default_factory=dict)
    min_holdout_count: int = 0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvalGate":
        return cls(
            metric_thresholds={
                str(k): float(v)
                for k, v in dict(data.get("metric_thresholds", {})).items()
            },
            min_holdout_count=int(data.get("min_holdout_count", 0)),
            notes=str(data.get("notes", "")),
        )

    def decide(
        self,
        *,
        split: DatasetSplit,
        holdout_metrics: dict[str, float],
    ) -> str:
        """Return passed / failed / needs_review for one run."""

        if split.holdout_count < self.min_holdout_count:
            return NEEDS_REVIEW
        if not self.metric_thresholds:
            return NEEDS_REVIEW
        for name, threshold in self.metric_thresholds.items():
            if name not in holdout_metrics:
                return FAILED
            if float(holdout_metrics[name]) < float(threshold):
                return FAILED
        return PASSED


@dataclass(slots=True)
class SkillVersion:
    """One versioned skill/prompt/program artifact."""

    skill_id: str
    version: str
    domain: str = "engineering"
    prompt_path: str = ""
    module_path: str = ""
    optimizer: str = "manual"
    source_run_id: str = ""
    status: str = "candidate"
    metrics: dict[str, float] = field(default_factory=dict)
    notes: str = ""
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillVersion":
        return cls(
            skill_id=str(data.get("skill_id", "")),
            version=str(data.get("version", "")),
            domain=str(data.get("domain", "engineering")),
            prompt_path=str(data.get("prompt_path", "")),
            module_path=str(data.get("module_path", "")),
            optimizer=str(data.get("optimizer", "manual")),
            source_run_id=str(data.get("source_run_id", "")),
            status=str(data.get("status", "candidate")),
            metrics={str(k): float(v) for k, v in dict(data.get("metrics", {})).items()},
            notes=str(data.get("notes", "")),
            created_at=str(data.get("created_at") or _utcnow()),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.skill_id.strip():
            errors.append("skill_id must be non-empty")
        if not self.version.strip():
            errors.append("version must be non-empty")
        if not self.domain.strip():
            errors.append("domain must be non-empty")
        return errors


@dataclass(slots=True)
class OptimizationRun:
    """One optimizer attempt, including its data split and gate result."""

    skill_id: str
    optimizer: str
    from_version: str
    to_version: str
    run_id: str = field(default_factory=_new_id)
    dataset_split: DatasetSplit = field(default_factory=DatasetSplit)
    eval_gate: EvalGate = field(default_factory=EvalGate)
    train_metrics: dict[str, float] = field(default_factory=dict)
    dev_metrics: dict[str, float] = field(default_factory=dict)
    holdout_metrics: dict[str, float] = field(default_factory=dict)
    pareto_candidates: int = 0
    gate_decision: str = ""
    notes: str = ""
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OptimizationRun":
        return cls(
            skill_id=str(data.get("skill_id", "")),
            optimizer=str(data.get("optimizer", "")),
            from_version=str(data.get("from_version", "")),
            to_version=str(data.get("to_version", "")),
            run_id=str(data.get("run_id") or _new_id()),
            dataset_split=DatasetSplit.from_dict(dict(data.get("dataset_split", {}))),
            eval_gate=EvalGate.from_dict(dict(data.get("eval_gate", {}))),
            train_metrics=_float_map(data.get("train_metrics", {})),
            dev_metrics=_float_map(data.get("dev_metrics", {})),
            holdout_metrics=_float_map(data.get("holdout_metrics", {})),
            pareto_candidates=int(data.get("pareto_candidates", 0)),
            gate_decision=str(data.get("gate_decision", "")),
            notes=str(data.get("notes", "")),
            created_at=str(data.get("created_at") or _utcnow()),
        )

    def validate(self) -> list[str]:
        errors = self.dataset_split.validate()
        if not self.skill_id.strip():
            errors.append("skill_id must be non-empty")
        if not self.optimizer.strip():
            errors.append("optimizer must be non-empty")
        if not self.from_version.strip():
            errors.append("from_version must be non-empty")
        if not self.to_version.strip():
            errors.append("to_version must be non-empty")
        if self.pareto_candidates < 0:
            errors.append("pareto_candidates must be >= 0")
        if self.gate_decision and self.gate_decision not in VALID_GATE_DECISIONS:
            errors.append(f"gate_decision must be one of {sorted(VALID_GATE_DECISIONS)}")
        return errors


class OptimizerStore:
    """SQLite-backed store for skill versions and optimization runs."""

    def __init__(
        self,
        workspace: Path | str = ".",
        db_path: str = ".omni/optimizer.sqlite3",
        *,
        create: bool = True,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.db_path = self._safe_path(db_path)
        if create:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()

    def register_skill_version(self, version: SkillVersion) -> SkillVersion:
        errors = version.validate()
        if errors:
            raise ValueError("; ".join(errors))
        row_json = json.dumps(version.to_dict(), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_versions (
                    skill_id, version, domain, optimizer, status,
                    source_run_id, created_at, row_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(skill_id, version) DO UPDATE SET
                    domain = excluded.domain,
                    optimizer = excluded.optimizer,
                    status = excluded.status,
                    source_run_id = excluded.source_run_id,
                    row_json = excluded.row_json
                """,
                (
                    version.skill_id,
                    version.version,
                    version.domain,
                    version.optimizer,
                    version.status,
                    version.source_run_id,
                    version.created_at,
                    row_json,
                ),
            )
            conn.commit()
        return version

    def list_skill_versions(
        self,
        *,
        skill_id: str | None = None,
        limit: int = 50,
    ) -> list[SkillVersion]:
        if not self.db_path.exists():
            return []
        clauses: list[str] = []
        params: list[object] = []
        if skill_id:
            clauses.append("skill_id = ?")
            params.append(skill_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT row_json FROM skill_versions {where} "
                "ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [SkillVersion.from_dict(json.loads(row["row_json"])) for row in rows]

    def record_run(self, run: OptimizationRun) -> OptimizationRun:
        if not run.gate_decision:
            run.gate_decision = run.eval_gate.decide(
                split=run.dataset_split,
                holdout_metrics=run.holdout_metrics,
            )
        errors = run.validate()
        if errors:
            raise ValueError("; ".join(errors))
        row_json = json.dumps(run.to_dict(), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO optimization_runs (
                    run_id, skill_id, optimizer, from_version, to_version,
                    gate_decision, created_at, row_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    skill_id = excluded.skill_id,
                    optimizer = excluded.optimizer,
                    from_version = excluded.from_version,
                    to_version = excluded.to_version,
                    gate_decision = excluded.gate_decision,
                    row_json = excluded.row_json
                """,
                (
                    run.run_id,
                    run.skill_id,
                    run.optimizer,
                    run.from_version,
                    run.to_version,
                    run.gate_decision,
                    run.created_at,
                    row_json,
                ),
            )
            conn.commit()
        return run

    def list_runs(
        self,
        *,
        skill_id: str | None = None,
        limit: int = 50,
    ) -> list[OptimizationRun]:
        if not self.db_path.exists():
            return []
        clauses: list[str] = []
        params: list[object] = []
        if skill_id:
            clauses.append("skill_id = ?")
            params.append(skill_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT row_json FROM optimization_runs {where} "
                "ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [OptimizationRun.from_dict(json.loads(row["row_json"])) for row in rows]

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA busy_timeout = 30000;

                CREATE TABLE IF NOT EXISTS skill_versions (
                    skill_id      TEXT NOT NULL,
                    version       TEXT NOT NULL,
                    domain        TEXT NOT NULL,
                    optimizer     TEXT NOT NULL,
                    status        TEXT NOT NULL,
                    source_run_id TEXT NOT NULL DEFAULT '',
                    created_at    TEXT NOT NULL,
                    row_json      TEXT NOT NULL,
                    PRIMARY KEY (skill_id, version)
                );

                CREATE TABLE IF NOT EXISTS optimization_runs (
                    run_id        TEXT PRIMARY KEY,
                    skill_id      TEXT NOT NULL,
                    optimizer     TEXT NOT NULL,
                    from_version  TEXT NOT NULL,
                    to_version    TEXT NOT NULL,
                    gate_decision TEXT NOT NULL,
                    created_at    TEXT NOT NULL,
                    row_json      TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_optimizer_runs_skill
                    ON optimization_runs(skill_id, created_at DESC);
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        from .._storage import connect_sqlite_store
        return connect_sqlite_store(self.db_path)

    def _safe_path(self, relative_path: str) -> Path:
        from .._storage import safe_workspace_path
        return safe_workspace_path(self.workspace, relative_path)


def _float_map(value: Any) -> dict[str, float]:
    return {str(k): float(v) for k, v in dict(value or {}).items()}


__all__ = [
    "DatasetSplit",
    "EvalGate",
    "OptimizationRun",
    "OptimizerStore",
    "SkillVersion",
    "FAILED",
    "NEEDS_REVIEW",
    "PASSED",
]
