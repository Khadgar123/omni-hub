from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .models import RiskLevel
from .skills import SkillKind, SkillSpec, SkillStatus


@dataclass(slots=True)
class SkillQuality:
    score: float
    grade: str
    signals: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["score"] = round(self.score, 4)
        return data


@dataclass(slots=True)
class SkillRecommendation:
    skill_id: str
    name: str
    kind: str
    score: float
    risk_level: str
    status: str
    quality: SkillQuality
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["score"] = round(self.score, 4)
        data["quality"]["score"] = round(self.quality.score, 4)
        return data


@dataclass(slots=True)
class SkillConflict:
    severity: str
    skill_ids: list[str]
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class SkillSetAnalysis:
    skill_ids: list[str]
    total_risk: int
    max_risk_level: str
    connectors: list[str]
    permissions: list[str]
    skill_quality: dict[str, SkillQuality] = field(default_factory=dict)
    conflicts: list[SkillConflict] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["skill_quality"] = {
            skill_id: quality.to_dict()
            for skill_id, quality in self.skill_quality.items()
        }
        data["conflicts"] = [conflict.to_dict() for conflict in self.conflicts]
        return data


def recommend_skills(
    skills: list[SkillSpec],
    query: str,
    *,
    limit: int = 10,
    max_risk: RiskLevel | None = None,
    include_disabled: bool = False,
) -> list[SkillRecommendation]:
    terms = _query_terms(query)
    recommendations: list[SkillRecommendation] = []

    for skill in skills:
        if not include_disabled and skill.status in {
            SkillStatus.DISABLED,
            SkillStatus.DEPRECATED,
        }:
            continue
        if max_risk is not None and skill.risk_level > max_risk:
            continue

        score, reasons = _score_skill(skill, terms)
        quality = evaluate_skill_quality(skill)
        warnings = _unique(_skill_warnings(skill) + quality.issues)
        if terms and not reasons:
            continue

        score += quality.score * 0.5
        score -= _risk_penalty(skill.risk_level)
        if skill.status == SkillStatus.DRAFT:
            score -= 0.4
            warnings.append("draft skill; review before enabling")
        elif skill.status == SkillStatus.DEPRECATED:
            score -= 1.0
            warnings.append("deprecated skill")
        elif skill.status == SkillStatus.DISABLED:
            score -= 1.5
            warnings.append("disabled skill")

        recommendations.append(
            SkillRecommendation(
                skill_id=skill.skill_id,
                name=skill.name,
                kind=skill.kind.value,
                score=max(score, 0.0),
                risk_level=skill.risk_level.code,
                status=skill.status.value,
                quality=quality,
                reasons=reasons,
                warnings=_unique(warnings),
            )
        )

    recommendations.sort(key=lambda item: (-item.score, item.skill_id))
    return recommendations[:limit]


def analyze_skill_set(skills: list[SkillSpec]) -> SkillSetAnalysis:
    connectors = sorted({connector for skill in skills for connector in skill.connectors})
    permissions = sorted(
        {permission for skill in skills for permission in skill.required_permissions}
    )
    total_risk = sum(int(skill.risk_level) for skill in skills)
    max_risk = max((skill.risk_level for skill in skills), default=RiskLevel.READ_ONLY)
    conflicts = _find_conflicts(skills)

    return SkillSetAnalysis(
        skill_ids=[skill.skill_id for skill in skills],
        total_risk=total_risk,
        max_risk_level=max_risk.code,
        connectors=connectors,
        permissions=permissions,
        skill_quality={
            skill.skill_id: evaluate_skill_quality(skill) for skill in skills
        },
        conflicts=conflicts,
    )


def evaluate_skill_quality(skill: SkillSpec) -> SkillQuality:
    score = 0.0
    signals: list[str] = []
    issues: list[str] = []

    if skill.status == SkillStatus.ACTIVE:
        score += 0.2
        signals.append("active status")
    elif skill.status == SkillStatus.DRAFT:
        score += 0.05
        issues.append("draft status")
    else:
        issues.append(f"{skill.status.value} status")

    if skill.entrypoint:
        score += 0.15
        signals.append("entrypoint declared")
    else:
        issues.append("missing entrypoint")

    if len(skill.description.strip()) >= 40:
        score += 0.15
        signals.append("descriptive summary")
    else:
        issues.append("description is too short")

    if skill.tags:
        score += 0.1
        signals.append("tags declared")
    else:
        issues.append("missing tags")

    if skill.inputs and skill.outputs:
        score += 0.15
        signals.append("input/output contract declared")
    else:
        issues.append("missing input/output contract")

    if skill.source_path:
        score += 0.1
        signals.append("source path declared")
    else:
        issues.append("missing source path")

    if skill.kind == SkillKind.CONNECTOR:
        if skill.connectors:
            score += 0.1
            signals.append("connector scope declared")
        else:
            issues.append("connector skill missing connector scope")
    else:
        score += 0.05

    if skill.risk_level <= RiskLevel.LOCAL_WRITE:
        score += 0.1
        signals.append("low local risk")
    else:
        issues.append("high-risk skill requires approval boundary")

    score = min(score, 1.0)
    return SkillQuality(
        score=score,
        grade=_quality_grade(score),
        signals=signals,
        issues=issues,
    )


def _score_skill(skill: SkillSpec, terms: list[str]) -> tuple[float, list[str]]:
    haystacks = {
        "id": skill.skill_id,
        "name": skill.name,
        "description": skill.description,
        "kind": skill.kind.value,
        "tags": " ".join(skill.tags),
        "connectors": " ".join(skill.connectors),
        "entrypoint": skill.entrypoint,
    }

    weights = {
        "id": 2.0,
        "name": 2.5,
        "description": 1.5,
        "kind": 1.0,
        "tags": 2.0,
        "connectors": 1.5,
        "entrypoint": 0.8,
    }

    score = 0.0
    reasons: list[str] = []
    for term in terms:
        for field_name, value in haystacks.items():
            if term in value.lower():
                score += weights[field_name]
                reasons.append(f"matched {field_name}: {term}")
                break

    if not terms and skill.status == SkillStatus.ACTIVE:
        score += 0.5
        reasons.append("active skill")

    if skill.status == SkillStatus.ACTIVE:
        score += 0.4
    if skill.risk_level <= RiskLevel.LOCAL_WRITE:
        score += 0.2

    return score, reasons


def _skill_warnings(skill: SkillSpec) -> list[str]:
    warnings: list[str] = []
    if skill.risk_level >= RiskLevel.EXTERNAL_PUBLISH:
        warnings.append("external publish or higher risk requires approval")
    elif skill.risk_level == RiskLevel.EXTERNAL_SEND:
        warnings.append("external send should be allowlisted or approved")
    if skill.required_permissions:
        warnings.append("requires permissions: " + ", ".join(skill.required_permissions))
    if not skill.entrypoint:
        warnings.append("missing entrypoint")
    return warnings


def _find_conflicts(skills: list[SkillSpec]) -> list[SkillConflict]:
    conflicts: list[SkillConflict] = []
    by_entrypoint: dict[str, list[str]] = {}
    by_connector: dict[str, list[SkillSpec]] = {}

    for skill in skills:
        if skill.entrypoint:
            by_entrypoint.setdefault(skill.entrypoint, []).append(skill.skill_id)
        for connector in skill.connectors:
            by_connector.setdefault(connector, []).append(skill)

    for entrypoint, skill_ids in by_entrypoint.items():
        if len(skill_ids) > 1:
            conflicts.append(
                SkillConflict(
                    severity="medium",
                    skill_ids=sorted(skill_ids),
                    reason=f"multiple skills share entrypoint {entrypoint}",
                )
            )

    for connector, connector_skills in by_connector.items():
        high_risk = [
            skill.skill_id
            for skill in connector_skills
            if skill.risk_level >= RiskLevel.EXTERNAL_SEND
        ]
        if len(high_risk) > 1:
            conflicts.append(
                SkillConflict(
                    severity="high",
                    skill_ids=sorted(high_risk),
                    reason=f"multiple external-write skills use connector {connector}",
                )
            )

    publish_skills = [
        skill.skill_id for skill in skills if skill.risk_level >= RiskLevel.EXTERNAL_PUBLISH
    ]
    if publish_skills:
        conflicts.append(
            SkillConflict(
                severity="high",
                skill_ids=sorted(publish_skills),
                reason="publish-capable skills require human approval boundaries",
            )
        )

    return conflicts


def _query_terms(query: str) -> list[str]:
    normalized = query.lower()
    terms = re.findall(r"[\w\-\u4e00-\u9fff]+", normalized)
    return [term for term in terms if term]


def _risk_penalty(risk_level: RiskLevel) -> float:
    return {
        RiskLevel.READ_ONLY: 0.0,
        RiskLevel.LOCAL_WRITE: 0.1,
        RiskLevel.EXTERNAL_SEND: 0.6,
        RiskLevel.EXTERNAL_PUBLISH: 1.0,
        RiskLevel.SANDBOX_EXECUTION: 1.4,
    }[risk_level]


def _quality_grade(score: float) -> str:
    if score >= 0.85:
        return "A"
    if score >= 0.7:
        return "B"
    if score >= 0.5:
        return "C"
    return "D"


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped
