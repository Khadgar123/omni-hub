"""Unified proposal model + SQLite-backed store.

A `Proposal` is the universal *typed-interrupt* primitive: any agent /
deterministic op that wants to commit a change first writes a `Proposal`
(state=pending) and the human resolves it via `propose-approve` /
`propose-reject` (or `digest_proposal` for the knowledge sub-flow).

The `kind` field distinguishes payload schema:

    knowledge        — entity / relation proposals from a vault note
    duplicate        — two records share title + summary
    stale            — record older than freshness window
    conflict         — same title, different summary
    low_signal       — low information density / no citation
    generation       — worker output candidate (Phase-1 agent worker output)

`EntityProposal` and `RelationProposal` survive as small nested dataclasses
used in the knowledge payload — they have stable schema and make
``build_knowledge_proposal`` easier to write.

Storage: ``.omni/proposals.sqlite3`` (WAL).  The JSON + markdown cards at
``.omni/proposals/<id>.{json,md}`` are still written by the knowledge flow
for Obsidian-side review; they're a derived view, not the source of truth.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .markdown import MarkdownDocument, plain_text_excerpt


KNOWN_ENTITY_TERMS = {
    "ai": "AI",
    "bilibili": "Bilibili",
    "codex": "Codex",
    "discord": "Discord",
    "feishu": "Feishu",
    "github": "GitHub",
    "graphiti": "Graphiti",
    "mem0": "Mem0",
    "obsidian": "Obsidian",
    "openai": "OpenAI",
    "temporal": "Temporal",
    "youtube": "YouTube",
    "小红书": "小红书",
    "飞书": "飞书",
    "万象中枢": "万象中枢",
    "自动化工作流": "自动化工作流",
}


PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
VALID_STATES = {PENDING, APPROVED, REJECTED}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return str(uuid4())


# ---------------------------------------------------------------------------
# Nested types — small, schema-stable, used inside knowledge payloads
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EntityProposal:
    name: str
    kind: str
    evidence: str
    confidence: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "EntityProposal":
        return cls(
            name=str(data["name"]),
            kind=str(data["kind"]),
            evidence=str(data["evidence"]),
            confidence=float(data["confidence"]),
        )


@dataclass(slots=True)
class RelationProposal:
    source: str
    relation: str
    target: str
    evidence: str
    confidence: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "RelationProposal":
        return cls(
            source=str(data["source"]),
            relation=str(data["relation"]),
            target=str(data["target"]),
            evidence=str(data["evidence"]),
            confidence=float(data["confidence"]),
        )


# ---------------------------------------------------------------------------
# Unified Proposal
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Proposal:
    """Typed-interrupt: a pending change awaiting human resolution.

    ``payload`` carries kind-specific data (e.g. ``entities`` + ``relations``
    for ``kind="knowledge"``, ``source_paths`` for the redundancy kinds).
    Schema-stable enough to round-trip through SQLite + jsonl.
    """

    proposal_id: str = field(default_factory=_new_id)
    kind: str = ""
    state: str = PENDING
    payload: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    suggested_action: str = ""
    title: str = ""
    summary: str = ""
    source_path: str = ""
    source_paths: list[str] = field(default_factory=list)
    source_task_id: str | None = None
    reason: str = ""
    decided_by: str = ""
    decided_at: str | None = None
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Proposal":
        return cls(
            proposal_id=str(data.get("proposal_id") or _new_id()),
            kind=str(data.get("kind", "")),
            state=str(data.get("state", PENDING)),
            payload=dict(data.get("payload", {})),
            confidence=float(data.get("confidence", 0.0)),
            suggested_action=str(data.get("suggested_action", "")),
            title=str(data.get("title", "")),
            summary=str(data.get("summary", "")),
            source_path=str(data.get("source_path", "")),
            source_paths=list(data.get("source_paths", [])),
            source_task_id=data.get("source_task_id"),
            reason=str(data.get("reason", "")),
            decided_by=str(data.get("decided_by", "")),
            decided_at=data.get("decided_at"),
            created_at=str(data.get("created_at") or _utcnow()),
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class ProposalStore:
    """SQLite-backed store with optional JSON/markdown card render-out.

    Card files at ``.omni/proposals/<id>.{json,md}`` are written for
    knowledge proposals so Obsidian users can review them inline.  They are
    a derived view: SQLite is the source of truth for state.
    """

    def __init__(
        self,
        workspace: Path | str = ".",
        db_path: str = ".omni/proposals.sqlite3",
        *,
        create: bool = True,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.db_path = self._safe_path(db_path)
        if create:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()

    # ------- public API ----------------------------------------------------

    def store(self, proposal: Proposal, *, write_card: bool = True) -> dict[str, str]:
        """Insert (or upsert by proposal_id) and optionally render a card."""

        self._upsert(proposal)
        out: dict[str, str] = {"proposal_id": proposal.proposal_id}
        if write_card and proposal.kind == "knowledge":
            card_paths = self._render_card(proposal)
            out.update(card_paths)
        return out

    def load(self, proposal_id_or_path: str) -> Proposal:
        value = proposal_id_or_path.strip()
        if not value:
            raise ValueError("proposal id or path is required")

        # path forms — fall back to reading the JSON card (legacy)
        if value.endswith(".json") or "/" in value:
            path = self._safe_path(value)
            if not path.exists():
                raise FileNotFoundError(f"proposal does not exist: {proposal_id_or_path}")
            return Proposal.from_dict(json.loads(path.read_text(encoding="utf-8")))

        # id form — query SQLite first, fall back to card file
        proposal = self._fetch(value)
        if proposal is not None:
            return proposal
        card = self._safe_path(f".omni/proposals/{value}.json")
        if card.exists():
            return Proposal.from_dict(json.loads(card.read_text(encoding="utf-8")))
        raise FileNotFoundError(f"proposal does not exist: {proposal_id_or_path}")

    def list(
        self,
        *,
        state: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[Proposal]:
        if not self.db_path.exists():
            return []
        clauses: list[str] = []
        params: list[object] = []
        if state:
            clauses.append("state = ?")
            params.append(state)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT row_json FROM proposals {where} "
                f"ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [Proposal.from_dict(json.loads(row["row_json"])) for row in rows]

    def approve(
        self,
        proposal_id: str,
        *,
        reason: str = "",
        decided_by: str = "local-user",
    ) -> Proposal:
        return self._decide(proposal_id, APPROVED, reason=reason, decided_by=decided_by)

    def reject(
        self,
        proposal_id: str,
        *,
        reason: str = "",
        decided_by: str = "local-user",
    ) -> Proposal:
        return self._decide(proposal_id, REJECTED, reason=reason, decided_by=decided_by)

    def counts_by_state(self) -> dict[str, int]:
        if not self.db_path.exists():
            return {s: 0 for s in VALID_STATES}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT state, COUNT(*) AS n FROM proposals GROUP BY state"
            ).fetchall()
        out = {s: 0 for s in VALID_STATES}
        for row in rows:
            out[row["state"]] = int(row["n"])
        return out

    # ------- internals -----------------------------------------------------

    def _decide(
        self,
        proposal_id: str,
        new_state: str,
        *,
        reason: str,
        decided_by: str,
    ) -> Proposal:
        proposal = self._fetch(proposal_id)
        if proposal is None:
            raise FileNotFoundError(f"proposal does not exist: {proposal_id}")
        proposal.state = new_state
        proposal.reason = reason
        proposal.decided_by = decided_by
        proposal.decided_at = _utcnow()
        self._upsert(proposal)
        return proposal

    def _upsert(self, proposal: Proposal) -> None:
        row_json = json.dumps(proposal.to_dict(), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO proposals (
                    proposal_id, kind, state, confidence, suggested_action,
                    title, source_path, source_task_id, created_at,
                    decided_at, row_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    kind = excluded.kind,
                    state = excluded.state,
                    confidence = excluded.confidence,
                    suggested_action = excluded.suggested_action,
                    title = excluded.title,
                    source_path = excluded.source_path,
                    source_task_id = excluded.source_task_id,
                    decided_at = excluded.decided_at,
                    row_json = excluded.row_json
                """,
                (
                    proposal.proposal_id,
                    proposal.kind,
                    proposal.state,
                    proposal.confidence,
                    proposal.suggested_action,
                    proposal.title,
                    proposal.source_path,
                    proposal.source_task_id,
                    proposal.created_at,
                    proposal.decided_at,
                    row_json,
                ),
            )
            conn.commit()

    def _fetch(self, proposal_id: str) -> Proposal | None:
        if not self.db_path.exists():
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT row_json FROM proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            return None
        return Proposal.from_dict(json.loads(row["row_json"]))

    def _render_card(self, proposal: Proposal) -> dict[str, str]:
        card_dir = self._safe_path(".omni/proposals")
        card_dir.mkdir(parents=True, exist_ok=True)
        json_path = card_dir / f"{proposal.proposal_id}.json"
        markdown_path = card_dir / f"{proposal.proposal_id}.md"
        json_path.write_text(
            json.dumps(proposal.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        markdown_path.write_text(
            render_proposal_markdown(proposal), encoding="utf-8"
        )
        return {
            "proposal_json_path": str(json_path.relative_to(self.workspace)),
            "proposal_markdown_path": str(markdown_path.relative_to(self.workspace)),
        }

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA busy_timeout = 30000;

                CREATE TABLE IF NOT EXISTS proposals (
                    proposal_id      TEXT PRIMARY KEY,
                    kind             TEXT NOT NULL,
                    state            TEXT NOT NULL DEFAULT 'pending',
                    confidence       REAL NOT NULL DEFAULT 0.0,
                    suggested_action TEXT NOT NULL DEFAULT '',
                    title            TEXT NOT NULL DEFAULT '',
                    source_path      TEXT NOT NULL DEFAULT '',
                    source_task_id   TEXT,
                    created_at       TEXT NOT NULL,
                    decided_at       TEXT,
                    row_json         TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_proposals_state
                    ON proposals(state, kind, created_at DESC);
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        from ._storage import connect_sqlite_store
        return connect_sqlite_store(self.db_path)

    def _safe_path(self, relative_path: str) -> Path:
        from ._storage import safe_workspace_path
        return safe_workspace_path(self.workspace, relative_path)


# ---------------------------------------------------------------------------
# Knowledge factory + helpers (was build_knowledge_proposal etc.)
# ---------------------------------------------------------------------------


def build_knowledge_proposal(
    document: MarkdownDocument,
    *,
    source_task_id: str | None = None,
) -> Proposal:
    summary = build_summary(document)
    proposal_id = proposal_id_for_document(document)
    entities = extract_entity_proposals(document)
    relations = extract_relation_proposals(document, entities)

    return Proposal(
        proposal_id=proposal_id,
        kind="knowledge",
        state=PENDING,
        title=document.title,
        summary=summary,
        source_path=document.path,
        source_task_id=source_task_id,
        suggested_action="digest_into_memory",
        confidence=0.7,
        payload={
            "entities": [entity.to_dict() for entity in entities],
            "relations": [relation.to_dict() for relation in relations],
        },
    )


def build_summary(document: MarkdownDocument) -> str:
    excerpt = plain_text_excerpt(document.body, max_chars=900)
    if not excerpt:
        return f"{document.title}：暂无可提取正文。"
    if len(excerpt) <= 240:
        return excerpt
    return excerpt[:240].rstrip() + "..."


def proposal_id_for_document(document: MarkdownDocument) -> str:
    payload = f"{document.path}\n{document.title}\n{document.body}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def extract_entity_proposals(document: MarkdownDocument) -> list[EntityProposal]:
    candidates: dict[str, EntityProposal] = {}

    def add(name: str, kind: str, evidence: str, confidence: float) -> None:
        clean_name = name.strip()
        if not clean_name:
            return
        existing = candidates.get(clean_name)
        if existing is None or confidence > existing.confidence:
            candidates[clean_name] = EntityProposal(
                name=clean_name,
                kind=kind,
                evidence=evidence,
                confidence=confidence,
            )

    add(document.title, "topic", "document title", 0.75)

    for tag in document.tags:
        add(tag, "tag", f"tag #{tag}", 0.85)

    for wiki_link in document.wiki_links:
        add(wiki_link, "wiki_link", f"wiki link [[{wiki_link}]]", 0.85)

    text = f"{document.title}\n{document.body}"
    lowered = text.lower()
    for key, canonical_name in KNOWN_ENTITY_TERMS.items():
        if key in lowered or key in text:
            add(canonical_name, "known_term", canonical_name, 0.7)

    for label in _capitalized_terms(text):
        add(label, "name", label, 0.55)

    return sorted(
        candidates.values(),
        key=lambda entity: (-entity.confidence, entity.name),
    )


def extract_relation_proposals(
    document: MarkdownDocument,
    entities: list[EntityProposal],
) -> list[RelationProposal]:
    relations: list[RelationProposal] = []

    for entity in entities:
        if entity.name == document.title:
            continue
        relations.append(
            RelationProposal(
                source=document.title,
                relation="mentions",
                target=entity.name,
                evidence=entity.evidence,
                confidence=min(entity.confidence, 0.75),
            )
        )

    for link in document.markdown_links:
        relations.append(
            RelationProposal(
                source=document.title,
                relation="links_to",
                target=link["url"],
                evidence=link["label"],
                confidence=0.65,
            )
        )

    return relations


def _capitalized_terms(text: str) -> list[str]:
    terms = re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", text)
    ignored = {"Source", "Kind", "Content", "Extracted", "Text", "Next", "Actions"}
    return sorted({term for term in terms if term not in ignored})


# ---------------------------------------------------------------------------
# Markdown render — Obsidian-friendly card
# ---------------------------------------------------------------------------


def render_proposal_markdown(proposal: Proposal) -> str:
    lines = [
        "---",
        "omni_type: proposal",
        f"proposal_id: {json.dumps(proposal.proposal_id, ensure_ascii=False)}",
        f"kind: {json.dumps(proposal.kind, ensure_ascii=False)}",
        f"state: {json.dumps(proposal.state, ensure_ascii=False)}",
        f"source_path: {json.dumps(proposal.source_path, ensure_ascii=False)}",
        f"created_at: {json.dumps(proposal.created_at, ensure_ascii=False)}",
        "---",
        "",
        f"# Proposal: {proposal.title or proposal.proposal_id}",
        "",
        "## Summary",
        "",
        proposal.summary or "(empty)",
        "",
    ]

    if proposal.kind == "knowledge":
        entities = proposal.payload.get("entities", [])
        relations = proposal.payload.get("relations", [])
        lines.extend(["## Entity Proposals", ""])
        if entities:
            for entity in entities:
                lines.append(
                    f"- {entity['name']} | {entity['kind']} | "
                    f"confidence={float(entity['confidence']):.2f} | {entity['evidence']}"
                )
        else:
            lines.append("- 暂无")

        lines.extend(["", "## Relation Proposals", ""])
        if relations:
            for relation in relations:
                lines.append(
                    f"- {relation['source']} --{relation['relation']}--> {relation['target']} "
                    f"| confidence={float(relation['confidence']):.2f} | {relation['evidence']}"
                )
        else:
            lines.append("- 暂无")
    else:
        lines.extend([f"## kind = {proposal.kind}", ""])
        if proposal.suggested_action:
            lines.append(f"- suggested action: {proposal.suggested_action}")
        if proposal.source_paths:
            lines.append(f"- source paths: {', '.join(proposal.source_paths)}")
        if proposal.confidence:
            lines.append(f"- confidence: {proposal.confidence:.2f}")

    lines.extend(["", "## Review", "", "- [ ] 接受", "- [ ] 修改", "- [ ] 拒绝"])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Redundancy factories (replaces harness/redundancy.RedundancyProposal)
# ---------------------------------------------------------------------------


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def duplicate_proposal(
    hits: list[Any],  # graphiti_bridge.KnowledgeRecord — duck-typed
) -> Proposal:
    return Proposal(
        proposal_id=_stable_id("dup", *(r.source_path for r in hits)),
        kind="duplicate",
        title=hits[0].title[:80] if hits else "",
        summary=f"{len(hits)} records share title '{hits[0].title[:80]}'."
        if hits else "duplicate",
        source_paths=[r.source_path for r in hits],
        confidence=0.85,
        suggested_action="merge_proposal",
    )


def stale_proposal(record: Any, freshness_days: int) -> Proposal:
    return Proposal(
        proposal_id=_stable_id("stale", record.source_path, record.updated_at),
        kind="stale",
        title=record.title[:80],
        summary=(
            f"'{record.title[:80]}' last updated {record.updated_at}, "
            f"older than {freshness_days} days."
        ),
        source_path=record.source_path,
        source_paths=[record.source_path],
        confidence=0.6,
        suggested_action="archive_proposal",
    )


def conflict_proposal(hits: list[Any]) -> Proposal:
    return Proposal(
        proposal_id=_stable_id("conflict", *(r.source_path for r in hits)),
        kind="conflict",
        title=hits[0].title[:80] if hits else "",
        summary=(
            f"{len(hits)} records share title '{hits[0].title[:80]}' "
            f"but differ in summary — review for conflict."
        ),
        source_paths=[r.source_path for r in hits],
        confidence=0.65,
        suggested_action="review_proposal",
    )


def low_signal_proposal(record: Any, report: Any) -> Proposal:
    return Proposal(
        proposal_id=_stable_id("lowsignal", record.source_path),
        kind="low_signal",
        title=record.title[:80],
        summary=(
            f"'{record.title[:80]}' summary has {report.low_signal_claims}/"
            f"{report.total_claims} low-signal claims."
        ),
        source_path=record.source_path,
        source_paths=[record.source_path],
        confidence=0.55,
        suggested_action="demote_proposal",
    )
