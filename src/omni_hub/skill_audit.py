"""Skill-taxonomy audit — machine-checks HR #8 / #9 / #10.

Report-mode lint (surfaces findings, mutates nothing) over
``.agents/skills/<id>/SKILL.md``:

* **HR #8 — layer declared**: every domain skill (``*-wiki`` /
  ``status: active-domain``) must announce its layer so the router /
  reviewer can reason about always-on context tax.
* **HR #9 — atomic identity**: pairwise trigger-phrase overlap between two
  *domain* skills must stay < 0.30 (Jaccard), or the router can't
  disambiguate them.
* **HR #10 — domain skills are answer-only**: a domain ``*-wiki`` body must
  not inline retrieve/curate/write CLI verbs (those are foundation/pipeline
  concerns — CQRS read/write split).  Mentioning them in prose is fine;
  putting them in a runnable fenced block is the leak.

Pure stdlib.  Intended to be wired into ``wiki-doctor`` as a future gate;
shipped first as a reporting tool so the existing (pre-rewrite) stubs can be
surfaced without hard-failing CI.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

SKILL_AUDIT_SCHEMA_VERSION = "v0.47"

OVERLAP_THRESHOLD = 0.30

# write/curate verbs that must not be runnable inside a domain (answer-only) body
_WRITE_VERBS = (
    "wiki-ingest",
    "wiki-apply",
    "wiki-apply-proposal",
    "wiki-supersede",
    "wiki-propose-research",
    "propose-approve",
    "retrieve --persist",
)

_QUOTED = re.compile(r'["“]([^"”]{2,})["”]')
_FENCED = re.compile(r"```[a-zA-Z0-9_-]*\n(.*?)```", re.DOTALL)


@dataclass(slots=True)
class SkillAuditFinding:
    rule: str          # layer_missing | trigger_overlap | answer_only_leak
    severity: str      # error | warn
    skill_id: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class _Skill:
    skill_id: str
    is_domain: bool          # grouped as domain (by name OR declaration)
    declares_domain: bool    # explicitly declares `status: active-domain`
    triggers: frozenset[str]
    body: str


def _parse_skill(md_path: Path) -> _Skill:
    text = md_path.read_text(encoding="utf-8")
    skill_id = md_path.parent.name
    frontmatter, body = "", text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            frontmatter, body = parts[1], parts[2]
    declares_domain = (
        re.search(r"^\s*status:\s*active-domain\s*$", frontmatter, re.MULTILINE) is not None
    )
    is_domain = skill_id.endswith("-wiki") or declares_domain
    # triggers = quoted phrases anywhere in the frontmatter description
    triggers = frozenset(m.group(1).strip().casefold() for m in _QUOTED.finditer(frontmatter))
    return _Skill(
        skill_id=skill_id,
        is_domain=is_domain,
        declares_domain=declares_domain,
        triggers=triggers,
        body=body,
    )


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def audit_skills(skills_root: Path | str) -> list[SkillAuditFinding]:
    root = Path(skills_root)
    skills: list[_Skill] = []
    for md in sorted(root.glob("*/SKILL.md")):
        try:
            skills.append(_parse_skill(md))
        except OSError:
            continue

    findings: list[SkillAuditFinding] = []
    domain = [s for s in skills if s.is_domain]

    # HR #8 — layer declared: a *-wiki skill must explicitly declare its layer,
    # not merely be inferred from its id.
    for s in skills:
        if s.skill_id.endswith("-wiki") and not s.declares_domain:
            findings.append(SkillAuditFinding(
                rule="layer_missing", severity="error", skill_id=s.skill_id,
                detail="`*-wiki` skill does not declare `status: active-domain`",
            ))

    # HR #9 — atomic identity: pairwise trigger overlap among domain skills
    for i in range(len(domain)):
        for j in range(i + 1, len(domain)):
            ov = _jaccard(domain[i].triggers, domain[j].triggers)
            if ov > OVERLAP_THRESHOLD:
                findings.append(SkillAuditFinding(
                    rule="trigger_overlap", severity="error",
                    skill_id=f"{domain[i].skill_id}~{domain[j].skill_id}",
                    detail=f"trigger Jaccard {ov:.2f} > {OVERLAP_THRESHOLD} — router cannot disambiguate",
                ))

    # HR #10 — domain skills answer-only: no runnable write verb in a fenced block
    for s in domain:
        for block in _FENCED.findall(s.body):
            hit = next((v for v in _WRITE_VERBS if v in block), None)
            if hit:
                findings.append(SkillAuditFinding(
                    rule="answer_only_leak", severity="warn", skill_id=s.skill_id,
                    detail=f"domain body runs write/curate verb `{hit}` (belongs in foundation/pipeline)",
                ))
                break
    return findings


__all__ = [
    "SKILL_AUDIT_SCHEMA_VERSION",
    "OVERLAP_THRESHOLD",
    "SkillAuditFinding",
    "audit_skills",
]
