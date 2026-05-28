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
