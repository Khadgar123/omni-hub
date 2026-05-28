"""Replay / stats: read historical traces, produce harness-wide stats.

Two public functions:

- ``stats(backend)`` — counts traces by kind, plus per-domain preference
  counts and per-domain compile history.
- ``replay(backend, *, kind=...)`` — yield raw trace envelopes filtered by
  kind, in chronological order.

This is the closing piece of the flywheel: once you've run the harness for a
while, ``stats()`` tells you whether the data flywheel is actually spinning.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterator

from . import opik_bridge


@dataclass(slots=True)
class HarnessStats:
    total_traces: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    preference_by_domain: dict[str, dict[str, int]] = field(default_factory=dict)
    compiles_by_domain: dict[str, list[str]] = field(default_factory=dict)
    judge_wins_by_model: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total_traces": self.total_traces,
            "by_kind": dict(sorted(self.by_kind.items())),
            "preference_by_domain": {
                d: dict(sorted(v.items())) for d, v in sorted(self.preference_by_domain.items())
            },
            "compiles_by_domain": {
                d: list(v) for d, v in sorted(self.compiles_by_domain.items())
            },
            "judge_wins_by_model": dict(sorted(
                self.judge_wins_by_model.items(),
                key=lambda kv: kv[1], reverse=True,
            )),
        }


def stats(*, prefer_backend: str = "auto") -> HarnessStats:
    backend = opik_bridge.get_backend(prefer_backend)
    out = HarnessStats()
    pref: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    compiles: dict[str, list[str]] = defaultdict(list)
    judge_wins: dict[str, int] = defaultdict(int)

    for envelope in backend.read():
        out.total_traces += 1
        kind = envelope.get("kind", "")
        out.by_kind[kind] = out.by_kind.get(kind, 0) + 1
        payload = envelope.get("payload") or {}

        if kind == "preference":
            domain = payload.get("domain", "?")
            decision = payload.get("decision", "?")
            pref[domain][decision] += 1
        elif kind == "compile":
            domain = payload.get("domain", "?")
            version = payload.get("to_version", "?")
            compiles[domain].append(version)
        elif kind == "judge":
            winner_id = payload.get("winner_candidate_id")
            if not winner_id:
                continue
            for cand in (payload.get("record") or {}).get("candidates") or []:
                if cand.get("candidate_id") == winner_id:
                    judge_wins[cand.get("model", "?")] += 1
                    break

    out.preference_by_domain = {d: dict(v) for d, v in pref.items()}
    out.compiles_by_domain = dict(compiles)
    out.judge_wins_by_model = dict(judge_wins)
    return out


def replay(*, kind: str | None = None, prefer_backend: str = "auto") -> Iterator[dict[str, Any]]:
    backend = opik_bridge.get_backend(prefer_backend)
    yield from backend.read(kind=kind)
