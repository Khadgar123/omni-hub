"""Judge Protocol + dataclasses (v0.23)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class JudgeRequest:
    """Input contract to ``Judge.evaluate``."""

    domain: str                         # one of the 19 domain slugs
    candidate: str                      # the answer being judged
    reference: str = ""                 # optional ground-truth / context
    rubric: dict[str, float] = field(default_factory=dict)
    # The 5 core dimensions from JudgeRubric (evidence_coverage,
    # information_density, citation_support, style_fit,
    # uncertainty_calibration) plus any domain-specific extras.
    metadata: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""                  # propagated from caller

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DimensionScore:
    """One rubric-dimension score (0..1) + a short rationale."""

    dimension: str
    score: float                        # clamp to [0.0, 1.0]
    weight: float = 1.0
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class JudgeVerdict:
    """Output contract.  ``composite`` is the weighted aggregate; per-
    dimension scores carry the breakdown for skill compilation."""

    judge_name: str
    domain: str
    composite: float                    # 0..1
    dimensions: list[DimensionScore] = field(default_factory=list)
    rationale: str = ""
    trace_id: str = ""
    evaluated_at: str = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["composite"] = round(self.composite, 4)
        data["dimensions"] = [d.to_dict() for d in self.dimensions]
        return data


class Judge(Protocol):
    """Contract for every Judge."""

    name: str

    def evaluate(self, request: JudgeRequest) -> JudgeVerdict: ...


def composite_score(dims: list[DimensionScore]) -> float:
    """Weighted average, ignoring zero-weight dimensions."""

    total_w = sum(d.weight for d in dims if d.weight > 0)
    if total_w <= 0:
        return 0.0
    return sum(d.score * d.weight for d in dims) / total_w


# ---------------------------------------------------------------------------
# Registry — simple dict so callers can pick a Judge by name.
# ---------------------------------------------------------------------------


class Judges:
    """Tiny registry for ``judge-evaluate --judge <name>``."""

    def __init__(self) -> None:
        self._judges: dict[str, Judge] = {}

    def register(self, judge: Judge) -> None:
        if judge.name in self._judges:
            raise ValueError(f"judge {judge.name!r} already registered")
        self._judges[judge.name] = judge

    def get(self, name: str) -> Judge:
        try:
            return self._judges[name]
        except KeyError as exc:
            raise KeyError(
                f"judge {name!r} not registered; known: {sorted(self._judges)}",
            ) from exc

    def names(self) -> list[str]:
        return sorted(self._judges)


__all__ = [
    "DimensionScore",
    "Judge",
    "JudgeRequest",
    "JudgeVerdict",
    "Judges",
    "composite_score",
]
