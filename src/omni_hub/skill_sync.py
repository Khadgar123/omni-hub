"""Reconcile the skill three-truth-source problem.

Three places used to drift independently:

* ``.agents/skills/<id>/SKILL.md`` — frontmatter that Claude Code / Codex
  CLI actually load.  Canonical for ``name`` + ``description`` + human body.
* ``registry/skills.json`` — machine-readable list consumed by
  ``omni-hub skill-list`` and (future) MCP server.  Canonical for
  ``kind`` / ``entrypoint`` / ``risk_level`` / ``tags`` / ``status``.
* ``src/omni_hub/skills.py::SkillRegistry`` — Python in-process API.

This module bridges the first two by:

1. Walking every ``.agents/skills/<id>/SKILL.md`` and parsing its YAML
   frontmatter (using a tiny stdlib-only parser — no PyYAML).
2. Walking ``registry/skills.json`` for SkillSpec records.
3. Producing a per-skill_id diff: which side has it, what disagrees,
   what's missing.
4. With ``apply=True``, scaffolding the missing side so both end up
   present and consistent.

The frontmatter supports an optional ``omni_hub:`` block where the
SkillSpec machine fields can live alongside Claude Code's required
``name`` / ``description`` keys:

    ---
    name: api-management-status
    description: ...
    omni_hub:
      kind: connector
      entrypoint: operation:api_management_status
      risk_level: L0
      tags:
        - api-management
        - status
    ---
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._storage import safe_workspace_path
from .models import RiskLevel
from .skills import SkillKind, SkillSpec, SkillStatus


REGISTRY_REL = "registry/skills.json"
SKILLS_REL = ".agents/skills"


# ---------------------------------------------------------------------------
# Tiny stdlib-only YAML frontmatter parser (supports only what SKILL.md needs)
# ---------------------------------------------------------------------------


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse the leading ``---``-delimited YAML block of a markdown file.

    Supports a deliberate subset (no PyYAML dependency):
      - ``key: value`` (string), with optional quotes stripped
      - ``key: |`` block scalar (indented multi-line text)
      - ``key:`` followed by indented dict OR list (``- item``)
      - arbitrary nesting depth via recursive descent

    Returns ``{}`` when no frontmatter block is found.  Anything fancier
    (anchors, multi-line flow, type tags) is silently skipped — keep
    SKILL.md frontmatter simple.
    """

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end < 0:
        return {}
    obj, _ = _parse_block(lines[1:end], 0, 0)
    return obj if isinstance(obj, dict) else {}


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_blank(line: str) -> bool:
    return not line.strip() or line.lstrip().startswith("#")


def _parse_block(lines: list[str], start: int, base_indent: int) -> tuple[Any, int]:
    """Parse a list-or-dict block starting at ``lines[start]``.

    Returns ``(parsed_object, next_index)``.  Decides list vs dict by
    looking at the first non-blank line at indent >= base_indent.
    """

    i = start
    while i < len(lines) and _is_blank(lines[i]):
        i += 1
    if i >= len(lines):
        return {}, i

    first = lines[i]
    first_indent = _indent_of(first)
    if first_indent < base_indent:
        return {}, start

    if first.lstrip().startswith("- "):
        return _parse_list(lines, i, first_indent)
    return _parse_dict(lines, i, first_indent)


def _parse_list(lines: list[str], start: int, indent: int) -> tuple[list[str], int]:
    items: list[str] = []
    i = start
    while i < len(lines):
        line = lines[i]
        if _is_blank(line):
            i += 1
            continue
        cind = _indent_of(line)
        if cind < indent:
            break
        if cind == indent and line.lstrip().startswith("- "):
            items.append(line.lstrip()[2:].strip())
            i += 1
        else:
            # Either deeper indent (nested under list item — skip for our
            # minimal grammar) or a non-list line at same indent (end of
            # list).  Break either way to let the parent decide.
            if cind == indent:
                break
            i += 1
    return items, i


def _parse_dict(lines: list[str], start: int, indent: int) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    i = start
    while i < len(lines):
        line = lines[i]
        if _is_blank(line):
            i += 1
            continue
        cind = _indent_of(line)
        if cind < indent:
            break
        if cind > indent:
            # Should've been consumed by a nested parse; skip defensively.
            i += 1
            continue
        stripped = line.strip()
        if ":" not in stripped:
            i += 1
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()

        if value == "|":
            # Block scalar: collect lines indented strictly past `indent`.
            block_lines: list[str] = []
            block_indent = indent + 2
            j = i + 1
            while j < len(lines):
                child = lines[j]
                if not child.strip():
                    block_lines.append("")
                    j += 1
                    continue
                cind2 = _indent_of(child)
                if cind2 < block_indent:
                    break
                block_lines.append(child[block_indent:])
                j += 1
            out[key] = "\n".join(block_lines).rstrip()
            i = j
        elif not value:
            # Nested block — recurse with the natural child indent (2 deeper).
            sub, next_i = _parse_block(lines, i + 1, indent + 2)
            out[key] = sub
            i = next_i
        else:
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            out[key] = value
            i += 1
    return out, i


# ---------------------------------------------------------------------------
# Walk + diff + apply
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _load_skill_md(path: Path) -> tuple[str, dict[str, Any], str]:
    """Return ``(skill_id, frontmatter, body)`` for a single SKILL.md file."""

    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    skill_id = str(fm.get("name") or "").strip() or path.parent.name
    # Body is everything after the closing ---
    body = ""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            body = parts[2].lstrip("\n")
    return skill_id, fm, body


def _spec_from_md(
    skill_id: str,
    fm: dict[str, Any],
    body: str,
    *,
    source_path: str,
) -> SkillSpec:
    """Build a SkillSpec from SKILL.md frontmatter (+ optional ``omni_hub:`` block)."""

    omni = fm.get("omni_hub") or {}
    if not isinstance(omni, dict):
        omni = {}
    description = (
        omni.get("description")
        or fm.get("description")
        or body.strip().split("\n", 1)[0][:200]
    )
    tags = omni.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    connectors = omni.get("connectors") or []
    if isinstance(connectors, str):
        connectors = [c.strip() for c in connectors.split(",") if c.strip()]
    permissions = omni.get("required_permissions") or []
    if isinstance(permissions, str):
        permissions = [p.strip() for p in permissions.split(",") if p.strip()]

    return SkillSpec(
        skill_id=skill_id,
        name=str(omni.get("display_name") or fm.get("name") or skill_id),
        kind=SkillKind(str(omni.get("kind", "connector"))),
        description=str(description),
        version=str(omni.get("version", "0.1.0")),
        status=SkillStatus(str(omni.get("status", "active"))),
        entrypoint=str(omni.get("entrypoint", "")),
        # v0.39 — parse MD-side risk_level through RiskLevel so the diff
        # against registry/skills.json compares enum-to-enum, not
        # string-to-int.
        risk_level=RiskLevel.parse(omni.get("risk_level", "L0")),
        required_permissions=list(permissions),
        connectors=list(connectors),
        tags=list(tags),
        inputs=dict(omni.get("inputs", {})),
        outputs=dict(omni.get("outputs", {})),
        source_path=source_path,
    )


def _normalise_for_diff(field: str, value: Any) -> str:
    """Canonical-stringify a field value so MD-side / registry-side
    representations compare equal.

    Without this, ``risk_level`` drifted as "L0" (MD) vs "0" (registry)
    because IntEnum.value returns the int while string-from-MD stays a
    string — flagged in the v0.37 review as the last skill-sync noise.
    """

    if field == "risk_level":
        try:
            return RiskLevel.parse(value).code
        except Exception:                                      # noqa: BLE001
            return str(value)
    # Enum / IntEnum — use .value for canonical form.
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _registry_specs(registry_path: Path) -> dict[str, SkillSpec]:
    if not registry_path.exists():
        return {}
    text = registry_path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    raw = json.loads(text)
    if not isinstance(raw, list):
        raise ValueError(f"{registry_path}: expected a list of SkillSpec dicts")
    out: dict[str, SkillSpec] = {}
    for entry in raw:
        spec = SkillSpec.from_dict(entry)
        out[spec.skill_id] = spec
    return out


def _md_specs(skills_dir: Path) -> dict[str, SkillSpec]:
    if not skills_dir.exists():
        return {}
    out: dict[str, SkillSpec] = {}
    for sub in sorted(skills_dir.iterdir()):
        skill_md = sub / "SKILL.md"
        if not skill_md.exists():
            continue
        skill_id, fm, body = _load_skill_md(skill_md)
        spec = _spec_from_md(
            skill_id, fm, body,
            source_path=str(skill_md.relative_to(skills_dir.parent.parent))
            if skills_dir.parent.parent in skill_md.parents else str(skill_md),
        )
        out[skill_id] = spec
    return out


def _write_registry(registry_path: Path, specs: dict[str, SkillSpec]) -> None:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(specs.values(), key=lambda s: s.skill_id)
    text = json.dumps(
        [_spec_to_registry_dict(s) for s in ordered],
        ensure_ascii=False,
        indent=2,
    )
    registry_path.write_text(text + "\n", encoding="utf-8")


def _spec_to_registry_dict(spec: SkillSpec) -> dict[str, Any]:
    """Serialise a SkillSpec to the JSON shape registry/skills.json uses."""

    data = asdict(spec)
    # Convert Enums (kind, status, risk_level) to their underlying string
    data["kind"] = spec.kind.value if hasattr(spec.kind, "value") else str(spec.kind)
    data["status"] = (
        spec.status.value if hasattr(spec.status, "value") else str(spec.status)
    )
    data["risk_level"] = (
        spec.risk_level.code
        if hasattr(spec.risk_level, "code")
        else (
            spec.risk_level.value
            if hasattr(spec.risk_level, "value")
            else str(spec.risk_level)
        )
    )
    data.setdefault("created_at", _utcnow())
    data["updated_at"] = _utcnow()
    return data


# ---------------------------------------------------------------------------
# Public entry — diff + apply
# ---------------------------------------------------------------------------


def sync_skills(
    workspace: Path | str = ".",
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Compute and optionally apply the SKILL.md ↔ registry/skills.json reconciliation."""

    ws = Path(workspace).resolve()
    registry_path = safe_workspace_path(ws, REGISTRY_REL)
    skills_dir = safe_workspace_path(ws, SKILLS_REL)

    md_specs = _md_specs(skills_dir)
    reg_specs = _registry_specs(registry_path)

    md_only = sorted(set(md_specs) - set(reg_specs))
    reg_only = sorted(set(reg_specs) - set(md_specs))
    both = sorted(set(md_specs) & set(reg_specs))

    drift: list[dict[str, Any]] = []
    for skill_id in both:
        md = md_specs[skill_id]
        reg = reg_specs[skill_id]
        diffs: dict[str, dict[str, Any]] = {}
        # Diff a handful of important fields.  ``_normalise_for_diff``
        # collapses Enum / IntEnum / RiskLevel to a canonical string so
        # the v0.37 review's "L0 vs 0" drift disappears.
        for field in ("name", "description", "kind", "entrypoint", "risk_level"):
            md_value = getattr(md, field, "")
            reg_value = getattr(reg, field, "")
            md_str = _normalise_for_diff(field, md_value)
            reg_str = _normalise_for_diff(field, reg_value)
            if md_str != reg_str:
                diffs[field] = {"md": md_str, "registry": reg_str}
        if diffs:
            drift.append({"skill_id": skill_id, "diffs": diffs})

    summary: dict[str, Any] = {
        "skills_in_md": len(md_specs),
        "skills_in_registry": len(reg_specs),
        "md_only": md_only,
        "registry_only": reg_only,
        "drift": drift,
        "applied": False,
    }

    if not apply:
        return summary

    # Apply: write registry/skills.json containing the union, preferring md
    # for skills present in both (since SKILL.md is the human-edited file).
    merged: dict[str, SkillSpec] = {}
    for skill_id, spec in reg_specs.items():
        merged[skill_id] = spec
    for skill_id, spec in md_specs.items():
        merged[skill_id] = spec
    _write_registry(registry_path, merged)
    summary["applied"] = True
    summary["registry_path"] = str(registry_path.relative_to(ws))
    return summary
