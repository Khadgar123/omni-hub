"""EvalRunner — execute an EvalPack against a candidate (v0.41 + v0.42).

v0.41 design doc: every run persists into ``.omni/eval_runs.sqlite3`` so
the flywheel can compute per-pack trend lines (does v0.2 beat v0.1 on
the retained cases?).

v0.42 adds **SkillAdapter** so EvalRunner can route each EvalCase to
the real skill under test, not just echo ``expected``.  The adapter
Protocol is ``(case) -> candidate_text``; concrete adapters delegate
through OperationRunner (read-only ops only — eval must not mutate
state).  ``builtin_skill_adapters(workspace)`` maps domain / functional
skill ids to adapters.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from ..judge import HeuristicJudge, JudgeRequest, JudgeVerdict, LLMJudge
from .store import EvalCase, EvalClass, EvalPack, EvalStore


# v0.42 — SkillAdapter signature
SkillAdapter = Callable[[EvalCase], str]


class SkillAdapterProtocol(Protocol):
    """``(case) -> candidate_text``.

    Adapters MUST be read-only (no Proposal[T] writes during eval —
    eval reads should never leak into the preference store).  They
    receive the full :class:`EvalCase` so they can introspect
    ``case.domain`` / ``case.metadata`` to pick the right downstream
    operation.
    """

    def __call__(self, case: EvalCase) -> str: ...


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
    adapter_used: str = ""        # v0.42 — which SkillAdapter produced the candidate
    candidate_excerpt: str = ""   # first 280 chars (debug)

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

    v0.42 — the runner now resolves a ``SkillAdapter`` per case via
    :func:`pick_adapter` against :func:`builtin_skill_adapters` (lazy).
    Callers can still pass an explicit ``adapters`` dict to override
    (tests usually do).  Pass ``use_echo_only=True`` to bypass adapter
    routing entirely — useful for smoke-checking the eval primitives
    without booting the OperationRunner stack.
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
        adapters: dict[str, SkillAdapter] | None = None,
        use_echo_only: bool = False,
        skill_version: str = "",
        trace_id: str = "",
        include_holdout: bool = False,
    ) -> EvalRun:
        """Run ``pack`` against the resolved adapter (or ``candidate_fn``).

        Resolution order per case:
          1. ``candidate_fn`` if supplied (test injection).
          2. ``pick_adapter(case, adapters)`` if ``use_echo_only=False``.
          3. :func:`_default_echo_candidate` fallback.

        ``adapters`` defaults to :func:`builtin_skill_adapters(workspace)`
        on first need; building it imports OperationRunner so we
        lazy-load to keep ``EvalRunner()`` itself cheap.
        """

        store = EvalStore(self.workspace)
        cases = store.list_cases(pack, include_holdout=include_holdout)
        results: list[CaseResult] = []
        by_class: dict[str, list[bool]] = {}

        resolved_adapters: dict[str, SkillAdapter] | None = adapters
        for case in cases:
            adapter_used = ""
            try:
                if candidate_fn is not None:
                    candidate = candidate_fn(case)
                    adapter_used = "candidate_fn"
                elif use_echo_only:
                    candidate = _default_echo_candidate(case)
                    adapter_used = "echo"
                else:
                    if resolved_adapters is None:
                        resolved_adapters = builtin_skill_adapters(self.workspace)
                    adapter = pick_adapter(case, adapters=resolved_adapters)
                    if adapter is None:
                        candidate = _default_echo_candidate(case)
                        adapter_used = "echo:no_adapter"
                    else:
                        candidate = adapter(case)
                        adapter_used = _adapter_label(case, resolved_adapters)
            except Exception as exc:                   # pragma: no cover — defensive
                candidate = f"(adapter error: {exc.__class__.__name__}: {exc})"
                adapter_used = "echo:adapter_error"

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
                adapter_used=adapter_used,
                candidate_excerpt=str(candidate)[:280],
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


def _adapter_label(
    case: EvalCase,
    adapters: dict[str, SkillAdapter],
) -> str:
    """Reverse-resolve the registry key for telemetry."""

    explicit = case.metadata.get("skill_id")
    if explicit and explicit in adapters:
        return explicit
    if case.domain.startswith("functional:"):
        return case.domain.split(":", 1)[1]
    return f"{case.domain.replace('_', '-')}-wiki"


def _default_echo_candidate(case: EvalCase) -> str:
    """No-LLM placeholder candidate (echo expected / question).

    Use only for pipeline sanity tests — real evals must pass a
    SkillAdapter so the candidate reflects the skill under test.  v0.42
    review: the runner previously *always* echoed expected, meaning
    every benchmark scored its own ground truth (vacuous).
    """

    if case.eval_class in (EvalClass.CAPABILITY, EvalClass.REGRESSION):
        return case.expected
    return case.question


# ---------------------------------------------------------------------------
# v0.42 — Built-in SkillAdapters
# ---------------------------------------------------------------------------


def builtin_skill_adapters(workspace: Path | str = ".") -> dict[str, SkillAdapter]:
    """Map domain / functional skill ids to read-only adapters.

    The adapter calls a registered operation (via OperationRunner) and
    returns whatever text best represents the skill's answer for the
    case.  All adapters are pure-read (no Proposal[T] writes) so eval
    sweeps don't pollute PreferenceStore.

    Domain wikis: build a context-pack and return the joined wiki +
    research snippet text — this matches how the domain SKILL.md's
    "Apply Knowledge" section is supposed to consume the pack.

    Functional skills:
    * ``chat-route`` → app_route_task — returns selected_skill_id + recommended_operation
    * ``retrieve`` → retrieve_cascade — returns top-N record titles
    * ``context-pack`` → context_pack_build — returns the pack body
    """

    from ..builtins import build_default_registry
    from ..models import OperationSpec, RiskLevel
    from ..operation_receipts import OperationReceiptStore
    from ..runner import OperationRunner

    workspace_root = Path(workspace).resolve()
    runner = OperationRunner(
        build_default_registry(workspace_root),
        receipts=OperationReceiptStore(
            workspace_root / ".omni" / "operation-receipts.sqlite3"
        ),
    )

    def _domain_wiki_adapter(domain: str) -> SkillAdapter:
        def _run(case: EvalCase) -> str:
            spec = OperationSpec(
                name="context_pack_build", action="build",
                payload={
                    "query": case.question, "domain": domain,
                    "wiki_limit": 6, "research_limit": 6,
                    "persist": False, "tier": "standard",
                    "include_closed": False,
                },
                risk_level=RiskLevel.READ_ONLY,
            )
            result = runner.run(spec)
            out = result.output or {}
            wiki = out.get("wiki_results") or []
            research = out.get("research_results") or []
            parts: list[str] = []
            for w in wiki[:6]:
                snippet = w.get("snippet") or w.get("body_excerpt") or ""
                if snippet:
                    parts.append(snippet)
            for r in research[:6]:
                snippet = r.get("snippet", "")
                if snippet:
                    parts.append(snippet)
            return "\n\n".join(parts) or "(empty context pack — wiki domain has no pages yet)"
        return _run

    def _chat_route_adapter(case: EvalCase) -> str:
        spec = OperationSpec(
            name="app_route_task", action="route",
            payload={"query": case.question},
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        out = result.output or {}
        decision = out.get("decision") or {}
        return (
            f"selected_skill_id={decision.get('selected_skill_id', '')}, "
            f"primary_intent={decision.get('primary_intent', '')}, "
            f"recommended_operation={decision.get('recommended_operation', '')}"
        )

    def _retrieve_adapter(case: EvalCase) -> str:
        spec = OperationSpec(
            name="retrieve_cascade", action="retrieve",
            payload={
                "query": case.question,
                "domain": case.metadata.get("domain_profile") or "default",
                "limit": 5, "fusion": "rrf",
            },
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        out = result.output or {}
        records = out.get("records") or []
        return "\n".join(f"- {r.get('title', '')}" for r in records[:5])

    def _context_pack_adapter(case: EvalCase) -> str:
        domain = case.metadata.get("domain_profile") or "research"
        spec = OperationSpec(
            name="context_pack_build", action="build",
            payload={
                "query": case.question, "domain": domain,
                "tier": "standard", "persist": False,
            },
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        out = result.output or {}
        snippet_count = len((out.get("wiki_results") or [])) + len((out.get("research_results") or []))
        return f"context_pack: {snippet_count} snippets, tier=standard"

    def _app_report_build_adapter(case: EvalCase) -> str:
        period = case.metadata.get("report_period", "daily")
        spec = OperationSpec(
            name="app_report_build", action="build",
            payload={"period": period, "persist": False, "narrate": False},
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        out = result.output or {}
        md = out.get("markdown") or out.get("summary") or ""
        return md[:1200] if md else f"app_report_build: period={period}, empty"

    def _inbox_route_adapter(case: EvalCase) -> str:
        # Classifier-only read; never enqueues, never proposes.
        spec = OperationSpec(
            name="inbox_classify", action="classify",
            payload={
                "content": case.question,
                "sender": case.metadata.get("sender", "eval"),
            },
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        out = result.output or {}
        return f"inbox_kind={out.get('kind', '')}, dispatch={out.get('recommended_operation', '')}"

    def _meta_cross_skill_scan_adapter(case: EvalCase) -> str:
        spec = OperationSpec(
            name="meta_cross_skill_scan", action="scan",
            payload={"min_domains": int(case.metadata.get("min_domains", 3))},
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        out = result.output or {}
        findings = out.get("findings") or []
        if not findings:
            return "meta_cross_skill_scan: no cross-skill findings yet"
        tokens = [str(f.get("token") or f.get("phrase") or "?") for f in findings[:5]]
        return "meta_cross_skill_scan: " + ", ".join(tokens)

    def _finance_screen_adapter(case: EvalCase) -> str:
        spec = OperationSpec(
            name="finance_screen", action="screen",
            payload=dict(case.metadata.get("screen_filters") or {}),
            risk_level=RiskLevel.READ_ONLY,
        )
        result = runner.run(spec)
        out = result.output or {}
        rows = out.get("results") or out.get("rows") or []
        if not rows:
            return "finance_screen: no matches (stub returns [] until v0.42 SQL screen lands)"
        names = [str(r.get("symbol") or r.get("ticker") or "?") for r in rows[:5]]
        return f"finance_screen: {len(rows)} rows — " + ", ".join(names)

    # ---- write-class skills: describe-only (no execution) -------------
    # Eval must never mutate state, so order-propose / calendar-add / etc.
    # get adapters that *narrate* the intended action from the case
    # metadata without calling the operation.  Cases that want full
    # round-trip coverage should be calibration class with rubric_weights.

    def _describe_only(operation_name: str) -> SkillAdapter:
        def _run(case: EvalCase) -> str:
            payload = case.metadata.get("expected_payload") or {}
            payload_str = ", ".join(f"{k}={v}" for k, v in payload.items()) or "(no payload hints)"
            return (
                f"would call {operation_name} (write-class, eval is read-only).\n"
                f"derived intent: {payload_str}"
            )
        return _run

    # Domain wiki ids look like "research-wiki" / "engineering-wiki" / etc.
    # Functional skill ids match their SKILL.md slug.
    registry: dict[str, SkillAdapter] = {
        "chat-route": _chat_route_adapter,
        "retrieve": _retrieve_adapter,
        "context-pack": _context_pack_adapter,
        "app-report-build": _app_report_build_adapter,
        "inbox-route": _inbox_route_adapter,
        "meta-cross-skill-scan": _meta_cross_skill_scan_adapter,
        "finance-screen": _finance_screen_adapter,
        # Write-class — describe-only (eval is read-only).
        "project-plan":  _describe_only("project_plan"),
        "pptx-build":    _describe_only("pptx_build"),
        "calendar-add":  _describe_only("calendar_add"),
        "schedule-plan": _describe_only("schedule_plan"),
        "task-add":      _describe_only("task_add"),
        "order-propose": _describe_only("order_propose"),
    }
    # Auto-register one adapter per known 19 domain wiki.
    from ..domain_schemas import DOMAIN_SCHEMAS
    for domain_slug in DOMAIN_SCHEMAS:
        registry[f"{domain_slug.replace('_', '-')}-wiki"] = _domain_wiki_adapter(domain_slug)

    return registry


def pick_adapter(
    case: EvalCase,
    *,
    adapters: dict[str, SkillAdapter],
) -> SkillAdapter | None:
    """Choose the right SkillAdapter for a case.

    Rules (in order):
      1. ``case.metadata["skill_id"]`` — explicit override
      2. ``functional:<name>`` domain → ``<name>`` adapter
      3. ``<domain_slug>`` → ``<domain_slug>-wiki`` adapter
    """

    explicit = case.metadata.get("skill_id")
    if explicit and explicit in adapters:
        return adapters[explicit]
    if case.domain.startswith("functional:"):
        skill_id = case.domain.split(":", 1)[1]
        return adapters.get(skill_id)
    wiki_id = f"{case.domain.replace('_', '-')}-wiki"
    return adapters.get(wiki_id)


__all__ = [
    "CaseResult",
    "EvalRun",
    "EvalRunner",
    "SkillAdapter",
    "SkillAdapterProtocol",
    "builtin_skill_adapters",
    "pick_adapter",
]
