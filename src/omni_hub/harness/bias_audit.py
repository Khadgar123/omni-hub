"""LLM-as-judge bias audit (FairJudge / BiasScope inspired, stdlib only).

The harness deliberately tracks the five judge biases recognised by 2026
literature:

    1. position_bias        — preference depending on candidate order
    2. verbosity_bias       — preference for longer answers
    3. self_preference_bias — judge favouring outputs from the same model
                              family
    4. format_bias          — preference for markdown / list / structured
                              presentation regardless of substance
    5. calibration_drift    — scores cluster (all-high or all-low)

We compute these *structurally* from already-collected ``JudgeScore`` data —
no extra LLM calls — so this module can be run as a CI gate after every
``harness-judge`` run.

The output is a ``BiasAuditReport`` with per-bias severity in [0,1].  Anything
above ``severity_threshold`` (default 0.4) goes into
``Candidate.judge_scores[*].detected_biases`` as a tag and gets logged into
the GenerationRecord for later inspection.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Sequence

from .models import Candidate, GenerationRecord, JudgeScore


@dataclass(slots=True)
class BiasFinding:
    bias: str
    severity: float
    detail: str


@dataclass(slots=True)
class BiasAuditReport:
    findings: list[BiasFinding] = field(default_factory=list)
    severity_threshold: float = 0.4

    def to_dict(self) -> dict:
        return {
            "severity_threshold": self.severity_threshold,
            "findings": [
                {"bias": f.bias, "severity": f.severity, "detail": f.detail}
                for f in self.findings
            ],
        }

    def tags_above_threshold(self) -> list[str]:
        return [
            f.bias for f in self.findings if f.severity >= self.severity_threshold
        ]


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _all_judge_totals(record: GenerationRecord) -> list[tuple[Candidate, JudgeScore, float]]:
    out: list[tuple[Candidate, JudgeScore, float]] = []
    for cand in record.candidates:
        for js in cand.judge_scores:
            out.append((cand, js, sum(js.dimensions.values()) / max(1, len(js.dimensions))))
    return out


# ---------------------------------------------------------------------------
# Individual bias detectors
# ---------------------------------------------------------------------------


def _position_bias(record: GenerationRecord) -> BiasFinding:
    """If every judge consistently scores the first candidate higher than the
    last, position bias is high."""

    if len(record.candidates) < 2:
        return BiasFinding("position_bias", 0.0, "fewer than 2 candidates")

    deltas: list[float] = []
    for js_index in range(max((len(c.judge_scores) for c in record.candidates), default=0)):
        first_score = _judge_total(record.candidates[0], js_index)
        last_score = _judge_total(record.candidates[-1], js_index)
        if first_score is None or last_score is None:
            continue
        deltas.append(first_score - last_score)

    if not deltas:
        return BiasFinding("position_bias", 0.0, "no comparable judge scores")
    avg = _mean(deltas)
    severity = min(1.0, abs(avg) * 2.0)  # ~0.5 mean delta -> severity 1.0
    return BiasFinding(
        "position_bias",
        severity,
        f"avg(first-last) judge score = {avg:+.3f}",
    )


def _verbosity_bias(record: GenerationRecord) -> BiasFinding:
    """Pearson-ish correlation between candidate text length and judge total."""

    pairs: list[tuple[int, float]] = []
    for cand in record.candidates:
        length = len(cand.text)
        for js in cand.judge_scores:
            total = sum(js.dimensions.values()) / max(1, len(js.dimensions))
            pairs.append((length, total))
    if len(pairs) < 3:
        return BiasFinding("verbosity_bias", 0.0, "not enough samples")
    lengths = [p[0] for p in pairs]
    totals = [p[1] for p in pairs]
    try:
        r = _pearson(lengths, totals)
    except statistics.StatisticsError:
        return BiasFinding("verbosity_bias", 0.0, "constant series")
    severity = max(0.0, r)  # positive correlation == bias
    return BiasFinding(
        "verbosity_bias", min(1.0, severity), f"pearson(length, score) = {r:+.3f}"
    )


def _self_preference_bias(record: GenerationRecord) -> BiasFinding:
    """If a judge's model and a candidate's model share a family root
    (e.g. ``claude``, ``deepseek``, ``codex``) and that judge consistently
    awards above-median scores to those candidates, flag self-preference."""

    family_pairs_above: int = 0
    family_pairs_total: int = 0

    judge_totals_by_id: dict[str, list[float]] = {}
    for cand in record.candidates:
        for js in cand.judge_scores:
            judge_totals_by_id.setdefault(js.judge_id, []).append(
                sum(js.dimensions.values()) / max(1, len(js.dimensions))
            )
    medians = {jid: statistics.median(values) for jid, values in judge_totals_by_id.items() if values}

    for cand in record.candidates:
        cand_family = _family(cand.model)
        for js in cand.judge_scores:
            judge_family = _family(js.model)
            if cand_family and cand_family == judge_family:
                family_pairs_total += 1
                total = sum(js.dimensions.values()) / max(1, len(js.dimensions))
                if total > medians.get(js.judge_id, total):
                    family_pairs_above += 1
    if family_pairs_total == 0:
        return BiasFinding("self_preference_bias", 0.0, "no same-family judge/candidate pairs")
    ratio = family_pairs_above / family_pairs_total
    severity = max(0.0, (ratio - 0.5) * 2.0)
    return BiasFinding(
        "self_preference_bias",
        min(1.0, severity),
        f"{family_pairs_above}/{family_pairs_total} same-family pairs above median",
    )


def _format_bias(record: GenerationRecord) -> BiasFinding:
    """Correlation between markdown structure density and judge total."""

    pairs: list[tuple[float, float]] = []
    for cand in record.candidates:
        text = cand.text
        markdown_density = (
            text.count("\n- ")
            + text.count("\n* ")
            + text.count("\n#")
            + text.count("```")
            + text.count("**")
        ) / max(1, len(text)) * 1000.0
        for js in cand.judge_scores:
            total = sum(js.dimensions.values()) / max(1, len(js.dimensions))
            pairs.append((markdown_density, total))
    if len(pairs) < 3:
        return BiasFinding("format_bias", 0.0, "not enough samples")
    try:
        r = _pearson([p[0] for p in pairs], [p[1] for p in pairs])
    except statistics.StatisticsError:
        return BiasFinding("format_bias", 0.0, "constant series")
    severity = max(0.0, r)
    return BiasFinding(
        "format_bias", min(1.0, severity),
        f"pearson(markdown_density, score) = {r:+.3f}"
    )


def _calibration_drift(record: GenerationRecord) -> BiasFinding:
    """Low variance across all judge totals = drift (everyone agrees too easily)."""

    totals: list[float] = []
    for cand in record.candidates:
        for js in cand.judge_scores:
            totals.append(sum(js.dimensions.values()) / max(1, len(js.dimensions)))
    if len(totals) < 3:
        return BiasFinding("calibration_drift", 0.0, "not enough samples")
    stdev = statistics.pstdev(totals)
    # If stdev <0.05 we suspect drift; severity scales inversely
    severity = max(0.0, min(1.0, 1.0 - stdev / 0.15))
    return BiasFinding(
        "calibration_drift", severity, f"pstdev across judge totals = {stdev:.3f}"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _judge_total(candidate: Candidate, judge_index: int) -> float | None:
    if judge_index >= len(candidate.judge_scores):
        return None
    js = candidate.judge_scores[judge_index]
    return sum(js.dimensions.values()) / max(1, len(js.dimensions))


def _family(model: str) -> str:
    if not model:
        return ""
    lower = model.lower()
    for key in ("claude", "anthropic", "deepseek", "codex", "gpt", "openai", "gemini", "qwen", "llama", "mistral"):
        if key in lower:
            return key
    return lower.split("-")[0]


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 2:
        raise statistics.StatisticsError("inputs differ or too short")
    mx = _mean(xs)
    my = _mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = (
        (sum((x - mx) ** 2 for x in xs) ** 0.5)
        * (sum((y - my) ** 2 for y in ys) ** 0.5)
    )
    if den == 0:
        raise statistics.StatisticsError("zero variance")
    return num / den


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def audit(
    record: GenerationRecord,
    *,
    severity_threshold: float = 0.4,
    annotate_record: bool = True,
) -> BiasAuditReport:
    """Run all five bias detectors over a GenerationRecord.

    If ``annotate_record`` is True, biases above threshold are appended to each
    ``JudgeScore.detected_biases`` list so the data flywheel keeps the audit
    next to the original judgement.
    """

    findings = [
        _position_bias(record),
        _verbosity_bias(record),
        _self_preference_bias(record),
        _format_bias(record),
        _calibration_drift(record),
    ]
    report = BiasAuditReport(findings=findings, severity_threshold=severity_threshold)
    if annotate_record:
        tags = report.tags_above_threshold()
        if tags:
            for cand in record.candidates:
                for js in cand.judge_scores:
                    for tag in tags:
                        if tag not in js.detected_biases:
                            js.detected_biases.append(tag)
    return report
