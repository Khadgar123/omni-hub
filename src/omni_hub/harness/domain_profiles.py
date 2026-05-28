"""Domain profile loader + TaskPacket template generator.

Reads ``agent-harness/domain-profiles.json`` (the human-curated source of
truth for each domain's goal / required context / proposal rules / judge
dimensions) and exposes:

- ``load_all()`` returns ``dict[domain_id, DomainProfile]``
- ``get(domain_id)`` raises ``KeyError`` if unknown
- ``build_task_packet_template(domain_id)`` returns a starter ``TaskPacket``
  with judge rubric weighted across the domain's judge dimensions

The intent is that adding a new domain only requires editing
``domain-profiles.json``; no Python changes needed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .models import Constraints, JudgeRubric, RetrievalPolicy, TaskPacket


DEFAULT_PROFILE_PATH = Path("agent-harness/domain-profiles.json")


@dataclass(slots=True)
class DomainProfile:
    domain_id: str
    goal: str
    required_context: list[str] = field(default_factory=list)
    proposal_rules: list[str] = field(default_factory=list)
    judge_dimensions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "domain_id": self.domain_id,
            "goal": self.goal,
            "required_context": list(self.required_context),
            "proposal_rules": list(self.proposal_rules),
            "judge_dimensions": list(self.judge_dimensions),
        }


def load_all(path: Path | str = DEFAULT_PROFILE_PATH) -> dict[str, DomainProfile]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"domain profiles not found at {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    domains_raw = data.get("domains") or {}
    out: dict[str, DomainProfile] = {}
    for domain_id, body in domains_raw.items():
        out[domain_id] = DomainProfile(
            domain_id=domain_id,
            goal=str(body.get("goal", "")),
            required_context=list(body.get("required_context", [])),
            proposal_rules=list(body.get("proposal_rules", [])),
            judge_dimensions=list(body.get("judge_dimensions", [])),
        )
    return out


def get(domain_id: str, *, path: Path | str = DEFAULT_PROFILE_PATH) -> DomainProfile:
    profiles = load_all(path)
    if domain_id not in profiles:
        raise KeyError(f"unknown domain '{domain_id}'. known: {sorted(profiles)}")
    return profiles[domain_id]


def list_ids(path: Path | str = DEFAULT_PROFILE_PATH) -> list[str]:
    return sorted(load_all(path).keys())


# ---------------------------------------------------------------------------
# Template builder
# ---------------------------------------------------------------------------


# Domain-specific overrides — used when the generic mapping under-serves a
# domain.  Keep this map small; most domains can rely on the heuristic
# distribution below.
_DOMAIN_RUBRIC_OVERRIDES: dict[str, dict[str, float]] = {
    "finance": {
        "evidence_coverage": 0.30,        # data freshness
        "information_density": 0.20,
        "citation_support": 0.20,
        "style_fit": 0.10,
        "uncertainty_calibration": 0.20,  # mandatory risk disclosure
    },
    "policy": {
        "evidence_coverage": 0.30,
        "information_density": 0.20,
        "citation_support": 0.25,         # precise dates + source status
        "style_fit": 0.05,
        "uncertainty_calibration": 0.20,
    },
    "international_relations": {
        "evidence_coverage": 0.25,
        "information_density": 0.20,
        "citation_support": 0.20,
        "style_fit": 0.05,
        "uncertainty_calibration": 0.30,  # scenario ranges, not false certainty
    },
}


def _build_rubric_for(domain: DomainProfile) -> JudgeRubric:
    override = _DOMAIN_RUBRIC_OVERRIDES.get(domain.domain_id)
    if override:
        rubric = JudgeRubric(**override)
    else:
        rubric = JudgeRubric()  # balanced default

    # Map domain-specific judge_dimensions into the rubric.extras with small
    # equal weights so they show up in scoring but don't unbalance the core
    # five.  We renormalise at the end.
    extra_dimensions = [
        d for d in domain.judge_dimensions
        if d not in {
            "evidence_coverage", "information_density", "citation_support",
            "style_fit", "uncertainty_calibration",
        }
    ]
    if extra_dimensions:
        extras_weight = 0.10  # total contribution of all extras
        each = extras_weight / len(extra_dimensions)
        rubric.extras = {d: each for d in extra_dimensions}
        # Trim the core five proportionally to make room for extras
        scale = (1.0 - extras_weight) / max(
            1e-9,
            (
                rubric.evidence_coverage + rubric.information_density
                + rubric.citation_support + rubric.style_fit
                + rubric.uncertainty_calibration
            ),
        )
        rubric.evidence_coverage *= scale
        rubric.information_density *= scale
        rubric.citation_support *= scale
        rubric.style_fit *= scale
        rubric.uncertainty_calibration *= scale
    return rubric


def build_task_packet_template(
    domain_id: str,
    *,
    path: Path | str = DEFAULT_PROFILE_PATH,
    goal: str = "",
    audience: str = "",
) -> TaskPacket:
    """Return a fully populated TaskPacket *template* for ``domain_id``.

    Notes is set to the domain's proposal_rules so a human editing the JSON
    sees them as guidance.  ``claims_to_cover`` is intentionally empty —
    the human fills it in per task.
    """

    profile = get(domain_id, path=path)
    rubric = _build_rubric_for(profile)

    # Domain-flavoured retrieval policy: info-heavy domains require more
    # sources and freshness.
    info_heavy = domain_id in {"finance", "policy", "international_relations", "research"}
    retrieval = RetrievalPolicy(
        must_search=True,
        min_sources=4 if info_heavy else 3,
        freshness_required=info_heavy,
        allowed_source_kinds=list(profile.required_context),
    )

    constraints = Constraints(
        no_generic_claims=True,
        citation_required=True,
        preserve_uncertainty=True,
    )

    return TaskPacket(
        task_type=domain_id,
        domain_profile=domain_id,
        goal=goal or f"[ Fill in: concrete {domain_id} task goal ]",
        audience=audience,
        retrieval_policy=retrieval,
        constraints=constraints,
        judge_rubric=rubric,
        notes=" / ".join(profile.proposal_rules),
    )


def all_templates(path: Path | str = DEFAULT_PROFILE_PATH) -> Iterable[TaskPacket]:
    """Convenience: yield a starter template for every domain (CI sanity)."""

    for domain_id in list_ids(path):
        yield build_task_packet_template(domain_id, path=path)
