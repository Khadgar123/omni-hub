"""Redundancy detection that produces *proposals*, never deletes.

Four detector classes (matches `docs/agent-system-development-plan.md`):

    duplicate  — title/summary near-identical
    stale      — updated_at older than a freshness window
    conflict   — same title, different summary (potential conflict)
    low_signal — summary dominated by low-signal phrases (uses grounding)

Each match becomes a ``RedundancyProposal`` written to
``.omni/proposals/redundancy.jsonl``.  Nothing in the memory store is ever
deleted by this module — only a human-approved follow-up may delete.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from . import graphiti_bridge, grounding


@dataclass(slots=True)
class RedundancyProposal:
    proposal_id: str
    kind: str          # duplicate | stale | conflict | low_signal
    source_paths: list[str]
    summary: str
    confidence: float  # 0..1
    suggested_action: str  # merge_proposal | archive_proposal | review_proposal | demote_proposal
    detected_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(text: str) -> str:
    # cheap normalisation: lower + collapse whitespace + strip punct boundaries
    return re.sub(r"\s+", " ", (text or "").lower().strip())


def _hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()[:12]


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def _duplicate_proposals(
    records: list[graphiti_bridge.KnowledgeRecord],
) -> list[RedundancyProposal]:
    by_title: dict[str, list[graphiti_bridge.KnowledgeRecord]] = {}
    for r in records:
        key = _hash(r.title)
        by_title.setdefault(key, []).append(r)
    out: list[RedundancyProposal] = []
    for hits in by_title.values():
        if len(hits) < 2:
            continue
        summaries = {_hash(r.summary) for r in hits if r.summary}
        if len(summaries) <= 1:
            # near-identical title + summary → strong dup
            out.append(RedundancyProposal(
                proposal_id=_stable_id("dup", *(r.source_path for r in hits)),
                kind="duplicate",
                source_paths=[r.source_path for r in hits],
                summary=f"{len(hits)} records share title '{hits[0].title[:80]}'.",
                confidence=0.85,
                suggested_action="merge_proposal",
                detected_at=_now(),
            ))
    return out


def _stale_proposals(
    records: list[graphiti_bridge.KnowledgeRecord],
    *,
    freshness_days: int,
) -> list[RedundancyProposal]:
    threshold = datetime.now(timezone.utc) - timedelta(days=freshness_days)
    out: list[RedundancyProposal] = []
    for r in records:
        if not r.updated_at:
            continue
        try:
            ts = datetime.fromisoformat(r.updated_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < threshold:
            out.append(RedundancyProposal(
                proposal_id=_stable_id("stale", r.source_path, r.updated_at),
                kind="stale",
                source_paths=[r.source_path],
                summary=(
                    f"'{r.title[:80]}' last updated {r.updated_at}, "
                    f"older than {freshness_days} days."
                ),
                confidence=0.6,
                suggested_action="archive_proposal",
                detected_at=_now(),
            ))
    return out


def _conflict_proposals(
    records: list[graphiti_bridge.KnowledgeRecord],
) -> list[RedundancyProposal]:
    by_title: dict[str, list[graphiti_bridge.KnowledgeRecord]] = {}
    for r in records:
        by_title.setdefault(_hash(r.title), []).append(r)
    out: list[RedundancyProposal] = []
    for hits in by_title.values():
        if len(hits) < 2:
            continue
        summaries = {_hash(r.summary) for r in hits if r.summary}
        if len(summaries) >= 2:
            out.append(RedundancyProposal(
                proposal_id=_stable_id("conflict", *(r.source_path for r in hits)),
                kind="conflict",
                source_paths=[r.source_path for r in hits],
                summary=(
                    f"{len(hits)} records share title '{hits[0].title[:80]}' "
                    f"but differ in summary — review for conflict."
                ),
                confidence=0.65,
                suggested_action="review_proposal",
                detected_at=_now(),
            ))
    return out


def _low_signal_proposals(
    records: list[graphiti_bridge.KnowledgeRecord],
    *,
    min_low_signal_ratio: float,
) -> list[RedundancyProposal]:
    out: list[RedundancyProposal] = []
    for r in records:
        if not r.summary:
            continue
        report = grounding.analyze_grounding(r.summary)
        if report.total_claims == 0:
            continue
        if report.nugget_density < (1.0 - min_low_signal_ratio):
            out.append(RedundancyProposal(
                proposal_id=_stable_id("lowsignal", r.source_path),
                kind="low_signal",
                source_paths=[r.source_path],
                summary=(
                    f"'{r.title[:80]}' summary has {report.low_signal_claims}/"
                    f"{report.total_claims} low-signal claims."
                ),
                confidence=0.55,
                suggested_action="demote_proposal",
                detected_at=_now(),
            ))
    return out


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RedundancyScanReport:
    backend: str
    documents_scanned: int
    proposals: list[RedundancyProposal] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "documents_scanned": self.documents_scanned,
            "proposals": [p.to_dict() for p in self.proposals],
            "by_kind": self.counts_by_kind(),
        }

    def counts_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.proposals:
            out[p.kind] = out.get(p.kind, 0) + 1
        return out


def scan(
    *,
    db_path: Path | str = ".omni/memory.sqlite3",
    prefer_backend: str = "auto",
    freshness_days: int = 365,
    min_low_signal_ratio: float = 0.5,
    write_to: Path | str | None = ".omni/proposals/redundancy.jsonl",
    max_documents: int = 5000,
) -> RedundancyScanReport:
    backend = graphiti_bridge.get_backend(prefer=prefer_backend, db_path=db_path)
    records: list[graphiti_bridge.KnowledgeRecord] = []
    for record in graphiti_bridge.iter_all_documents(
        prefer_backend=prefer_backend, db_path=db_path, page_size=200,
    ):
        records.append(record)
        if len(records) >= max_documents:
            break

    proposals: list[RedundancyProposal] = []
    proposals.extend(_duplicate_proposals(records))
    proposals.extend(_stale_proposals(records, freshness_days=freshness_days))
    proposals.extend(_conflict_proposals(records))
    proposals.extend(_low_signal_proposals(records, min_low_signal_ratio=min_low_signal_ratio))

    if write_to:
        path = Path(write_to)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for prop in proposals:
                fh.write(json.dumps(prop.to_dict(), ensure_ascii=False) + "\n")

    return RedundancyScanReport(
        backend=backend.name,
        documents_scanned=len(records),
        proposals=proposals,
    )


def load_proposals(
    *, path: Path | str = ".omni/proposals/redundancy.jsonl",
) -> Iterable[RedundancyProposal]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[RedundancyProposal] = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(RedundancyProposal(**json.loads(line)))
    return out
