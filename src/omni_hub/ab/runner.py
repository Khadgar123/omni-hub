"""A/B test runner (v0.29).

Given two candidate variants + reference + judge name, run the judge
against each, compute composite delta, and emit a verdict.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..judge import HeuristicJudge, JudgeRequest, JudgeVerdict, LLMJudge


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class Variant:
    """One side of the A/B."""

    label: str
    candidate: str
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ABTestVerdict:
    """The outcome of one A/B run."""

    run_id: str
    domain: str
    judge_name: str
    a: Variant
    b: Variant
    verdict_a: dict[str, Any]
    verdict_b: dict[str, Any]
    winner: str                       # "a" | "b" | "tie"
    delta: float                      # composite_b − composite_a
    confidence_label: str             # "decisive" | "moderate" | "marginal" | "tie"
    rationale: str = ""
    created_at: str = field(default_factory=_utcnow)
    trace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ABTestRunner:
    """Drives one A/B comparison."""

    def __init__(
        self,
        *,
        judge_name: str = "heuristic",
    ) -> None:
        self.judge_name = judge_name
        if judge_name == "llm":
            self._judge = LLMJudge()
        else:
            self._judge = HeuristicJudge()

    def run(
        self,
        *,
        run_id: str,
        domain: str,
        a: Variant,
        b: Variant,
        reference: str = "",
        rubric: dict[str, float] | None = None,
        trace_id: str = "",
    ) -> ABTestVerdict:
        rubric = rubric or {}

        req_a = JudgeRequest(
            domain=domain, candidate=a.candidate, reference=reference,
            rubric=rubric, trace_id=trace_id,
            metadata={"variant_label": a.label},
        )
        req_b = JudgeRequest(
            domain=domain, candidate=b.candidate, reference=reference,
            rubric=rubric, trace_id=trace_id,
            metadata={"variant_label": b.label},
        )

        verdict_a: JudgeVerdict = self._judge.evaluate(req_a)
        verdict_b: JudgeVerdict = self._judge.evaluate(req_b)

        delta = verdict_b.composite - verdict_a.composite
        winner, confidence_label = self._classify(delta)
        rationale = (
            f"Composite A={verdict_a.composite:.3f}, B={verdict_b.composite:.3f}, "
            f"Δ={delta:+.3f} ({confidence_label})."
        )
        return ABTestVerdict(
            run_id=run_id,
            domain=domain,
            judge_name=self.judge_name,
            a=a,
            b=b,
            verdict_a=verdict_a.to_dict(),
            verdict_b=verdict_b.to_dict(),
            winner=winner,
            delta=round(delta, 4),
            confidence_label=confidence_label,
            rationale=rationale,
            trace_id=trace_id,
        )

    @staticmethod
    def _classify(delta: float) -> tuple[str, str]:
        """Map composite delta → (winner_label, confidence_label).

        Thresholds are heuristic.  The Judge composite is in [0, 1], so:

        * |Δ| < 0.03 → tie (within noise floor)
        * |Δ| < 0.10 → marginal — needs more samples to trust
        * |Δ| < 0.20 → moderate
        *          ≥ 0.20 → decisive
        """

        abs_d = abs(delta)
        if abs_d < 0.03:
            return "tie", "tie"
        winner = "b" if delta > 0 else "a"
        if abs_d < 0.10:
            return winner, "marginal"
        if abs_d < 0.20:
            return winner, "moderate"
        return winner, "decisive"


__all__ = ["ABTestRunner", "ABTestVerdict", "Variant"]
