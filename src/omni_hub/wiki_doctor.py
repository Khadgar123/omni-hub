"""Wiki health check — one-stop integrity probe.

Mirrors ``retrieve-doctor`` (which probes connector readiness) but for
the knowledge plane.  Surfaces issues that the per-page wiki-lint rules
can't see (cross-store invariants, FTS5 staleness, supersede graph
cycles, dead index entries).

Each check returns:

    {"name", "ok" (bool), "severity" ("info"|"warn"|"error"), "detail"}

The dispatcher aggregates them so the CLI can print one table and the
MCP tool can return one structured object.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ._storage import safe_workspace_path
from .domain_schemas import DOMAIN_SCHEMAS
from .knowledge_plane import CLAIM_LEDGER_PATH, WIKI_ROOT


@dataclass(slots=True)
class DoctorCheck:
    name: str
    ok: bool
    severity: str          # info | warn | error
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DoctorReport:
    ok: bool
    counts: dict[str, int]
    checks: list[DoctorCheck]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": dict(self.counts),
            "checks": [c.to_dict() for c in self.checks],
        }


def run_doctor(workspace: Path | str = ".") -> DoctorReport:
    workspace_root = Path(workspace).resolve()
    checks: list[DoctorCheck] = [
        _check_layout(workspace_root),
        _check_domain_schemas(workspace_root),
        _check_fts5(workspace_root),
        _check_claims_jsonl(workspace_root),
        _check_supersede_graph(workspace_root),
        _check_index_md(workspace_root),
        _check_orphan_skill_md(workspace_root),
        _check_projection(workspace_root),
    ]
    counts = {"info": 0, "warn": 0, "error": 0}
    for check in checks:
        counts[check.severity] = counts.get(check.severity, 0) + 1
    return DoctorReport(
        ok=counts["error"] == 0,
        counts=counts,
        checks=checks,
    )


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_layout(workspace: Path) -> DoctorCheck:
    """vault/wiki + AGENTS.md + index.md + log.md must exist."""

    required = [
        f"{WIKI_ROOT}/AGENTS.md",
        f"{WIKI_ROOT}/index.md",
        f"{WIKI_ROOT}/log.md",
    ]
    missing = [p for p in required if not (workspace / p).exists()]
    if missing:
        return DoctorCheck(
            name="wiki_layout", ok=False, severity="error",
            detail={"missing": missing,
                    "suggestion": "run `omni-hub wiki-init`"},
        )
    return DoctorCheck(
        name="wiki_layout", ok=True, severity="info",
        detail={"checked": required},
    )


def _check_domain_schemas(workspace: Path) -> DoctorCheck:
    """All 12 domain `_schema.md` must exist."""

    missing = []
    for slug, schema in DOMAIN_SCHEMAS.items():
        path = workspace / WIKI_ROOT / "domains" / schema.folder / "_schema.md"
        if not path.exists():
            missing.append(f"domains/{schema.folder}/_schema.md")
    if missing:
        return DoctorCheck(
            name="domain_schemas", ok=False, severity="error",
            detail={"missing": missing,
                    "suggestion": "run `omni-hub wiki-init`"},
        )
    return DoctorCheck(
        name="domain_schemas", ok=True, severity="info",
        detail={"count": len(DOMAIN_SCHEMAS)},
    )


def _check_fts5(workspace: Path) -> DoctorCheck:
    """FTS5 index row count vs filesystem .md count.  Mismatch suggests reindex."""

    try:
        from .wiki_fts import WikiFTSIndex, fts5_available
    except Exception:
        return DoctorCheck(
            name="fts5_freshness", ok=False, severity="error",
            detail={"reason": "wiki_fts import failed"},
        )
    if not fts5_available():
        return DoctorCheck(
            name="fts5_freshness", ok=True, severity="warn",
            detail={"reason": "local sqlite lacks FTS5; using substring fallback"},
        )
    wiki_root = workspace / WIKI_ROOT
    if not wiki_root.exists():
        return DoctorCheck(
            name="fts5_freshness", ok=True, severity="info",
            detail={"reason": "wiki not initialised"},
        )
    fs_count = sum(
        1 for p in wiki_root.rglob("*.md")
        if p.is_file() and p.name not in {"AGENTS.md", "index.md", "log.md", "_schema.md"}
    )
    indexed = WikiFTSIndex(workspace).stats().get("indexed", 0)
    if indexed != fs_count:
        return DoctorCheck(
            name="fts5_freshness", ok=False, severity="warn",
            detail={
                "indexed": indexed, "filesystem": fs_count,
                "delta": fs_count - indexed,
                "suggestion": "run `omni-hub wiki-reindex`",
            },
        )
    return DoctorCheck(
        name="fts5_freshness", ok=True, severity="info",
        detail={"indexed": indexed, "filesystem": fs_count},
    )


def _check_claims_jsonl(workspace: Path) -> DoctorCheck:
    """claims.jsonl is parseable; every claim_id appears once; required fields present."""

    ledger = workspace / CLAIM_LEDGER_PATH
    if not ledger.exists():
        return DoctorCheck(
            name="claims_jsonl", ok=True, severity="info",
            detail={"reason": "no claims yet"},
        )
    seen_ids: set[str] = set()
    duplicates: list[str] = []
    parse_errors: list[int] = []
    missing_fields: list[dict[str, Any]] = []
    required_fields = ("claim_id", "statement", "domain", "review_state")
    total = 0
    for idx, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        total += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            parse_errors.append(idx)
            continue
        cid = str(record.get("claim_id", ""))
        if not cid:
            missing_fields.append({"line": idx, "missing": "claim_id"})
            continue
        if cid in seen_ids:
            duplicates.append(cid)
        seen_ids.add(cid)
        miss = [f for f in required_fields if f not in record]
        if miss:
            missing_fields.append({"line": idx, "claim_id": cid, "missing": miss})

    error = bool(parse_errors or duplicates or missing_fields)
    return DoctorCheck(
        name="claims_jsonl", ok=not error,
        severity="error" if error else "info",
        detail={
            "total": total,
            "unique_claim_ids": len(seen_ids),
            "duplicates": duplicates[:5],
            "parse_errors": parse_errors[:5],
            "missing_fields": missing_fields[:5],
        },
    )


def _check_supersede_graph(workspace: Path) -> DoctorCheck:
    """Detect cycles + dangling pointers in supersede chain."""

    ledger = workspace / CLAIM_LEDGER_PATH
    if not ledger.exists():
        return DoctorCheck(
            name="supersede_graph", ok=True, severity="info",
            detail={"reason": "no claims yet"},
        )
    claims: dict[str, dict[str, Any]] = {}
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        cid = str(record.get("claim_id", ""))
        if cid:
            claims[cid] = record

    dangling_superseded_by: list[str] = []
    dangling_supersedes: list[str] = []
    cycles: list[list[str]] = []

    for cid, record in claims.items():
        sb = record.get("superseded_by")
        if isinstance(sb, str) and sb and sb not in claims:
            dangling_superseded_by.append(f"{cid} -> {sb}")
        for older in record.get("supersedes", []) or []:
            if isinstance(older, str) and older and older not in claims:
                dangling_supersedes.append(f"{cid} -> {older}")

    # Cycle detection via DFS on superseded_by chain.
    color: dict[str, int] = {cid: 0 for cid in claims}
    for start in claims:
        if color[start] != 0:
            continue
        path: list[str] = []
        node = start
        while node:
            if color.get(node) == 1:
                idx = path.index(node) if node in path else 0
                cycles.append(path[idx:] + [node])
                break
            if color.get(node) == 2:
                break
            color[node] = 1
            path.append(node)
            sb = claims.get(node, {}).get("superseded_by")
            if not (isinstance(sb, str) and sb):
                break
            node = sb
        for visited in path:
            color[visited] = 2

    error = bool(dangling_superseded_by or dangling_supersedes or cycles)
    return DoctorCheck(
        name="supersede_graph", ok=not error,
        severity="error" if cycles else ("warn" if error else "info"),
        detail={
            "claims": len(claims),
            "dangling_superseded_by": dangling_superseded_by[:5],
            "dangling_supersedes": dangling_supersedes[:5],
            "cycles": cycles[:3],
        },
    )


_INDEX_LINK_RE = re.compile(r"\[\[([^\]\|]+)\|([^\]]+)\]\]")


def _check_index_md(workspace: Path) -> DoctorCheck:
    """index.md links must point at existing pages."""

    index_path = workspace / WIKI_ROOT / "index.md"
    if not index_path.exists():
        return DoctorCheck(
            name="index_md", ok=True, severity="info",
            detail={"reason": "no index.md yet"},
        )
    text = index_path.read_text(encoding="utf-8")
    dead: list[str] = []
    total = 0
    for match in _INDEX_LINK_RE.finditer(text):
        total += 1
        target_path = match.group(1).strip()
        full = workspace / target_path
        if not full.exists():
            dead.append(target_path)
    return DoctorCheck(
        name="index_md", ok=not dead,
        severity="warn" if dead else "info",
        detail={"total_links": total, "dead_links": dead[:5]},
    )


def _check_orphan_skill_md(workspace: Path) -> DoctorCheck:
    """SKILL.md files under .agents/skills/ must be registered in registry/skills.json."""

    skills_dir = workspace / ".agents" / "skills"
    registry_path = workspace / "registry" / "skills.json"
    if not skills_dir.exists():
        return DoctorCheck(
            name="skill_registry", ok=True, severity="info",
            detail={"reason": "no .agents/skills/"},
        )

    md_skills: list[str] = []
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        if (skill_dir / "SKILL.md").exists():
            md_skills.append(skill_dir.name)

    registered_ids: set[str] = set()
    if registry_path.exists():
        try:
            data = json.loads(registry_path.read_text(encoding="utf-8"))
            # The registry historically used either `[{...}, ...]` (top-level
            # list) or `{"skills": [...]}`.  Support both.
            skills_iter = data if isinstance(data, list) else (data.get("skills", []) if isinstance(data, dict) else [])
            for skill in skills_iter or []:
                if isinstance(skill, dict):
                    sid = str(skill.get("skill_id", ""))
                    if sid:
                        registered_ids.add(sid)
        except json.JSONDecodeError:
            return DoctorCheck(
                name="skill_registry", ok=False, severity="error",
                detail={"reason": "registry/skills.json is invalid JSON"},
            )

    orphans = [s for s in md_skills if s not in registered_ids]
    return DoctorCheck(
        name="skill_registry", ok=not orphans,
        severity="warn" if orphans else "info",
        detail={
            "skills_on_disk": len(md_skills),
            "registered": len(registered_ids),
            "orphans": orphans[:8],
            "suggestion": "run `omni-hub skill-sync --apply`" if orphans else "",
        },
    )


def _check_projection(workspace: Path) -> DoctorCheck:
    """WS1: claims<->synthesis-page projection integrity.

    Detects drift between the claim ledger (source of truth) and the
    synthesis pages projected from it: orphan pages on disk with no backing
    active claim, and claim-referenced synthesis targets with no page.
    Wrapped so the probe can never crash the doctor.
    """

    try:
        from . import wiki_projection as _wp
        pj = _wp.doctor_projection(workspace)
    except Exception as exc:                                       # noqa: BLE001
        return DoctorCheck(
            name="projection_integrity", ok=True, severity="info",
            detail={"skipped": str(exc)},
        )
    orphans = pj.get("orphan_pages", [])
    unrendered = pj.get("unrendered", [])
    ok = bool(pj.get("ok", not orphans and not unrendered))
    return DoctorCheck(
        name="projection_integrity", ok=ok,
        severity="info" if ok else "error",
        detail={
            "orphan_pages": orphans[:8],
            "unrendered": unrendered[:8],
            "suggestion": "run `omni-hub wiki-render` to rebuild pages from claims" if not ok else "",
        },
    )
