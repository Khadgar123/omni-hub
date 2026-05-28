"""Local preference store, JSONL-on-disk, Argilla-compatible schema.

Why local first
---------------
- Argilla is fork-pinned but spinning it up is heavy (FastAPI + Elastic).
- For Phase-1 we want a *durable*, *queryable* store of human accepted/rejected
  decisions that the DSPy compile loop can consume immediately.
- The on-disk format is a single JSONL file per domain under
  ``.omni/preference/<domain>.jsonl`` so it's easy to inspect, diff, and
  later push to Argilla via a thin sync script.

Each record on disk is one ``PreferenceRecord``:

    {
      "schema_version": 1,
      "record_id": "uuid",
      "task_id": "uuid",
      "domain": "research",
      "prompt_version": "v3",
      "candidate_text": "...",
      "decision": "accepted" | "rejected" | "edited",
      "accepted_spans": ["..."],
      "rejected_spans": ["..."],
      "edited_text": "...",
      "reason": "free text",
      "reviewer": "local-user",
      "judge_summary": {"evidence_coverage": 0.7, ...},
      "created_at": "iso8601"
    }
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator
from uuid import uuid4


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class PreferenceRecord:
    schema_version: int = 1
    record_id: str = field(default_factory=lambda: str(uuid4()))
    task_id: str = ""
    domain: str = "engineering"
    prompt_version: str = "v0"
    candidate_text: str = ""
    decision: str = "accepted"        # accepted | rejected | edited
    accepted_spans: list[str] = field(default_factory=list)
    rejected_spans: list[str] = field(default_factory=list)
    edited_text: str = ""
    reason: str = ""
    reviewer: str = "local-user"
    judge_summary: dict[str, float] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict:
        return asdict(self)


class PreferenceStore:
    """Append-only JSONL store, one file per domain."""

    def __init__(self, root: Path | str = ".omni/preference") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, domain: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in domain)
        return self.root / f"{safe}.jsonl"

    def append(self, record: PreferenceRecord) -> Path:
        path = self._path(record.domain)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_dict(), ensure_ascii=False))
            fh.write("\n")
        return path

    def read(self, domain: str) -> Iterator[PreferenceRecord]:
        path = self._path(domain)
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                yield PreferenceRecord(**raw)

    def stats(self, domain: str) -> dict:
        accepted = rejected = edited = 0
        accepted_spans = rejected_spans = 0
        for rec in self.read(domain):
            if rec.decision == "accepted":
                accepted += 1
            elif rec.decision == "rejected":
                rejected += 1
            elif rec.decision == "edited":
                edited += 1
            accepted_spans += len(rec.accepted_spans)
            rejected_spans += len(rec.rejected_spans)
        return {
            "domain": domain,
            "accepted": accepted,
            "rejected": rejected,
            "edited": edited,
            "accepted_spans": accepted_spans,
            "rejected_spans": rejected_spans,
            "total": accepted + rejected + edited,
        }

    def list_domains(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(
            p.stem for p in self.root.glob("*.jsonl") if p.is_file()
        )

    def export(
        self,
        domain: str,
        *,
        include_decisions: Iterable[str] = ("accepted", "edited"),
        max_records: int | None = None,
    ) -> list[PreferenceRecord]:
        """Materialise records suitable for handing to DSPy compile.

        Defaults to accepted+edited (positive demonstrations).  Passing
        ``include_decisions=("rejected",)`` yields negative examples.
        """

        wanted = set(include_decisions)
        out: list[PreferenceRecord] = []
        for rec in self.read(domain):
            if rec.decision in wanted:
                out.append(rec)
                if max_records and len(out) >= max_records:
                    break
        return out
