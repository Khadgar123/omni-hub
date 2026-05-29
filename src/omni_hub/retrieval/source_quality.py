"""Measured per-source quality — decoupled from cascade priority.

The cascade's ``DEFAULT_DOMAIN_CASCADES`` order is *priority*: which source
we TRY first.  It says nothing about which source is actually BEST.  This
store records observed outcomes so the system can tell the two apart:

* a high-priority source that has gone stale / flaky gets a low score and
  can be demoted;
* a "fallback" (later in the cascade, or a tier-2 paid source) that
  consistently delivers gets a high score and can be promoted.

That is the concrete answer to "降级的不一定差,优先级高的不一定好": rank by
*measured* quality, not by fetch order.

Score = ``success_rate × freshness`` (truth-discovery style, minus the
cross-source agreement term — that lives in the cascade's RRF, where a
record corroborated by N sources already outranks a singleton).  Pure
stdlib, single-file JSON at ``.omni/source_quality.json``, atomic writes.
The write is a side effect of the audited ``retrieve_cascade`` op, so it
stays under policy + audit (HR #1) without being its own write command.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class SourceStat:
    """Rolling outcome counters for one source."""

    source: str
    success: int = 0
    failure: int = 0
    last_success_at: str = ""

    def attempts(self) -> int:
        return self.success + self.failure

    def success_rate(self) -> float:
        n = self.attempts()
        return self.success / n if n else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "success": self.success,
            "failure": self.failure,
            "last_success_at": self.last_success_at,
        }


class SourceQualityStore:
    """Append-cheap key→stat store at ``.omni/source_quality.json``."""

    def __init__(self, workspace: Path | str = ".") -> None:
        self.path = Path(workspace).resolve() / ".omni" / "source_quality.json"

    # ------------------------------------------------------------------ io
    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)  # atomic

    # -------------------------------------------------------------- record
    def record_cascade(
        self,
        *,
        tried: Iterable[str],
        succeeded: Iterable[str],
        at: str | None = None,
    ) -> None:
        """Record one cascade run's per-source outcome.

        Every source in ``tried`` is credited a success (if also in
        ``succeeded``) or a failure (if not).  Sources that timed out /
        errored count as failures, which is exactly the signal we want.
        """

        succ = set(succeeded)
        stamp = at or _utcnow().isoformat()
        data = self._load()
        for name in tried:
            row = data.get(name) or {
                "source": name, "success": 0, "failure": 0, "last_success_at": "",
            }
            if name in succ:
                row["success"] = int(row.get("success", 0)) + 1
                row["last_success_at"] = stamp
            else:
                row["failure"] = int(row.get("failure", 0)) + 1
            data[name] = row
        self._save(data)

    # ---------------------------------------------------------------- read
    def stat(self, source: str) -> SourceStat:
        row = self._load().get(source) or {}
        return SourceStat(
            source=source,
            success=int(row.get("success", 0)),
            failure=int(row.get("failure", 0)),
            last_success_at=str(row.get("last_success_at", "")),
        )

    def quality_score(
        self,
        source: str,
        *,
        now: datetime | None = None,
        half_life_days: float = 30.0,
    ) -> float:
        """``success_rate × freshness`` in ``[0, 1]``.

        Freshness halves every ``half_life_days`` since the last success, so
        a once-reliable source that has gone quiet decays toward 0.  ``now``
        is injectable for deterministic tests.  Unseen sources score 0.0.
        """

        s = self.stat(source)
        if s.attempts() == 0:
            return 0.0
        rate = s.success_rate()
        if not s.last_success_at:
            return rate * 0.5  # has attempts but never succeeded recently
        try:
            last = datetime.fromisoformat(s.last_success_at)
        except ValueError:
            return rate * 0.5
        now_dt = now or _utcnow()
        age_days = max(0.0, (now_dt - last).total_seconds() / 86_400.0)
        freshness = 0.5 ** (age_days / max(half_life_days, 1e-9))
        return rate * freshness

    def ranking(
        self, sources: Iterable[str], *, now: datetime | None = None
    ) -> list[tuple[str, float]]:
        """Sources sorted by measured quality, best first (ties: name)."""

        scored = [(s, self.quality_score(s, now=now)) for s in sources]
        scored.sort(key=lambda t: (-t[1], t[0]))
        return scored


__all__ = ["SourceStat", "SourceQualityStore"]
