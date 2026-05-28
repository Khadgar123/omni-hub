"""Per-user 3-tier memory (v0.31).

Letta-style MemGPT layering:

* **core** — the persona / handle / style prefs already live in
  :class:`UserProfile.persona_block` (capped 4 KB).  Treated as RAM.
* **recall** — recent conversation summaries; per-user JSONL under
  ``vault/users/<user_id>/recall/<YYYY-MM>.jsonl``.  Treated as disk cache.
* **archival** — full searchable history; per-user JSONL under
  ``vault/users/<user_id>/archival/<YYYY-MM>.jsonl``.  Tool-queried only.

Tier promotion (Letta 2024 paper):

    archival ←─ promote (high-confidence) ── recall ←─ promote ── core

omni-hub keeps the **promote** step human-gated via Proposal[T] —
agents may read all three tiers but writes to core go through
``user-set-persona-block`` which is a LOCAL_WRITE op + audit trail.

The store is intentionally JSONL (not SQLite) to keep per-user
state portable: a user can be exported by ``tar`` of
``vault/users/<user_id>/``.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


class MemoryTier(str, Enum):
    CORE = "core"               # persona block (lives in UserProfile.persona_block)
    RECALL = "recall"           # recent session summaries
    ARCHIVAL = "archival"       # full searchable history


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class RecallEntry:
    """One recall-tier item (recent session summary)."""

    user_id: str
    summary: str                # short markdown summary
    skill_id: str = ""          # which skill produced this turn
    trace_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ArchivalEntry:
    """One archival-tier item (full message + tagged metadata)."""

    user_id: str
    role: str                   # "user" | "agent"
    body: str
    skill_id: str = ""
    trace_id: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PerUserMemoryStore:
    """Per-user recall + archival store (JSONL on disk)."""

    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace).resolve()

    def _user_root(self, user_id: str) -> Path:
        return self.workspace / "vault" / "users" / user_id

    def _tier_dir(self, user_id: str, tier: MemoryTier) -> Path:
        if tier is MemoryTier.CORE:
            raise ValueError(
                "core memory lives in UserProfile.persona_block; use "
                "UserProfileStore.set_persona_block instead"
            )
        target = self._user_root(user_id) / tier.value
        target.mkdir(parents=True, exist_ok=True)
        return target

    @staticmethod
    def _current_month_file(tier_dir: Path) -> Path:
        return tier_dir / f"{datetime.now(UTC).strftime('%Y-%m')}.jsonl"

    # ---- recall ------------------------------------------------

    def append_recall(self, entry: RecallEntry) -> Path:
        target = self._current_month_file(self._tier_dir(entry.user_id, MemoryTier.RECALL))
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        return target

    def list_recall(
        self, user_id: str, *, limit: int = 50,
    ) -> list[RecallEntry]:
        tier_dir = self._user_root(user_id) / MemoryTier.RECALL.value
        if not tier_dir.exists():
            return []
        out: list[RecallEntry] = []
        # Iterate newest-first across month-rolled files.
        for monthly in sorted(tier_dir.glob("*.jsonl"), reverse=True):
            for line in reversed(monthly.read_text(encoding="utf-8").splitlines()):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                out.append(RecallEntry(**data))
                if len(out) >= limit:
                    return out
        return out

    # ---- archival ----------------------------------------------

    def append_archival(self, entry: ArchivalEntry) -> Path:
        target = self._current_month_file(self._tier_dir(entry.user_id, MemoryTier.ARCHIVAL))
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        return target

    def search_archival(
        self,
        user_id: str,
        query: str,
        *,
        limit: int = 20,
    ) -> list[ArchivalEntry]:
        """Case-insensitive substring search across all monthly files.

        v0.31 keeps this stdlib + lexical; v0.40+ can swap to FTS5 once
        per-user archives grow.
        """

        tier_dir = self._user_root(user_id) / MemoryTier.ARCHIVAL.value
        if not tier_dir.exists() or not query.strip():
            return []
        needle = query.lower()
        out: list[ArchivalEntry] = []
        for monthly in sorted(tier_dir.glob("*.jsonl"), reverse=True):
            for line in reversed(monthly.read_text(encoding="utf-8").splitlines()):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if needle in (data.get("body") or "").lower():
                    out.append(ArchivalEntry(**data))
                    if len(out) >= limit:
                        return out
        return out

    # ---- tier-aware summary ------------------------------------

    def overview(self, user_id: str) -> dict[str, int]:
        """Counts per tier — used by ``user-status`` CLI."""

        return {
            "recall_files": len(list((self._user_root(user_id)
                                      / MemoryTier.RECALL.value).glob("*.jsonl")))
                            if (self._user_root(user_id) / MemoryTier.RECALL.value).exists() else 0,
            "archival_files": len(list((self._user_root(user_id)
                                        / MemoryTier.ARCHIVAL.value).glob("*.jsonl")))
                              if (self._user_root(user_id) / MemoryTier.ARCHIVAL.value).exists() else 0,
            "archival_entries": self._count_entries(user_id, MemoryTier.ARCHIVAL),
            "recall_entries": self._count_entries(user_id, MemoryTier.RECALL),
        }

    def _count_entries(self, user_id: str, tier: MemoryTier) -> int:
        tier_dir = self._user_root(user_id) / tier.value
        if not tier_dir.exists():
            return 0
        return sum(
            sum(1 for _ in monthly.read_text(encoding="utf-8").splitlines() if _)
            for monthly in tier_dir.glob("*.jsonl")
        )


__all__ = [
    "ArchivalEntry",
    "MemoryTier",
    "PerUserMemoryStore",
    "RecallEntry",
]
