from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ._storage import safe_workspace_path
from .proposals import APPROVED, PENDING, Proposal, ProposalStore
from .research_assets import (
    default_sources,
    iter_index,
    read_analysis,
    search as search_research_assets,
)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


WIKI_ROOT = "vault/wiki"
RAW_ROOT = "vault/raw"
EVIDENCE_ROOT = "vault/evidence"
CLAIM_LEDGER_PATH = ".omni/claims.jsonl"
CONTEXT_PACK_ROOT = ".omni/context_packs"

WIKI_DIRS = (
    RAW_ROOT,
    EVIDENCE_ROOT,
    WIKI_ROOT,
    f"{WIKI_ROOT}/sources",
    f"{WIKI_ROOT}/summaries",
    f"{WIKI_ROOT}/concepts",
    f"{WIKI_ROOT}/entities",
    f"{WIKI_ROOT}/events",
    f"{WIKI_ROOT}/methods",
    f"{WIKI_ROOT}/claims",
    f"{WIKI_ROOT}/syntheses",
    f"{WIKI_ROOT}/domains",
    f"{WIKI_ROOT}/domains/research",
    f"{WIKI_ROOT}/domains/engineering",
    f"{WIKI_ROOT}/domains/photography",
    f"{WIKI_ROOT}/domains/fashion",
    f"{WIKI_ROOT}/domains/chat-relationships",
    f"{WIKI_ROOT}/domains/finance",
    f"{WIKI_ROOT}/domains/policy",
    f"{WIKI_ROOT}/domains/international-relations",
    f"{WIKI_ROOT}/domains/ai-progress",
    f"{WIKI_ROOT}/domains/agent-systems",
)


@dataclass(slots=True)
class WikiSearchResult:
    path: str
    title: str
    snippet: str
    score: float

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["score"] = round(self.score, 4)
        return data


@dataclass(slots=True)
class ContextPack:
    pack_id: str
    query: str
    domain: str
    wiki_results: list[WikiSearchResult] = field(default_factory=list)
    research_results: list[dict[str, object]] = field(default_factory=list)
    path: str = ""
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, object]:
        return {
            "pack_id": self.pack_id,
            "query": self.query,
            "domain": self.domain,
            "wiki_results": [result.to_dict() for result in self.wiki_results],
            "research_results": self.research_results,
            "path": self.path,
            "created_at": self.created_at,
        }


def init_layout(workspace: Path | str = ".") -> dict[str, object]:
    workspace_root = Path(workspace).resolve()
    for relative in WIKI_DIRS:
        safe_workspace_path(workspace_root, relative).mkdir(parents=True, exist_ok=True)

    _write_if_missing(
        workspace_root / WIKI_ROOT / "index.md",
        "# Omni Wiki Index\n\n"
        "Karpathy-style compiled wiki. Read this first, then drill into domain pages.\n",
    )
    _write_if_missing(
        workspace_root / WIKI_ROOT / "log.md",
        "# Omni Wiki Log\n\n"
        "Append-only operation log for ingest, query, lint, and apply events.\n",
    )
    _write_if_missing(
        workspace_root / WIKI_ROOT / "AGENTS.md",
        "# Omni Wiki Schema\n\n"
        "- Raw sources are append-only evidence.\n"
        "- The wiki is the compiled, human-readable knowledge layer.\n"
        "- Agent-written wiki changes must be proposed before being applied.\n"
        "- Claims must keep source refs, uncertainty, and conflict state.\n",
    )
    return status(workspace_root)


def status(workspace: Path | str = ".") -> dict[str, object]:
    workspace_root = Path(workspace).resolve()
    wiki = workspace_root / WIKI_ROOT
    raw = workspace_root / RAW_ROOT
    evidence = workspace_root / EVIDENCE_ROOT
    index = wiki / "index.md"
    log = wiki / "log.md"
    schema = wiki / "AGENTS.md"
    return {
        "raw": {"path": str(raw), "exists": raw.is_dir()},
        "evidence": {"path": str(evidence), "exists": evidence.is_dir()},
        "wiki": {
            "path": str(wiki),
            "exists": wiki.is_dir(),
            "ready": wiki.is_dir() and index.exists() and log.exists() and schema.exists(),
            "index_path": str(index),
            "log_path": str(log),
            "schema_path": str(schema),
            "page_count": _count_wiki_pages(wiki),
        },
        "claims": {
            "path": str(workspace_root / CLAIM_LEDGER_PATH),
            "count": _count_jsonl(workspace_root / CLAIM_LEDGER_PATH),
        },
    }


def search_wiki(
    query: str,
    *,
    workspace: Path | str = ".",
    limit: int = 10,
) -> list[WikiSearchResult]:
    workspace_root = Path(workspace).resolve()
    wiki_root = workspace_root / WIKI_ROOT
    normalized = query.strip()
    if not normalized or not wiki_root.exists():
        return []
    terms = _query_terms(normalized)
    results: list[WikiSearchResult] = []
    for path in sorted(wiki_root.rglob("*.md")):
        if path.name in {"AGENTS.md", "index.md", "log.md"}:
            continue
        text = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(workspace_root))
        score = _score_text(f"{relative}\n{text}", terms)
        if score <= 0:
            continue
        results.append(
            WikiSearchResult(
                path=relative,
                title=_markdown_title(text) or path.stem.replace("-", " ").title(),
                snippet=_snippet(text, terms),
                score=score,
            )
        )
    results.sort(key=lambda item: (-item.score, item.path))
    return results[: max(limit, 0)]


def propose_research_wiki_update(
    workspace: Path | str = ".",
    *,
    source_id: str,
    analysis_path: str,
    target_domain: str = "research",
) -> dict[str, object]:
    workspace_root = Path(workspace).resolve()
    init_layout(workspace_root)

    entry = _research_entry_by_path(workspace_root, source_id, analysis_path)
    analysis = read_analysis(
        analysis_path,
        workspace=workspace_root,
        source_id=source_id,
        max_chars=8000,
    )

    title = str(entry.get("title") or _markdown_title(str(analysis["body"])) or "Research Note")
    target_path = _wiki_target_path(title, target_domain)
    claims = _claims_from_research_entry(
        entry,
        source_id=source_id,
        analysis_path=analysis_path,
        domain=target_domain,
    )
    body = _render_research_wiki_page(
        title=title,
        source_id=source_id,
        analysis_path=analysis_path,
        domain=target_domain,
        entry=entry,
        analysis_body=str(analysis["body"]),
        claims=claims,
    )
    summary = str(entry.get("core_operator") or entry.get("primary_logic") or _snippet(str(analysis["body"]), []))

    proposal = Proposal(
        kind="wiki_update",
        state=PENDING,
        title=title,
        summary=summary[:500],
        source_path=analysis_path,
        confidence=0.72,
        suggested_action="review_and_apply_wiki_patch",
        payload={
            "target_path": target_path,
            "domain": target_domain,
            "body": body,
            "claims": claims,
            "source": {
                "source_id": source_id,
                "analysis_path": analysis_path,
                "paper_link": entry.get("paper_link", ""),
            },
        },
    )
    paths = ProposalStore(workspace_root).store(proposal, write_card=False)
    return {"proposal": proposal, **paths}


def apply_wiki_proposal(
    workspace: Path | str = ".",
    proposal_id: str = "",
) -> dict[str, object]:
    workspace_root = Path(workspace).resolve()
    init_layout(workspace_root)
    proposal = ProposalStore(workspace_root).load(proposal_id)
    if proposal.kind != "wiki_update":
        raise ValueError(f"apply_wiki_proposal only handles kind='wiki_update'; got {proposal.kind!r}")
    if proposal.state != APPROVED:
        raise ValueError("wiki update proposal must be approved before apply")

    target_path = str(proposal.payload["target_path"])
    target = safe_workspace_path(workspace_root, target_path)
    wiki_root = (workspace_root / WIKI_ROOT).resolve()
    try:
        target.relative_to(wiki_root)
    except ValueError as exc:
        raise PermissionError("wiki update target must stay inside vault/wiki") from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    body = str(proposal.payload["body"])
    target.write_text(body.rstrip() + "\n", encoding="utf-8")
    _append_log(workspace_root, f"apply | {proposal.title}", proposal.source_path)
    _upsert_index_entry(workspace_root, target.relative_to(workspace_root), proposal.title, proposal.summary)
    claims_written = _append_claims(
        workspace_root,
        list(proposal.payload.get("claims", [])),
        proposal_id=proposal.proposal_id,
        target_path=str(target.relative_to(workspace_root)),
    )

    return {
        "proposal_id": proposal.proposal_id,
        "target_path": str(target.relative_to(workspace_root)),
        "claims_written": claims_written,
        "log_path": f"{WIKI_ROOT}/log.md",
    }


def build_context_pack(
    workspace: Path | str = ".",
    *,
    query: str,
    domain: str = "research",
    wiki_limit: int = 6,
    research_limit: int = 6,
    persist: bool = False,
) -> ContextPack:
    workspace_root = Path(workspace).resolve()
    wiki_results = search_wiki(query, workspace=workspace_root, limit=wiki_limit)
    research_results = [
        result.to_dict()
        for result in search_research_assets(
            query,
            workspace=workspace_root,
            source_id="all",
            limit=research_limit,
        )
    ]
    pack = ContextPack(
        pack_id=_stable_id("context-pack", domain, query, _utcnow(), str(uuid4())),
        query=query,
        domain=domain,
        wiki_results=wiki_results,
        research_results=research_results,
    )
    if persist:
        out_dir = safe_workspace_path(workspace_root, CONTEXT_PACK_ROOT)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{pack.pack_id}.json"
        pack.path = str(path)
        path.write_text(json.dumps(pack.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return pack


def _write_if_missing(path: Path, body: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def _count_wiki_pages(wiki_root: Path) -> int:
    if not wiki_root.exists():
        return 0
    return sum(1 for path in wiki_root.rglob("*.md") if path.is_file())


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _research_entry_by_path(
    workspace: Path,
    source_id: str,
    analysis_path: str,
) -> dict[str, object]:
    source = default_sources(workspace).get(source_id)
    if source is not None:
        for entry in iter_index(source):
            if str(entry.get("analysis_path", "")) == analysis_path:
                return dict(entry)
    return {"analysis_path": analysis_path}


def _wiki_target_path(title: str, domain: str) -> str:
    return f"{WIKI_ROOT}/domains/{_slugify(domain)}/{_slugify(title)}.md"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if slug:
        return slug
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _claims_from_research_entry(
    entry: dict[str, object],
    *,
    source_id: str,
    analysis_path: str,
    domain: str,
) -> list[dict[str, object]]:
    statements = [
        str(entry.get("core_operator", "")).strip(),
        str(entry.get("primary_logic", "")).strip(),
    ]
    claims: list[dict[str, object]] = []
    for statement in statements:
        if not statement:
            continue
        claims.append(
            {
                "claim_id": _stable_id("claim", domain, source_id, analysis_path, statement),
                "domain": domain,
                "statement": statement,
                "support": [{"source_id": source_id, "path": analysis_path}],
                "against": [],
                "confidence": 0.68,
                "uncertainty": "single-source research evidence; keep open to later conflict checks",
                "review_state": "proposed",
            }
        )
    return claims


def _render_research_wiki_page(
    *,
    title: str,
    source_id: str,
    analysis_path: str,
    domain: str,
    entry: dict[str, object],
    analysis_body: str,
    claims: list[dict[str, object]],
) -> str:
    lines = [
        "---",
        "omni_type: compiled_wiki",
        f"domain: {domain}",
        f"source_id: {source_id}",
        f"source_path: {analysis_path}",
        f"paper_link: {entry.get('paper_link', '')}",
        "review_state: approved_after_proposal",
        "---",
        "",
        f"# {title}",
        "",
        "## Source",
        "",
        f"- source_id: {source_id}",
        f"- analysis_path: {analysis_path}",
    ]
    if entry.get("paper_link"):
        lines.append(f"- paper_link: {entry['paper_link']}")
    if entry.get("venue_year"):
        lines.append(f"- venue_year: {entry['venue_year']}")

    lines.extend(["", "## Compiled Synthesis", ""])
    synthesis = str(entry.get("primary_logic") or entry.get("core_operator") or "").strip()
    lines.append(synthesis or _snippet(analysis_body, []))

    lines.extend(["", "## Claims", ""])
    if claims:
        for claim in claims:
            lines.append(f"- `{claim['claim_id']}` {claim['statement']}")
    else:
        lines.append("- No atomic claims extracted yet.")

    lines.extend(["", "## Evidence Excerpt", "", _snippet(analysis_body, [], max_chars=1200)])
    return "\n".join(lines) + "\n"


def _append_log(workspace: Path, title: str, source_path: str) -> None:
    log_path = workspace / WIKI_ROOT / "log.md"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n## [{_utcnow()}] {title}\n\n- source: {source_path}\n")


def _upsert_index_entry(workspace: Path, relative_path: Path, title: str, summary: str) -> None:
    index_path = workspace / WIKI_ROOT / "index.md"
    marker = f"- [[{relative_path.as_posix()}|{title}]]"
    existing = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    if marker in existing:
        return
    with index_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{marker} — {summary[:180]}\n")


def _append_claims(
    workspace: Path,
    claims: list[object],
    *,
    proposal_id: str,
    target_path: str,
) -> int:
    ledger = workspace / CLAIM_LEDGER_PATH
    ledger.parent.mkdir(parents=True, exist_ok=True)
    existing_ids: set[str] = set()
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                existing_ids.add(str(json.loads(line).get("claim_id", "")))
            except json.JSONDecodeError:
                continue

    written = 0
    with ledger.open("a", encoding="utf-8") as handle:
        for raw in claims:
            if not isinstance(raw, dict):
                continue
            record = dict(raw)
            claim_id = str(record.get("claim_id", ""))
            if not claim_id or claim_id in existing_ids:
                continue
            record["review_state"] = "approved"
            record["proposal_id"] = proposal_id
            record["target_path"] = target_path
            record["updated_at"] = _utcnow()
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            existing_ids.add(claim_id)
            written += 1
    return written


def _query_terms(query: str) -> list[str]:
    lowered = query.casefold()
    terms = [part for part in re.split(r"\s+", lowered) if part]
    if lowered and lowered not in terms:
        terms.insert(0, lowered)
    return terms


def _score_text(text: str, terms: list[str]) -> float:
    lowered = text.casefold()
    return sum(1.0 for term in terms if term and term in lowered)


def _markdown_title(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _snippet(text: str, terms: list[str], *, max_chars: int = 320) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return ""
    lowered = compact.casefold()
    positions = [lowered.find(term) for term in terms if term and lowered.find(term) >= 0]
    start = max(min(positions) - 80, 0) if positions else 0
    return compact[start:start + max_chars]


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
