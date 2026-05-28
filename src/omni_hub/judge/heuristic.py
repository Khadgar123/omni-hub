"""Stdlib-only heuristic Judge (v0.23).

Five-dimension scorer matching :class:`omni_hub.harness.models.JudgeRubric`:

* ``evidence_coverage``     — # distinct ``[N]`` citation markers / 10
* ``information_density``   — type-token ratio capped at 0.6
* ``citation_support``      — fraction of paragraphs ending with a citation
* ``style_fit``             — heading + bullet density vs reference (0.5 floor)
* ``uncertainty_calibration``— presence of hedge / uncertainty words

The composite uses the weights from the caller-supplied rubric (or
balanced defaults).  This Judge is **deterministic** — same input →
same verdict — so it's safe to use in regression tests and as the
"first-pass auto-grader" before the more expensive LLMJudge.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from .base import DimensionScore, Judge, JudgeRequest, JudgeVerdict, composite_score


_CITATION_PATTERN = re.compile(r"\[(\d+)\]")
_HEDGE_TOKENS = (
    "may", "might", "could", "likely", "unlikely", "unclear",
    "possibly", "preliminary", "suggests", "appears", "约", "也许", "可能",
    "似乎", "不确定", "待进一步", "ranging", "between", "approximately",
)
_PARA_TERMINATOR = re.compile(r"[\n。.!?！？]")


_DEFAULT_RUBRIC: dict[str, float] = {
    "evidence_coverage": 0.30,
    "information_density": 0.20,
    "citation_support": 0.20,
    "style_fit": 0.15,
    "uncertainty_calibration": 0.15,
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[\w一-鿿]+", text)]


def _distinct_citations(text: str) -> int:
    return len({m.group(1) for m in _CITATION_PATTERN.finditer(text)})


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def _heading_bullet_density(text: str) -> float:
    if not text.strip():
        return 0.0
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    structured = sum(
        1 for ln in lines
        if ln.lstrip().startswith(("#", "-", "*", "1.", "2.", "3.", "4.", "5."))
    )
    return _clamp01(structured / len(lines))


def _type_token_ratio(text: str) -> float:
    tokens = _tokens(text)
    if not tokens:
        return 0.0
    return min(0.6, len(set(tokens)) / len(tokens)) / 0.6


def _hedge_score(text: str) -> float:
    lower = text.lower()
    hits = sum(1 for tok in _HEDGE_TOKENS if tok in lower)
    # 0 hedges → 0.0;  1-2 → balanced;  >5 → over-hedged (cap 1.0).
    if hits == 0:
        return 0.0
    if hits <= 2:
        return 0.6
    if hits <= 4:
        return 0.9
    return 0.7


class HeuristicJudge:
    """Deterministic 5-dimension scorer."""

    name = "heuristic"

    def evaluate(self, request: JudgeRequest) -> JudgeVerdict:
        rubric = {**_DEFAULT_RUBRIC, **(request.rubric or {})}
        candidate = request.candidate or ""

        # ---- dimension scores ------------------------------------
        n_citations = _distinct_citations(candidate)
        coverage = _clamp01(n_citations / 10.0)
        density = _type_token_ratio(candidate)
        paragraphs = _paragraphs(candidate)
        cited_paragraphs = sum(1 for p in paragraphs if _CITATION_PATTERN.search(p))
        citation_support = _clamp01(cited_paragraphs / max(len(paragraphs), 1))
        style = _heading_bullet_density(candidate)
        if request.reference:
            ref_style = _heading_bullet_density(request.reference)
            # Reward matching the reference's style profile.
            style = _clamp01(1.0 - abs(style - ref_style))
        else:
            style = max(0.5, style)
        uncertainty = _hedge_score(candidate)

        dims = [
            DimensionScore(
                dimension="evidence_coverage", score=coverage,
                weight=rubric.get("evidence_coverage", 0.30),
                rationale=f"{n_citations} distinct citation markers (target 10).",
            ),
            DimensionScore(
                dimension="information_density", score=density,
                weight=rubric.get("information_density", 0.20),
                rationale="Type-token ratio (cap 0.6) ~ vocabulary breadth.",
            ),
            DimensionScore(
                dimension="citation_support", score=citation_support,
                weight=rubric.get("citation_support", 0.20),
                rationale=(
                    f"{cited_paragraphs}/{len(paragraphs)} paragraphs end with a "
                    "citation marker."
                ),
            ),
            DimensionScore(
                dimension="style_fit", score=style,
                weight=rubric.get("style_fit", 0.15),
                rationale="Heading + bullet density vs reference.",
            ),
            DimensionScore(
                dimension="uncertainty_calibration", score=uncertainty,
                weight=rubric.get("uncertainty_calibration", 0.15),
                rationale="Presence of hedge / uncertainty markers.",
            ),
        ]
        # Any extras the caller passed in get a unit-weight pass-through 0.5.
        known = {d.dimension for d in dims}
        for extra_name, extra_weight in rubric.items():
            if extra_name not in known:
                dims.append(DimensionScore(
                    dimension=extra_name, score=0.5,
                    weight=extra_weight,
                    rationale="Heuristic Judge has no signal for this dimension; "
                              "use LLMJudge for richer scoring.",
                ))
        composite = composite_score(dims)
        return JudgeVerdict(
            judge_name=self.name,
            domain=request.domain,
            composite=composite,
            dimensions=dims,
            rationale=(
                f"Composite {composite:.2f} from {len(dims)} dimensions. "
                f"Lengths: candidate={len(candidate)} chars, "
                f"reference={len(request.reference)} chars."
            ),
            trace_id=request.trace_id,
        )


__all__ = ["HeuristicJudge"]
