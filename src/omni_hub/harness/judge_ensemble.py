"""Multi-judge scoring over a GenerationRecord.

Design rules:

- Multiple judges (different models / families) score the same candidate to
  avoid single-judge tyranny.
- Each judge returns a structured ``JudgeScore`` keyed by
  ``JudgeRubric.dimensions``.
- A heuristic ``LocalHeuristicJudge`` is provided so the harness can run
  *without any external LLM*; it scores structurally on grounding/length/etc.
  Tests use it; production swaps in real LLM judges via ``LLMJudge``.
- After all judges run we call ``bias_audit.audit`` to detect 5-dimension
  judge biases.
- The selected winner uses ``GenerationRecord.best_candidate_by_judge`` (the
  weighted-median already implemented in ``models.py``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

from . import bias_audit, grounding
from .ensemble import ModelSpec, _build_request, _default_secret_resolver
from .models import Candidate, GenerationRecord, JudgeRubric, JudgeScore


# ---------------------------------------------------------------------------
# Judge protocol — anything implementing ``score(candidate, packet_rubric)``
# can play.  Two implementations ship: local heuristic + LLM-via-ccLoad.
# ---------------------------------------------------------------------------


class JudgeBase:
    judge_id: str
    model: str

    def __init__(self, judge_id: str, model: str) -> None:
        self.judge_id = judge_id
        self.model = model

    def score(self, candidate: Candidate, rubric: JudgeRubric) -> JudgeScore:  # pragma: no cover - abstract
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Local heuristic judge — scores grounded text without any LLM call
# ---------------------------------------------------------------------------


class LocalHeuristicJudge(JudgeBase):
    """No-LLM judge built from ``grounding`` analysis.

    Useful as a baseline + as the offline judge in CI.  Scores are not great
    in absolute terms but they're cheap, deterministic, and bias-free.
    """

    def score(self, candidate: Candidate, rubric: JudgeRubric) -> JudgeScore:
        report = grounding.analyze_grounding(candidate.text)
        # Each dimension is mapped to a structural proxy in [0,1].
        dims = {
            "evidence_coverage": min(1.0, report.citation_density + 0.1)
            if report.total_claims else 0.0,
            "information_density": report.nugget_density,
            "citation_support": report.citation_density,
            "style_fit": _style_fit_proxy(candidate.text),
            "uncertainty_calibration": _uncertainty_proxy(candidate.text),
        }
        rationale = (
            f"local heuristic: {report.cited_claims}/{report.total_claims} cited, "
            f"{report.total_claims - report.low_signal_claims} informative claims"
        )
        return JudgeScore(
            judge_id=self.judge_id,
            model=self.model,
            dimensions=dims,
            rationale=rationale,
        )


def _style_fit_proxy(text: str) -> float:
    if not text:
        return 0.0
    length = len(text)
    if length < 50:
        return 0.3
    if length > 4000:
        return 0.4
    sentences = max(1, text.count(".") + text.count("。"))
    avg_sentence_len = length / sentences
    if 50 <= avg_sentence_len <= 200:
        return 0.85
    return 0.6


def _uncertainty_proxy(text: str) -> float:
    """Boost when the text qualifies claims; penalise absolute language."""

    if not text:
        return 0.0
    hedges = sum(
        text.lower().count(w)
        for w in (
            " may ", " might ", " suggest", " indicate", " likely",
            "可能", "或许", "倾向于", "尚不确定",
        )
    )
    absolutes = sum(
        text.lower().count(w)
        for w in (
            " always ", " never ", " obviously ", " clearly ", " undoubtedly ",
            "必然", "肯定", "显然",
        )
    )
    score = 0.5 + 0.05 * hedges - 0.07 * absolutes
    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# LLM judge — calls a real model via ccLoad and parses a JSON response
# ---------------------------------------------------------------------------


_JUDGE_INSTRUCTION = (
    "You are an evaluation judge. Score the candidate on each dimension "
    "in [0.0, 1.0]. Respond ONLY with compact JSON of shape "
    '{"dimensions": {"evidence_coverage": 0.0, "information_density": 0.0, '
    '"citation_support": 0.0, "style_fit": 0.0, "uncertainty_calibration": 0.0}, '
    '"rationale": "..."}.  No prose outside the JSON.'
)


class LLMJudge(JudgeBase):
    """Calls a real model through ccLoad.  Same plumbing as ``ensemble._call_one``
    but with a judge-specific system prompt."""

    def __init__(
        self,
        judge_id: str,
        spec: ModelSpec,
        *,
        secret_resolver: Callable[[str], str] | None = None,
        http_call: Callable | None = None,
    ) -> None:
        super().__init__(judge_id, spec.name)
        self.spec = spec
        self._resolver = secret_resolver or _default_secret_resolver
        self._http = http_call

    def score(self, candidate: Candidate, rubric: JudgeRubric) -> JudgeScore:
        prompt = (
            f"<rubric>{json.dumps({k: getattr(rubric, k) for k in ('evidence_coverage','information_density','citation_support','style_fit','uncertainty_calibration')})}</rubric>\n"
            f"<candidate>{candidate.text}</candidate>"
        )
        request = _build_request(self.spec, prompt, _JUDGE_INSTRUCTION, self._resolver)
        try:
            if self._http is not None:
                payload = self._http(request, self.spec.timeout_seconds)
            else:
                from urllib.request import urlopen
                with urlopen(request, timeout=self.spec.timeout_seconds) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            return JudgeScore(
                judge_id=self.judge_id,
                model=self.model,
                rationale=f"judge call failed: {exc}",
            )

        content = ""
        choices = payload.get("choices") or []
        if choices:
            content = (choices[0].get("message") or {}).get("content", "") or ""

        dims, rationale = _parse_judge_json(content)
        return JudgeScore(
            judge_id=self.judge_id,
            model=self.model,
            dimensions=dims,
            rationale=rationale or "(no rationale)",
        )


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def _parse_judge_json(content: str) -> tuple[dict[str, float], str]:
    if not content:
        return {}, "empty response"
    # First try strict parse, then fall back to extracting the largest JSON block.
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(content)
        if not match:
            return {}, f"unparseable: {content[:120]}"
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}, f"unparseable: {content[:120]}"
    raw = data.get("dimensions") or {}
    dims = {str(k): float(v) for k, v in raw.items() if _is_finite(v)}
    return dims, str(data.get("rationale", ""))


def _is_finite(value) -> bool:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    return f == f and abs(f) < float("inf")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class JudgeEnsembleResult:
    record: GenerationRecord
    bias_report: bias_audit.BiasAuditReport = field(
        default_factory=lambda: bias_audit.BiasAuditReport(findings=[])
    )
    winner_candidate_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "record": self.record.to_dict(),
            "bias_report": self.bias_report.to_dict(),
            "winner_candidate_id": self.winner_candidate_id,
        }


def run_judges(
    record: GenerationRecord,
    judges: Iterable[JudgeBase],
    rubric: JudgeRubric,
    *,
    audit_severity_threshold: float = 0.4,
) -> JudgeEnsembleResult:
    """Score every candidate with every judge; run bias audit; pick winner."""

    judges_list = list(judges)
    if not judges_list:
        raise ValueError("at least one judge required")

    for cand in record.candidates:
        if cand.error:
            continue
        for judge in judges_list:
            cand.judge_scores.append(judge.score(cand, rubric))

    report = bias_audit.audit(
        record, severity_threshold=audit_severity_threshold, annotate_record=True
    )
    winner = record.best_candidate_by_judge(rubric)
    return JudgeEnsembleResult(
        record=record,
        bias_report=report,
        winner_candidate_id=winner.candidate_id if winner else None,
    )
