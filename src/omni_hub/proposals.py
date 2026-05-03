from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

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


@dataclass(slots=True)
class KnowledgeProposal:
    proposal_id: str
    source_path: str
    title: str
    summary: str
    entities: list[EntityProposal] = field(default_factory=list)
    relations: list[RelationProposal] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["entities"] = [entity.to_dict() for entity in self.entities]
        data["relations"] = [relation.to_dict() for relation in self.relations]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "KnowledgeProposal":
        entity_data = data.get("entities", [])
        relation_data = data.get("relations", [])
        return cls(
            proposal_id=str(data["proposal_id"]),
            source_path=str(data["source_path"]),
            title=str(data["title"]),
            summary=str(data["summary"]),
            entities=[
                EntityProposal.from_dict(item)
                for item in entity_data
                if isinstance(item, dict)
            ],
            relations=[
                RelationProposal.from_dict(item)
                for item in relation_data
                if isinstance(item, dict)
            ],
            created_at=str(data.get("created_at", datetime.now(UTC).isoformat())),
        )


class ProposalStore:
    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace).resolve()

    def store(self, proposal: KnowledgeProposal) -> dict[str, str]:
        proposal_dir = self._safe_path(".omni/proposals")
        proposal_dir.mkdir(parents=True, exist_ok=True)

        json_path = proposal_dir / f"{proposal.proposal_id}.json"
        markdown_path = proposal_dir / f"{proposal.proposal_id}.md"
        json_path.write_text(
            json.dumps(proposal.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        markdown_path.write_text(render_proposal_markdown(proposal), encoding="utf-8")

        return {
            "proposal_json_path": str(json_path.relative_to(self.workspace)),
            "proposal_markdown_path": str(markdown_path.relative_to(self.workspace)),
        }

    def load(self, proposal_id_or_path: str) -> KnowledgeProposal:
        path = self._resolve_proposal_path(proposal_id_or_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("proposal JSON must contain an object")
        return KnowledgeProposal.from_dict(data)

    def _resolve_proposal_path(self, proposal_id_or_path: str) -> Path:
        value = proposal_id_or_path.strip()
        if not value:
            raise ValueError("proposal id or path is required")

        if value.endswith(".json") or "/" in value:
            path = self._safe_path(value)
        else:
            path = self._safe_path(f".omni/proposals/{value}.json")

        if not path.exists():
            raise FileNotFoundError(f"proposal does not exist: {proposal_id_or_path}")
        return path

    def _safe_path(self, relative_path: str) -> Path:
        target = (self.workspace / relative_path).resolve()
        try:
            target.relative_to(self.workspace)
        except ValueError as exc:
            raise PermissionError("target path is outside the workspace") from exc
        return target


def build_knowledge_proposal(document: MarkdownDocument) -> KnowledgeProposal:
    summary = build_summary(document)
    proposal_id = proposal_id_for_document(document)
    entities = extract_entity_proposals(document)
    relations = extract_relation_proposals(document, entities)

    return KnowledgeProposal(
        proposal_id=proposal_id,
        source_path=document.path,
        title=document.title,
        summary=summary,
        entities=entities,
        relations=relations,
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

    return sorted(candidates.values(), key=lambda entity: (-entity.confidence, entity.name))


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


def render_proposal_markdown(proposal: KnowledgeProposal) -> str:
    lines = [
        "---",
        "omni_type: knowledge_proposal",
        f"proposal_id: {json.dumps(proposal.proposal_id, ensure_ascii=False)}",
        f"source_path: {json.dumps(proposal.source_path, ensure_ascii=False)}",
        f"created_at: {json.dumps(proposal.created_at, ensure_ascii=False)}",
        "---",
        "",
        f"# Proposal: {proposal.title}",
        "",
        "## Summary",
        "",
        proposal.summary,
        "",
        "## Entity Proposals",
        "",
    ]

    if proposal.entities:
        for entity in proposal.entities:
            lines.append(
                f"- {entity.name} | {entity.kind} | confidence={entity.confidence:.2f} | {entity.evidence}"
            )
    else:
        lines.append("- 暂无")

    lines.extend(["", "## Relation Proposals", ""])
    if proposal.relations:
        for relation in proposal.relations:
            lines.append(
                f"- {relation.source} --{relation.relation}--> {relation.target} "
                f"| confidence={relation.confidence:.2f} | {relation.evidence}"
            )
    else:
        lines.append("- 暂无")

    lines.extend(["", "## Review", "", "- [ ] 接受", "- [ ] 修改", "- [ ] 拒绝"])
    return "\n".join(lines) + "\n"


def _capitalized_terms(text: str) -> list[str]:
    terms = re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", text)
    ignored = {"Source", "Kind", "Content", "Extracted", "Text", "Next", "Actions"}
    return sorted({term for term in terms if term not in ignored})
