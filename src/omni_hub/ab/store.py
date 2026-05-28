"""SQLite-backed A/B test record store (v0.29).

Stores one row per :class:`ABTestVerdict` so subsequent runs can compute
win-rates per domain × variant.  WAL mode + busy_timeout to match the
project's stdlib SQLite conventions.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .runner import ABTestVerdict, Variant


AB_DB_REL = ".omni/ab_tests.sqlite3"


def _new_run_id() -> str:
    return f"ab_{int(time.time() * 1000):x}_{secrets.token_hex(4)}"


class ABTestStore:
    """Persistent log of A/B verdicts."""

    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace).resolve()
        self.db_path = self.workspace / AB_DB_REL
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ---- write -------------------------------------------------

    def new_run_id(self) -> str:
        return _new_run_id()

    def record(self, verdict: ABTestVerdict) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ab_runs "
                "(run_id, domain, judge_name, winner, delta, confidence_label, "
                " variant_a_label, variant_b_label, "
                " composite_a, composite_b, created_at, trace_id, verdict_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    verdict.run_id, verdict.domain, verdict.judge_name,
                    verdict.winner, verdict.delta, verdict.confidence_label,
                    verdict.a.label, verdict.b.label,
                    float(verdict.verdict_a.get("composite", 0.0)),
                    float(verdict.verdict_b.get("composite", 0.0)),
                    verdict.created_at, verdict.trace_id,
                    json.dumps(verdict.to_dict(), ensure_ascii=False),
                ),
            )
            conn.commit()

    # ---- read -------------------------------------------------

    def get(self, run_id: str) -> ABTestVerdict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT verdict_json FROM ab_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        data = json.loads(row["verdict_json"])
        # Reconstruct dataclass for type-stable callers.
        a = Variant(**data["a"])
        b = Variant(**data["b"])
        return ABTestVerdict(
            run_id=data["run_id"], domain=data["domain"],
            judge_name=data["judge_name"], a=a, b=b,
            verdict_a=data["verdict_a"], verdict_b=data["verdict_b"],
            winner=data["winner"], delta=data["delta"],
            confidence_label=data["confidence_label"],
            rationale=data["rationale"], created_at=data["created_at"],
            trace_id=data["trace_id"],
        )

    def list(
        self,
        *,
        domain: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT run_id, domain, judge_name, winner, delta, confidence_label, "
            "       variant_a_label, variant_b_label, composite_a, composite_b, "
            "       created_at "
            "FROM ab_runs "
        )
        params: tuple[Any, ...]
        if domain:
            sql += "WHERE domain = ? ORDER BY created_at DESC LIMIT ?"
            params = (domain, limit)
        else:
            sql += "ORDER BY created_at DESC LIMIT ?"
            params = (limit,)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def win_rate(self, *, domain: str | None = None) -> dict[str, Any]:
        """Aggregate winner counts; useful for ``ab-stats`` CLI."""

        sql = "SELECT winner, COUNT(*) AS n FROM ab_runs "
        params: tuple[Any, ...]
        if domain:
            sql += "WHERE domain = ? GROUP BY winner"
            params = (domain,)
        else:
            sql += "GROUP BY winner"
            params = ()
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        tally = {"a": 0, "b": 0, "tie": 0}
        for row in rows:
            tally[row["winner"]] = int(row["n"])
        total = sum(tally.values())
        return {
            "domain": domain,
            "total": total,
            "tally": tally,
            "rates": {
                k: (v / total) if total > 0 else 0.0
                for k, v in tally.items()
            },
        }

    # ---- schema -----------------------------------------------

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA busy_timeout = 30000;

                CREATE TABLE IF NOT EXISTS ab_runs (
                    run_id            TEXT PRIMARY KEY,
                    domain            TEXT NOT NULL,
                    judge_name        TEXT NOT NULL,
                    winner            TEXT NOT NULL,
                    delta             REAL NOT NULL,
                    confidence_label  TEXT NOT NULL,
                    variant_a_label   TEXT NOT NULL,
                    variant_b_label   TEXT NOT NULL,
                    composite_a       REAL NOT NULL DEFAULT 0,
                    composite_b       REAL NOT NULL DEFAULT 0,
                    created_at        TEXT NOT NULL,
                    trace_id          TEXT DEFAULT '',
                    verdict_json      TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ab_domain_created
                    ON ab_runs(domain, created_at DESC);
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        from .._storage import connect_sqlite_store
        return connect_sqlite_store(self.db_path)
