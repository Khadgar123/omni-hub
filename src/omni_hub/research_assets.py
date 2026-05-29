from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass(slots=True)
class ResearchAssetSource:
    source_id: str
    root: Path
    index_path: Path
    role: str

    @property
    def available(self) -> bool:
        return self.root.exists() and self.index_path.exists()

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "root": str(self.root),
            "index_path": str(self.index_path),
            "role": self.role,
            "available": self.available,
        }


@dataclass(slots=True)
class ResearchAssetRecord:
    source_id: str
    title: str
    analysis_path: str
    score: float
    entry: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["score"] = round(self.score, 4)
        return data


@dataclass(slots=True)
class ResearchFlowSkill:
    name: str
    path: str
    description: str
    status: str
    mode: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def default_sources(workspace: Path | str = ".") -> dict[str, ResearchAssetSource]:
    root = Path(workspace).resolve()
    researchflow = root / "agent-harness" / "researchflow"
    paperbite = root / "agent-harness" / "paperbite"
    return {
        "researchflow": ResearchAssetSource(
            source_id="researchflow",
            root=researchflow,
            index_path=researchflow / "obsidian-vault" / "index" / "index.jsonl",
            role="workflow and small demo research memory",
        ),
        "paperbite": ResearchAssetSource(
            source_id="paperbite",
            root=paperbite,
            index_path=paperbite / "index" / "index.jsonl",
            role="large read-only public evidence vault",
        ),
    }


def status(workspace: Path | str = ".") -> dict[str, object]:
    sources = default_sources(workspace)
    source_rows: list[dict[str, object]] = []
    for source in sources.values():
        row = source.to_dict()
        row["index_records"] = count_index_records(source)
        row["analysis_notes"] = count_analysis_notes(source)
        source_rows.append(row)
    return {
        "sources": source_rows,
        "total_index_records": sum(int(row["index_records"]) for row in source_rows),
        "total_analysis_notes": sum(int(row["analysis_notes"]) for row in source_rows),
    }


def search(
    query: str,
    *,
    workspace: Path | str = ".",
    source_id: str = "all",
    limit: int = 10,
) -> list[ResearchAssetRecord]:
    normalized = query.strip()
    if not normalized:
        return []

    selected = _select_sources(default_sources(workspace), source_id)
    terms = _query_terms(normalized)
    results: list[ResearchAssetRecord] = []
    for source in selected:
        for entry in iter_index(source):
            score = _score_entry(entry, terms)
            if score <= 0:
                continue
            results.append(
                ResearchAssetRecord(
                    source_id=source.source_id,
                    title=str(entry.get("title", "")),
                    analysis_path=str(entry.get("analysis_path", "")),
                    score=score,
                    entry=entry,
                )
            )
    results.sort(key=lambda item: (-item.score, item.source_id, item.title))
    return results[: max(limit, 0)]


def read_analysis(
    analysis_path: str,
    *,
    workspace: Path | str = ".",
    source_id: str,
    max_chars: int = 4000,
) -> dict[str, object]:
    source = _source(default_sources(workspace), source_id)
    path = _safe_source_path(source.root, analysis_path)
    if path.suffix.lower() != ".md":
        raise ValueError("analysis_path must point to a Markdown file")
    relative = str(path.relative_to(source.root))
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = _read_tracked_file_from_git(source.root, relative)
    return {
        "source_id": source.source_id,
        "path": str(path),
        "relative_path": relative,
        "body": text[: max(max_chars, 0)],
        "body_chars": len(text),
    }


def list_researchflow_skills(workspace: Path | str = ".") -> list[ResearchFlowSkill]:
    root = Path(workspace).resolve() / "agent-harness" / "researchflow"
    skills_root = root / ".claude" / "skills"
    if not skills_root.exists():
        return []

    skills: list[ResearchFlowSkill] = []
    for skill_md in sorted(skills_root.glob("*/SKILL.md")):
        metadata = _frontmatter(skill_md)
        skills.append(
            ResearchFlowSkill(
                name=str(metadata.get("name") or skill_md.parent.name),
                path=str(skill_md),
                description=str(metadata.get("description", "")),
                status=str(metadata.get("status", "")),
                mode=str(metadata.get("mode", "")),
            )
        )
    return skills


def iter_index(source: ResearchAssetSource) -> Iterable[dict[str, object]]:
    if not source.index_path.exists():
        return
    with source.index_path.open(encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            value = json.loads(raw)
            if isinstance(value, dict):
                yield value


def count_index_records(source: ResearchAssetSource) -> int:
    if not source.index_path.exists():
        return 0
    with source.index_path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def count_analysis_notes(source: ResearchAssetSource) -> int:
    if not source.root.exists():
        return 0
    analysis_root = source.root / "analysis"
    if not analysis_root.exists():
        analysis_root = source.root / "obsidian-vault" / "analysis"
    if not analysis_root.exists():
        return 0
    return sum(1 for path in analysis_root.rglob("*.md") if path.is_file())


def _select_sources(
    sources: dict[str, ResearchAssetSource], source_id: str
) -> list[ResearchAssetSource]:
    if source_id == "all":
        return list(sources.values())
    return [_source(sources, source_id)]


def _source(
    sources: dict[str, ResearchAssetSource], source_id: str
) -> ResearchAssetSource:
    try:
        return sources[source_id]
    except KeyError as exc:
        raise ValueError(
            f"unknown source_id: {source_id!r}; expected one of "
            f"{', '.join(sorted(sources))}"
        ) from exc


def _safe_source_path(root: Path, relative_path: str) -> Path:
    if not relative_path.strip():
        raise ValueError("analysis_path is required")
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise PermissionError("analysis_path escapes the research asset root") from exc
    return candidate


def _read_tracked_file_from_git(root: Path, relative_path: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"HEAD:{relative_path}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise FileNotFoundError(
            f"analysis note does not exist in working tree or git index: {relative_path}"
        )
    return result.stdout


def _query_terms(query: str) -> list[str]:
    lowered = query.casefold()
    parts = [part for part in re.split(r"\s+", lowered) if part]
    if lowered not in parts:
        parts.insert(0, lowered)
    return parts


def _field_text(value: object) -> str:
    if isinstance(value, list):
        return " ".join(_field_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_field_text(item) for item in value.values())
    return str(value or "")


def _score_entry(entry: dict[str, object], terms: list[str]) -> float:
    weighted_fields = (
        (3.0, entry.get("title")),
        (2.0, entry.get("methods")),
        (2.0, entry.get("topics")),
        (1.5, entry.get("datasets")),
        (1.5, entry.get("method_groups")),
        (1.2, entry.get("tags")),
        (1.0, entry.get("core_operator")),
        (1.0, entry.get("primary_logic")),
        (0.5, entry.get("venue_year")),
    )
    score = 0.0
    for weight, value in weighted_fields:
        text = _field_text(value).casefold()
        for term in terms:
            if term and term in text:
                score += weight
    return score


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}

    metadata: dict[str, str] = {}
    current_key = ""
    current_parts: list[str] = []

    def flush() -> None:
        nonlocal current_key, current_parts
        if current_key:
            metadata[current_key] = " ".join(part.strip() for part in current_parts).strip()
        current_key = ""
        current_parts = []

    for raw_line in text[3:end].splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if ":" in line and not line.startswith((" ", "\t", ">")):
            flush()
            key, value = line.split(":", 1)
            current_key = key.strip()
            first_value = value.strip().strip('"')
            current_parts = [] if first_value in {">", "|"} else [first_value]
        elif current_key:
            current_parts.append(line.strip().strip("> ").strip('"'))
    flush()
    return metadata


# ---------------------------------------------------------------------------
# WS3: ResearchFlow deep-parse (main_analysis.json) -> candidate claims -> Proposal
#
# ResearchFlow's main_analysis.json (MinerU-extracted sections / figures /
# tables / formulas + a verified analysis object) is the research-domain
# Layer-4 the parent repo lacks.  This adapter decomposes that object into
# candidate claims in the omni-hub claim schema, then emits them through the
# sanctioned Proposal[T] path (HR#5: never a direct wiki write).  Once
# approved, WS1 projects the synthesis page from those claims.
# ---------------------------------------------------------------------------

import hashlib as _hashlib
from datetime import datetime as _datetime, timezone as _timezone


def _rf_claim_id(*parts: str) -> str:
    return _hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _rf_utcnow() -> str:
    return _datetime.now(_timezone.utc).isoformat()


def researchflow_analysis_to_claims(
    analysis: object,
    *,
    source_id: str = "researchflow",
    analysis_path: str = "",
    domain: str = "research",
) -> list[dict[str, object]]:
    """Decompose a ResearchFlow ``main_analysis.json`` into candidate claims.

    Three claim families (each retains an evidence anchor back to the RF
    section/figure for audit):

    * ``analysis_truth.decisive_evidence[]`` + ``core_insight`` -> conclusion
    * ``method.changed_slots[]``                                -> method-change
    * ``experiments.main_results[]``                            -> result

    Conservative + lossless-on-skip: malformed entries are skipped (they stay
    in the RF note as evidence), never crash.  Dedups by statement; ids are
    deterministic so re-ingesting the same analysis is idempotent.
    """

    if not isinstance(analysis, dict):
        return []

    claims: list[dict[str, object]] = []
    seen: set[str] = set()

    def _add(statement: str, anchor: object, confidence: object, kind: str) -> None:
        s = (statement or "").strip()
        if not s or s.lower() in seen:
            return
        seen.add(s.lower())
        try:
            conf = float(confidence) if confidence not in (None, "") else 0.6
        except (TypeError, ValueError):
            conf = 0.6
        claims.append({
            "claim_id": _rf_claim_id("rf", domain, kind, source_id, analysis_path, s),
            "domain": domain,
            "statement": s,
            "support": [{
                "source_id": source_id,
                "path": analysis_path,
                "anchor": str(anchor or ""),
                "source": "researchflow",
                "served_via": "deep_parse",
                "claim_kind": kind,
            }],
            "against": [],
            "confidence": conf,
            "uncertainty": (
                "ResearchFlow deep-parse evidence; awaits human review + "
                "cross-source confirmation"
            ),
            "review_state": "proposed",
            "t_valid_from": _rf_utcnow(),
            "t_valid_to": None,
            "supersedes": [],
            "superseded_by": None,
        })

    truth = analysis.get("analysis_truth")
    if isinstance(truth, dict):
        for ev in truth.get("decisive_evidence", []) or []:
            if isinstance(ev, dict):
                _add(str(ev.get("claim", "")), ev.get("anchor", ""),
                     ev.get("confidence"), "conclusion")
        _add(str(truth.get("core_insight", "")), "analysis_truth.core_insight",
             0.6, "conclusion")

    method = analysis.get("method")
    if isinstance(method, dict):
        proposed = str(method.get("proposed_method_name", "")).strip()
        for slot in method.get("changed_slots", []) or []:
            if not isinstance(slot, dict):
                continue
            name = str(slot.get("slot_name", "")).strip()
            if not name:
                continue
            base = str(slot.get("baseline_value", "")).strip()
            new = str(slot.get("proposed_value", "")).strip()
            prefix = f"{proposed}: " if proposed else ""
            statement = (
                f"{prefix}{name} changed from '{base}' to '{new}'"
                if (base or new) else f"{prefix}{name} is the proposed change"
            )
            _add(statement, slot.get("evidence_anchor", ""),
                 slot.get("confidence"), "method")

    experiments = analysis.get("experiments")
    if isinstance(experiments, dict):
        for res in experiments.get("main_results", []) or []:
            if not isinstance(res, dict):
                continue
            bench = str(res.get("benchmark", "")).strip()
            metric = str(res.get("metric", "")).strip()
            if not bench and not metric:
                continue
            prop = str(res.get("proposed", "")).strip()
            base = str(res.get("baseline", "")).strip()
            delta = str(res.get("delta", "")).strip()
            statement = (
                f"On {bench} ({metric}): proposed {prop} vs baseline {base}"
                + (f" (delta {delta})" if delta else "")
            )
            _add(statement, res.get("anchor", ""), res.get("confidence"), "result")

    return claims


def propose_researchflow_analysis(
    workspace="/.",
    *,
    analysis_json: str = "",
    domain: str = "research",
    title: str = "",
    trace_id: str = "",
) -> dict[str, object]:
    """Read a ResearchFlow main_analysis.json -> candidate claims -> Proposal.

    The target is a synthesis page, so on approve the body is rendered FROM
    the claims (WS1 projection).  Emits a Proposal[T] for human review — the
    only sanctioned path from a ResearchFlow deep-parse to the parent ledger.
    """

    import json as _json
    from pathlib import Path as _Path

    from .knowledge_plane import (
        _slugify,
        _synthesis_target_path,
        append_log,
        init_layout,
        safe_workspace_path,
    )
    from .proposals import PENDING, Proposal, ProposalStore

    workspace_root = _Path(workspace).resolve()
    init_layout(workspace_root)

    analysis_file = safe_workspace_path(workspace_root, analysis_json)
    if not analysis_file.exists():
        raise FileNotFoundError(f"ResearchFlow analysis not found: {analysis_json}")
    try:
        analysis = _json.loads(analysis_file.read_text(encoding="utf-8"))
    except _json.JSONDecodeError as exc:
        raise ValueError(f"invalid analysis json {analysis_json}: {exc}") from exc

    paper_meta = analysis.get("paper_metadata") if isinstance(analysis, dict) else {}
    paper_meta = paper_meta if isinstance(paper_meta, dict) else {}
    resolved_title = (
        title.strip() or str(paper_meta.get("title", "")).strip()
        or "ResearchFlow analysis"
    )
    source_id = f"researchflow:{_slugify(resolved_title)}"
    rel_analysis = str(analysis_file.relative_to(workspace_root))

    claims = researchflow_analysis_to_claims(
        analysis, source_id=source_id, analysis_path=rel_analysis, domain=domain,
    )
    if not claims:
        raise ValueError(
            "no candidate claims extracted from analysis "
            "(empty analysis_truth/method/experiments)"
        )

    target_path = _synthesis_target_path(resolved_title, source_id)
    body = (
        f"---\npage_type: synthesis\ndomain: {domain}\n"
        f"review_state: proposed\n---\n\n# {resolved_title}\n\n"
        f"_Pending projection from {len(claims)} ResearchFlow claim(s)._\n"
    )

    proposal = Proposal(
        kind="wiki_update",
        state=PENDING,
        title=f"[researchflow] {resolved_title}",
        summary=f"{len(claims)} candidate claim(s) from ResearchFlow deep-parse.",
        source_path=rel_analysis,
        payload={
            "target_path": target_path,
            "domain": domain,
            "page_type": "synthesis",
            "title": resolved_title,
            "query": resolved_title,
            "body": body,
            "claims": claims,
            "researchflow": {
                "analysis_path": rel_analysis,
                "venue": paper_meta.get("venue", ""),
                "year": paper_meta.get("year", ""),
            },
        },
    )
    stored = ProposalStore(workspace_root).store(proposal)
    proposal_id = stored.get("proposal_id", proposal.proposal_id)
    append_log(
        workspace_root, op="ingest",
        summary=f"researchflow {resolved_title} ({len(claims)} claims)",
        source=rel_analysis,
    )
    return {
        "proposal_id": proposal_id,
        "target_path": target_path,
        "claim_count": len(claims),
        "source_id": source_id,
        "trace_id": trace_id,
    }


__all__ = [
    "ResearchAssetSource",
    "ResearchAssetRecord",
    "ResearchFlowSkill",
    "default_sources",
    "status",
    "search",
    "read_analysis",
    "list_researchflow_skills",
    "iter_index",
    "count_index_records",
    "count_analysis_notes",
    "researchflow_analysis_to_claims",
    "propose_researchflow_analysis",
]
