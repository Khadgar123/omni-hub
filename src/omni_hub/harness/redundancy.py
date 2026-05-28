"""Redundancy detection that produces *proposals*, never deletes.

Four detector kinds (matches `docs/agent-system-development-plan.md`):

    duplicate  — title/summary near-identical
    stale      — updated_at older than a freshness window
    conflict   — same title, different summary (potential conflict)
    low_signal — summary dominated by low-signal phrases (uses grounding)

Each match becomes a ``Proposal`` (unified type from
``omni_hub.proposals``) and is persisted into the SQLite-backed
``ProposalStore`` so the propose-list / approve / reject workflow can pick
them up alongside knowledge proposals.  Nothing in memory is deleted by
this module — only a human-approved follow-up may delete.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from ..proposals import (
    Proposal,
    ProposalStore,
    conflict_proposal,
    duplicate_proposal,
    low_signal_proposal,
    stale_proposal,
)
from . import graphiti_bridge, grounding


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip())


def _hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def _duplicate_proposals(
    records: list[graphiti_bridge.KnowledgeRecord],
) -> list[Proposal]:
    by_title: dict[str, list[graphiti_bridge.KnowledgeRecord]] = {}
    for r in records:
        key = _hash(r.title)
        by_title.setdefault(key, []).append(r)
    out: list[Proposal] = []
    for hits in by_title.values():
        if len(hits) < 2:
            continue
        summaries = {_hash(r.summary) for r in hits if r.summary}
        if len(summaries) <= 1:
            out.append(duplicate_proposal(hits))
    return out


def _stale_proposals(
    records: list[graphiti_bridge.KnowledgeRecord],
    *,
    freshness_days: int,
) -> list[Proposal]:
    threshold = datetime.now(timezone.utc) - timedelta(days=freshness_days)
    out: list[Proposal] = []
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
            out.append(stale_proposal(r, freshness_days))
    return out


def _conflict_proposals(
    records: list[graphiti_bridge.KnowledgeRecord],
) -> list[Proposal]:
    by_title: dict[str, list[graphiti_bridge.KnowledgeRecord]] = {}
    for r in records:
        by_title.setdefault(_hash(r.title), []).append(r)
    out: list[Proposal] = []
    for hits in by_title.values():
        if len(hits) < 2:
            continue
        summaries = {_hash(r.summary) for r in hits if r.summary}
        if len(summaries) >= 2:
            out.append(conflict_proposal(hits))
    return out


def _low_signal_proposals(
    records: list[graphiti_bridge.KnowledgeRecord],
    *,
    min_low_signal_ratio: float,
) -> list[Proposal]:
    out: list[Proposal] = []
    for r in records:
        if not r.summary:
            continue
        report = grounding.analyze_grounding(r.summary)
        if report.total_claims == 0:
            continue
        if report.nugget_density < (1.0 - min_low_signal_ratio):
            out.append(low_signal_proposal(r, report))
    return out


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RedundancyScanReport:
    backend: str
    documents_scanned: int
    proposals: list[Proposal] = field(default_factory=list)

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
    write_to: Path | str | None = None,            # deprecated; ignored
    max_documents: int = 5000,
) -> RedundancyScanReport:
    """Scan + persist proposals into the unified ``ProposalStore``.

    The ``write_to`` keyword used to mirror to ``.omni/proposals/redundancy.jsonl``
    (legacy v0.6 sink).  In v0.7 the SQLite ``ProposalStore`` is the only
    sink — propose-list / approve / reject query that store, and a jsonl
    mirror would just drift out of sync.  ``write_to`` is accepted for
    backwards-compat with old callers but is ignored.
    """

    if write_to is not None:                       # pragma: no cover — soft warn
        import warnings
        warnings.warn(
            "redundancy.scan(write_to=...) is deprecated; the unified "
            "ProposalStore is the canonical sink. Argument ignored.",
            DeprecationWarning,
            stacklevel=2,
        )

    backend = graphiti_bridge.get_backend(prefer=prefer_backend, db_path=db_path)
    records: list[graphiti_bridge.KnowledgeRecord] = []
    for record in graphiti_bridge.iter_all_documents(
        prefer_backend=prefer_backend, db_path=db_path, page_size=200,
    ):
        records.append(record)
        if len(records) >= max_documents:
            break

    proposals: list[Proposal] = []
    proposals.extend(_duplicate_proposals(records))
    proposals.extend(_stale_proposals(records, freshness_days=freshness_days))
    proposals.extend(_conflict_proposals(records))
    proposals.extend(_low_signal_proposals(
        records, min_low_signal_ratio=min_low_signal_ratio,
    ))

    # Canonical sink: SQLite proposal store rooted at the workspace that owns
    # the memory database.  Inferring workspace from db_path matches what
    # callers expect (db lives under <workspace>/.omni/...).
    db = Path(db_path)
    if ".omni" in db.parts:
        workspace = Path(*db.parts[: db.parts.index(".omni")])
    else:
        workspace = db.parent
    store = ProposalStore(workspace=workspace if str(workspace) else ".")
    for prop in proposals:
        store.store(prop, write_card=False)

    return RedundancyScanReport(
        backend=backend.name,
        documents_scanned=len(records),
        proposals=proposals,
    )
