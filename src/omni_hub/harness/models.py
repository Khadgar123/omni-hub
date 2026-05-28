"""Contract dataclasses for the self-evolution harness.

These are the smallest possible shapes that let every later stage—ensemble
generation, judging, human preference, DSPy compile—read and write versioned
artifacts instead of loose chat history.

Design notes
------------
- `TaskPacket` is the *input contract*.  It carries the domain profile, the
  sources policy, the hard constraints, and the judge rubric.  Every
  `harness-*` CLI command starts by reading or building a `TaskPacket`.
- `GenerationRecord` is the *output contract*.  It stores the full ensemble of
  N candidates (because the user's chosen strategy is "no weight updates,
  multi-model voting"), plus judge scores, plus human feedback, plus a
  regression case suggestion.  Argilla, Graphiti, and Opik all consume this
  record.
- The dataclasses use `slots=True` for memory tightness and consistency with
  the existing `omni_hub.models` module.
- The classes serialize to plain dicts/JSON via `to_dict()`.  We deliberately
  avoid heavy validation libraries to keep the main repository dependency-free
  (see ``pyproject.toml`` — ``dependencies = []``).

Schema versioning
-----------------
The schema version is encoded in `TaskPacket.schema_version` and
`GenerationRecord.schema_version`.  Bump these when an incompatible field
change lands; never silently rename.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return str(uuid4())


# ---------------------------------------------------------------------------
# Input contract
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RetrievalPolicy:
    """How aggressively the agent must ground its output in sources."""

    must_search: bool = True
    min_sources: int = 3
    freshness_required: bool = False
    allowed_source_kinds: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Constraints:
    """Hard output constraints (these become judge dimensions, not regex)."""

    no_generic_claims: bool = True
    citation_required: bool = True
    preserve_uncertainty: bool = True
    max_words: int | None = None
    forbidden_phrases: list[str] = field(default_factory=list)


@dataclass(slots=True)
class JudgeRubric:
    """Weighted judge dimensions, sum should be ~1.0."""

    evidence_coverage: float = 0.30
    information_density: float = 0.25
    citation_support: float = 0.20
    style_fit: float = 0.10
    uncertainty_calibration: float = 0.15
    extras: dict[str, float] = field(default_factory=dict)

    def total_weight(self) -> float:
        base = (
            self.evidence_coverage
            + self.information_density
            + self.citation_support
            + self.style_fit
            + self.uncertainty_calibration
        )
        return base + sum(self.extras.values())


@dataclass(slots=True)
class TaskPacket:
    """Versioned input contract for a single harness task.

    Domain-specific behaviour is selected by ``domain_profile`` (a string id
    matching an entry in ``agent-harness/domain-profiles.json``).
    """

    schema_version: int = 1
    task_id: str = field(default_factory=_new_id)
    task_type: str = "engineering"
    domain_profile: str = "engineering"
    goal: str = ""
    audience: str = ""
    sources_required: list[str] = field(default_factory=list)
    sources_optional: list[str] = field(default_factory=list)
    retrieval_policy: RetrievalPolicy = field(default_factory=RetrievalPolicy)
    claims_to_cover: list[str] = field(default_factory=list)
    constraints: Constraints = field(default_factory=Constraints)
    positive_examples: list[str] = field(default_factory=list)
    negative_examples: list[str] = field(default_factory=list)
    judge_rubric: JudgeRubric = field(default_factory=JudgeRubric)
    human_review_required: bool = True
    created_at: str = field(default_factory=_utcnow_iso)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskPacket":
        # Re-hydrate nested dataclasses, tolerate missing keys (forward-compat)
        retrieval = data.get("retrieval_policy") or {}
        constraints = data.get("constraints") or {}
        rubric = data.get("judge_rubric") or {}
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            task_id=str(data.get("task_id") or _new_id()),
            task_type=str(data.get("task_type", "engineering")),
            domain_profile=str(data.get("domain_profile", "engineering")),
            goal=str(data.get("goal", "")),
            audience=str(data.get("audience", "")),
            sources_required=list(data.get("sources_required", [])),
            sources_optional=list(data.get("sources_optional", [])),
            retrieval_policy=RetrievalPolicy(**retrieval) if retrieval else RetrievalPolicy(),
            claims_to_cover=list(data.get("claims_to_cover", [])),
            constraints=Constraints(**constraints) if constraints else Constraints(),
            positive_examples=list(data.get("positive_examples", [])),
            negative_examples=list(data.get("negative_examples", [])),
            judge_rubric=JudgeRubric(**rubric) if rubric else JudgeRubric(),
            human_review_required=bool(data.get("human_review_required", True)),
            created_at=str(data.get("created_at") or _utcnow_iso()),
            notes=str(data.get("notes", "")),
        )

    def validate(self) -> list[str]:
        """Return a list of human-readable validation errors (empty == ok)."""

        errors: list[str] = []
        if not self.goal.strip():
            errors.append("goal must be non-empty")
        if not self.domain_profile.strip():
            errors.append("domain_profile must be set (see agent-harness/domain-profiles.json)")
        if self.retrieval_policy.min_sources < 0:
            errors.append("retrieval_policy.min_sources must be >= 0")
        weight = self.judge_rubric.total_weight()
        if not (0.95 <= weight <= 1.05):
            errors.append(
                f"judge_rubric weights should sum to ~1.0, got {weight:.3f}"
            )
        return errors


# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class JudgeScore:
    """One judge's structured score for one candidate.

    ``dimensions`` mirrors the ``JudgeRubric`` keys; missing entries are
    allowed (means the judge declined to score that dimension).
    """

    judge_id: str
    model: str
    dimensions: dict[str, float] = field(default_factory=dict)
    rationale: str = ""
    detected_biases: list[str] = field(default_factory=list)

    def weighted_total(self, rubric: JudgeRubric) -> float:
        weights = {
            "evidence_coverage": rubric.evidence_coverage,
            "information_density": rubric.information_density,
            "citation_support": rubric.citation_support,
            "style_fit": rubric.style_fit,
            "uncertainty_calibration": rubric.uncertainty_calibration,
            **rubric.extras,
        }
        return sum(
            float(self.dimensions.get(key, 0.0)) * weights.get(key, 0.0)
            for key in weights
        )


@dataclass(slots=True)
class Candidate:
    """One generation candidate from one model."""

    candidate_id: str = field(default_factory=_new_id)
    model: str = ""
    text: str = ""
    claim_evidence_map: list[dict[str, Any]] = field(default_factory=list)
    judge_scores: list[JudgeScore] = field(default_factory=list)
    failure_tags: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str | None = None


@dataclass(slots=True)
class HumanFeedback:
    """Human preference layer, mirrors what Argilla persists."""

    selected_candidate: str | None = None
    accepted_spans: list[str] = field(default_factory=list)
    rejected_spans: list[str] = field(default_factory=list)
    edit_diff: str = ""
    preference_reason: str = ""
    reviewer: str = "local-user"
    reviewed_at: str | None = None


@dataclass(slots=True)
class GenerationRecord:
    """Versioned output contract for one harness run."""

    schema_version: int = 1
    record_id: str = field(default_factory=_new_id)
    task_id: str = ""
    prompt_version: str = "v0"
    retrieval_snapshot: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    human_feedback: HumanFeedback | None = None
    regression_case_id: str | None = None
    created_at: str = field(default_factory=_utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def best_candidate_by_judge(
        self,
        rubric: JudgeRubric,
    ) -> Candidate | None:
        """Pick the candidate with the highest weighted-median judge total.

        Median (not mean) gives some robustness against an extreme outlier
        judge; per the design we never let a single judge be the ground
        truth.
        """

        scored: list[tuple[float, Candidate]] = []
        for candidate in self.candidates:
            if candidate.error or not candidate.judge_scores:
                continue
            totals = sorted(
                score.weighted_total(rubric) for score in candidate.judge_scores
            )
            if not totals:
                continue
            # weighted median
            n = len(totals)
            if n % 2 == 1:
                med = totals[n // 2]
            else:
                med = (totals[n // 2 - 1] + totals[n // 2]) / 2
            scored.append((med, candidate))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]
