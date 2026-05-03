from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .models import RiskLevel


class SkillKind(str, Enum):
    PROJECT = "project"
    CONNECTOR = "connector"
    WORKFLOW = "workflow"
    AGENT = "agent"
    MEMORY = "memory"
    UTILITY = "utility"


class SkillStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"


@dataclass(slots=True)
class SkillSpec:
    skill_id: str
    name: str
    kind: SkillKind
    description: str
    version: str = "0.1.0"
    status: SkillStatus = SkillStatus.DRAFT
    entrypoint: str = ""
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    required_permissions: list[str] = field(default_factory=list)
    connectors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    source_path: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        validate_skill_id(self.skill_id)
        if not self.name.strip():
            raise ValueError("skill name is required")
        if not self.description.strip():
            raise ValueError("skill description is required")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        data["status"] = self.status.value
        data["risk_level"] = self.risk_level.code
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillSpec":
        return cls(
            skill_id=str(data["skill_id"]),
            name=str(data["name"]),
            kind=SkillKind(str(data["kind"])),
            description=str(data["description"]),
            version=str(data.get("version", "0.1.0")),
            status=SkillStatus(str(data.get("status", SkillStatus.DRAFT.value))),
            entrypoint=str(data.get("entrypoint", "")),
            risk_level=RiskLevel.parse(data.get("risk_level", "L0")),
            required_permissions=list(data.get("required_permissions", [])),
            connectors=list(data.get("connectors", [])),
            tags=list(data.get("tags", [])),
            inputs=dict(data.get("inputs", {})),
            outputs=dict(data.get("outputs", {})),
            source_path=str(data.get("source_path", "")),
            created_at=str(data.get("created_at", datetime.now(UTC).isoformat())),
            updated_at=str(data.get("updated_at", datetime.now(UTC).isoformat())),
        )


class SkillRegistry:
    def __init__(
        self,
        workspace: Path | str = ".",
        registry_path: str = "registry/skills.json",
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.registry_path = self._safe_path(registry_path)

    def list(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        tag: str | None = None,
    ) -> list[SkillSpec]:
        skills = self._load_all()
        if kind:
            kind_value = SkillKind(kind).value
            skills = [skill for skill in skills if skill.kind.value == kind_value]
        if status:
            status_value = SkillStatus(status).value
            skills = [skill for skill in skills if skill.status.value == status_value]
        if tag:
            skills = [skill for skill in skills if tag in skill.tags]
        return sorted(skills, key=lambda skill: skill.skill_id)

    def get(self, skill_id: str) -> SkillSpec:
        for skill in self._load_all():
            if skill.skill_id == skill_id:
                return skill
        raise KeyError(f"skill does not exist: {skill_id}")

    def upsert(self, skill: SkillSpec, *, write_card: bool = True) -> dict[str, Any]:
        skills_by_id = {item.skill_id: item for item in self._load_all()}
        existing = skills_by_id.get(skill.skill_id)
        if existing is not None:
            skill.created_at = existing.created_at
            skill.updated_at = datetime.now(UTC).isoformat()

        skills_by_id[skill.skill_id] = skill
        self._save_all(sorted(skills_by_id.values(), key=lambda item: item.skill_id))

        output: dict[str, Any] = {
            "skill": skill.to_dict(),
            "registry_path": str(self.registry_path.relative_to(self.workspace)),
        }
        if write_card:
            output["skill_card_path"] = self.write_skill_card(skill)
        return output

    def disable(self, skill_id: str) -> SkillSpec:
        skill = self.get(skill_id)
        skill.status = SkillStatus.DISABLED
        skill.updated_at = datetime.now(UTC).isoformat()
        self.upsert(skill, write_card=True)
        return skill

    def write_skill_card(self, skill: SkillSpec) -> str:
        card_path = self._safe_path(f"vault/30_Skills/{skill.skill_id}.md")
        card_path.parent.mkdir(parents=True, exist_ok=True)
        card_path.write_text(render_skill_card(skill), encoding="utf-8")
        return str(card_path.relative_to(self.workspace))

    def _load_all(self) -> list[SkillSpec]:
        if not self.registry_path.exists():
            return []
        raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("skill registry must contain a JSON array")
        return [SkillSpec.from_dict(item) for item in raw if isinstance(item, dict)]

    def _save_all(self, skills: list[SkillSpec]) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(
            json.dumps(
                [skill.to_dict() for skill in skills],
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def _safe_path(self, relative_path: str) -> Path:
        target = (self.workspace / relative_path).resolve()
        try:
            target.relative_to(self.workspace)
        except ValueError as exc:
            raise PermissionError("target path is outside the workspace") from exc
        return target


def validate_skill_id(skill_id: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,63}", skill_id):
        raise ValueError(
            "skill id must be 2-64 lowercase letters, numbers, dots, dashes, or underscores"
        )


def render_skill_card(skill: SkillSpec) -> str:
    lines = [
        "---",
        "omni_type: skill",
        f"skill_id: {json.dumps(skill.skill_id, ensure_ascii=False)}",
        f"kind: {json.dumps(skill.kind.value, ensure_ascii=False)}",
        f"status: {json.dumps(skill.status.value, ensure_ascii=False)}",
        f"version: {json.dumps(skill.version, ensure_ascii=False)}",
        f"risk_level: {json.dumps(skill.risk_level.code, ensure_ascii=False)}",
        "---",
        "",
        f"# {skill.name}",
        "",
        skill.description,
        "",
        "## Contract",
        "",
        f"- Entry: {skill.entrypoint or '未配置'}",
        f"- Risk: {skill.risk_level.code}",
        f"- Permissions: {', '.join(skill.required_permissions) or 'none'}",
        f"- Connectors: {', '.join(skill.connectors) or 'none'}",
        f"- Tags: {', '.join(skill.tags) or 'none'}",
    ]

    if skill.source_path:
        lines.append(f"- Source: {skill.source_path}")

    lines.extend(["", "## Review", "", "- [ ] 可用", "- [ ] 需要权限", "- [ ] 需要测试"])
    return "\n".join(lines) + "\n"
