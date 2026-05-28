"""v0.18-J Graph projection — entities (local) + communities (global).

GraphRAG-style dual-level graph from ClaimLedger.  Stdlib only — no
NetworkX, no community detection lib;  we hand-roll BFS connected
components + lightweight community summaries from claim statements.

* **graph_entities** (local mode): one node per unique entity / canonical_id
  seen in claim.support / claim.against / claim.statement (capitalised
  noun heuristic).  Edges connect entities co-cited in the same claim.
* **graph_communities** (global mode): connected components from the
  entities graph;  each component is a "community" with a 1-sentence
  summary stitched from its claims' statements.

Persisted as JSON under ``.omni/graph/<snapshot_id>.json`` with an
``.omni/graph/current.json`` symlink (atomic rename — Iceberg pattern).
"""

from __future__ import annotations

import json
import re
import secrets
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._storage import safe_workspace_path
from .knowledge_plane import CLAIM_LEDGER_PATH


GRAPH_DIR_REL = ".omni/graph"
GRAPH_SCHEMA_VERSION = "v0.18"

# Capitalised words that aren't generic — heuristic for entity extraction.
_ENTITY_TOKEN_RE = re.compile(r"\b([A-Z][a-zA-Z0-9_-]{2,}(?:\s+[A-Z][a-zA-Z0-9_-]{2,})*)\b")
_STOPWORD_TITLES = {
    "The", "This", "That", "These", "Those",
    "When", "While", "Where", "What", "Which",
    "However", "Moreover", "Therefore",
}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _new_snapshot_id() -> str:
    return f"graph_{int(time.time() * 1000):x}_{secrets.token_hex(4)}"


@dataclass(slots=True)
class GraphNode:
    node_id: str                      # canonical_id or slugified entity
    label: str
    kind: str                         # "entity" | "canonical_source"
    domains: list[str] = field(default_factory=list)
    claim_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GraphEdge:
    source: str
    target: str
    relation: str                     # "co_cited" | "supports" | "supersedes"
    weight: int = 1
    evidence_claim_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GraphCommunity:
    community_id: str
    node_ids: list[str]
    summary: str
    domain: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GraphSnapshot:
    snapshot_id: str
    schema_version: str
    built_at: str
    built_from_claim_count: int
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    communities: list[GraphCommunity] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "schema_version": self.schema_version,
            "built_at": self.built_at,
            "built_from_claim_count": self.built_from_claim_count,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "community_count": len(self.communities),
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "communities": [c.to_dict() for c in self.communities],
        }


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def rebuild_graph(workspace: Path | str = ".") -> GraphSnapshot:
    """Rebuild from ClaimLedger.  Atomic snapshot under .omni/graph/."""

    workspace_root = Path(workspace).resolve()
    graph_dir = safe_workspace_path(workspace_root, GRAPH_DIR_REL)
    graph_dir.mkdir(parents=True, exist_ok=True)

    claims = _load_open_claims(workspace_root)
    snapshot = _build_snapshot(claims)
    target = graph_dir / f"{snapshot.snapshot_id}.json"
    target.write_text(
        json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Atomic swap of the "current" pointer (rename JSON file).
    current = graph_dir / "current.json"
    tmp = graph_dir / "current.json.tmp"
    tmp.write_text(json.dumps({"snapshot_id": snapshot.snapshot_id,
                                "target": target.name}),
                    encoding="utf-8")
    tmp.replace(current)
    return snapshot


def _build_snapshot(claims: list[dict[str, Any]]) -> GraphSnapshot:
    nodes_by_id: dict[str, GraphNode] = {}
    co_cited: dict[tuple[str, str], GraphEdge] = {}

    def _ensure_node(node_id: str, label: str, kind: str, domain: str) -> GraphNode:
        node = nodes_by_id.get(node_id)
        if node is None:
            node = GraphNode(node_id=node_id, label=label, kind=kind)
            nodes_by_id[node_id] = node
        node.claim_count += 1
        if domain and domain not in node.domains:
            node.domains.append(domain)
        return node

    for claim in claims:
        cid = str(claim.get("claim_id", ""))
        domain = str(claim.get("domain", "")).strip()
        statement = str(claim.get("statement", ""))

        # Canonical source nodes from support[] entries
        local_ids: list[str] = []
        for ref in claim.get("support", []) or []:
            if not isinstance(ref, dict):
                continue
            canonical = str(ref.get("source_id") or ref.get("canonical_id") or "").strip()
            if not canonical:
                continue
            _ensure_node(canonical, canonical[:64], "canonical_source", domain)
            local_ids.append(canonical)

        # Heuristic entity extraction from statement
        for raw in _extract_entities(statement):
            slug = _slug(raw)
            if not slug:
                continue
            _ensure_node(slug, raw, "entity", domain)
            local_ids.append(slug)

        # Co-citation edges between every pair in local_ids
        for a, b in _unique_pairs(local_ids):
            key = (a, b) if a < b else (b, a)
            edge = co_cited.get(key)
            if edge is None:
                edge = GraphEdge(source=key[0], target=key[1], relation="co_cited")
                co_cited[key] = edge
            edge.weight += 1
            if cid and cid not in edge.evidence_claim_ids:
                edge.evidence_claim_ids.append(cid)

        # Supersedes edges
        for older in claim.get("supersedes", []) or []:
            if isinstance(older, str) and older:
                co_cited[("supersede", cid + ":" + older)] = GraphEdge(
                    source=cid, target=str(older), relation="supersedes",
                    weight=1, evidence_claim_ids=[cid],
                )

    nodes = sorted(nodes_by_id.values(), key=lambda n: (-n.claim_count, n.node_id))
    edges = list(co_cited.values())
    communities = _detect_communities(nodes, edges, claims)

    return GraphSnapshot(
        snapshot_id=_new_snapshot_id(),
        schema_version=GRAPH_SCHEMA_VERSION,
        built_at=_utcnow(),
        built_from_claim_count=len(claims),
        nodes=nodes,
        edges=edges,
        communities=communities,
    )


def _detect_communities(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    claims: list[dict[str, Any]],
) -> list[GraphCommunity]:
    """BFS connected-component detection.  Each component gets a summary
    stitched from the first 2-3 claim statements that touch it."""

    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if edge.relation == "co_cited":
            adjacency[edge.source].add(edge.target)
            adjacency[edge.target].add(edge.source)

    node_ids = {n.node_id for n in nodes}
    seen: set[str] = set()
    communities: list[GraphCommunity] = []

    # Pre-index claims by canonical/entity for summary stitching
    claim_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        statement = str(claim.get("statement", ""))
        domain = str(claim.get("domain", ""))
        for ref in claim.get("support", []) or []:
            if isinstance(ref, dict):
                cid = str(ref.get("source_id") or ref.get("canonical_id") or "")
                if cid:
                    claim_by_node[cid].append({"statement": statement, "domain": domain})
        for raw in _extract_entities(statement):
            slug = _slug(raw)
            if slug:
                claim_by_node[slug].append({"statement": statement, "domain": domain})

    for start in node_ids:
        if start in seen:
            continue
        component: list[str] = []
        queue = [start]
        while queue:
            node = queue.pop()
            if node in seen:
                continue
            seen.add(node)
            component.append(node)
            queue.extend(adjacency.get(node, set()))
        if len(component) < 2:
            continue                                    # singleton is not a community
        # Stitch summary from up to 3 claim statements
        statements: list[str] = []
        domains: set[str] = set()
        for nid in component:
            for entry in claim_by_node.get(nid, [])[:2]:
                s = entry["statement"].strip()
                if s and s not in statements:
                    statements.append(s)
                if entry["domain"]:
                    domains.add(entry["domain"])
            if len(statements) >= 3:
                break
        summary = " · ".join(statements[:3])[:400]
        communities.append(GraphCommunity(
            community_id=f"comm_{_slug(component[0])[:24]}",
            node_ids=sorted(component),
            summary=summary,
            domain=(sorted(domains)[0] if domains else ""),
        ))
    communities.sort(key=lambda c: -len(c.node_ids))
    return communities


def query_neighbours(
    workspace: Path | str = ".",
    *,
    node_id: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Neighbour query against the current graph snapshot.  Local mode."""

    workspace_root = Path(workspace).resolve()
    snap = _load_current_graph(workspace_root)
    if snap is None:
        return {"node_id": node_id, "neighbours": [], "snapshot_id": None}
    edges = [
        e for e in snap.edges
        if (e.source == node_id or e.target == node_id) and e.relation == "co_cited"
    ]
    edges.sort(key=lambda e: -e.weight)
    return {
        "node_id": node_id,
        "snapshot_id": snap.snapshot_id,
        "neighbours": [
            {
                "node_id": e.target if e.source == node_id else e.source,
                "weight": e.weight,
                "evidence_claim_ids": e.evidence_claim_ids[:5],
            }
            for e in edges[:limit]
        ],
    }


def query_community(
    workspace: Path | str = ".",
    *,
    node_id: str | None = None,
    community_id: str | None = None,
) -> dict[str, Any]:
    """Global mode — return a community by id or the one containing node_id."""

    workspace_root = Path(workspace).resolve()
    snap = _load_current_graph(workspace_root)
    if snap is None:
        return {"community": None, "snapshot_id": None}
    target = None
    if community_id:
        target = next((c for c in snap.communities if c.community_id == community_id), None)
    elif node_id:
        target = next((c for c in snap.communities if node_id in c.node_ids), None)
    return {
        "community": target.to_dict() if target else None,
        "snapshot_id": snap.snapshot_id,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_open_claims(workspace: Path) -> list[dict[str, Any]]:
    ledger = workspace / CLAIM_LEDGER_PATH
    if not ledger.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            claim = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Skip closed claims (matches default search behaviour)
        if claim.get("t_valid_to") is not None:
            continue
        if str(claim.get("review_state", "")).lower() in {"rejected", "superseded"}:
            continue
        out.append(claim)
    return out


def _extract_entities(text: str) -> list[str]:
    entities: list[str] = []
    for match in _ENTITY_TOKEN_RE.finditer(text or ""):
        token = match.group(1).strip()
        first_word = token.split()[0] if token else ""
        if first_word in _STOPWORD_TITLES:
            continue
        if token and token not in entities:
            entities.append(token)
    return entities[:8]            # cap per statement


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _unique_pairs(items: list[str]) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    items = sorted(set(items))
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a != b:
                seen.append((a, b))
    return seen


def _load_current_graph(workspace: Path) -> GraphSnapshot | None:
    pointer = workspace / GRAPH_DIR_REL / "current.json"
    if not pointer.exists():
        return None
    try:
        info = json.loads(pointer.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    target_name = info.get("target")
    if not target_name:
        return None
    target = workspace / GRAPH_DIR_REL / target_name
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return GraphSnapshot(
        snapshot_id=data.get("snapshot_id", ""),
        schema_version=data.get("schema_version", GRAPH_SCHEMA_VERSION),
        built_at=data.get("built_at", ""),
        built_from_claim_count=int(data.get("built_from_claim_count", 0)),
        nodes=[GraphNode(**n) for n in data.get("nodes", [])],
        edges=[GraphEdge(**e) for e in data.get("edges", [])],
        communities=[GraphCommunity(**c) for c in data.get("communities", [])],
    )
