"""Opik trace bridge with JSONL fallback.

Until the Opik fork is pinned and a local Opik server is running, the harness
writes traces to ``.omni/traces/traces.jsonl`` with the same shape Opik
ingests (timestamp / kind / record_id / payload).  When you later spin Opik
up, swap ``OpikBackend`` for ``LocalJsonlBackend`` — the call sites stay the
same.

Trace kinds:

- ``generation``  — full GenerationRecord
- ``judge``       — JudgeEnsembleResult
- ``preference``  — PreferenceRecord
- ``compile``     — CompileReport
- ``redundancy``  — RedundancyScanReport (summary only)
- ``report``      — daily/weekly/monthly report metadata
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _opik_available() -> bool:
    try:
        import opik  # type: ignore[import-not-found]  # noqa: F401
        return True
    except Exception:
        return False


class TraceBackend:
    name: str = "abstract"

    def log(self, kind: str, payload: dict[str, Any], *, record_id: str = "") -> None:  # pragma: no cover
        raise NotImplementedError

    def read(self, *, kind: str | None = None) -> Iterator[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError


class LocalJsonlBackend(TraceBackend):
    name = "jsonl-local"

    def __init__(self, path: Path | str = ".omni/traces/traces.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, kind: str, payload: dict[str, Any], *, record_id: str = "") -> None:
        envelope = {
            "ts": _utcnow(),
            "kind": kind,
            "record_id": record_id,
            "payload": payload,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(envelope, ensure_ascii=False))
            fh.write("\n")

    def read(self, *, kind: str | None = None) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if kind is None or obj.get("kind") == kind:
                    yield obj


class OpikBackend(TraceBackend):  # pragma: no cover — exercised once installed
    name = "opik"

    def __init__(self) -> None:
        try:
            import opik  # type: ignore[import-not-found]
        except Exception as exc:
            raise RuntimeError("opik not installed") from exc
        self._opik = opik

    def log(self, kind: str, payload: dict[str, Any], *, record_id: str = "") -> None:
        # Real wire-up lands once opik fork is pinned.
        # For now write through to the local jsonl too so we never lose data.
        LocalJsonlBackend().log(kind, payload, record_id=record_id)

    def read(self, *, kind: str | None = None):
        return LocalJsonlBackend().read(kind=kind)


def get_backend(prefer: str = "auto") -> TraceBackend:
    if prefer == "opik":
        return OpikBackend()
    if prefer == "auto" and _opik_available():
        return OpikBackend()
    return LocalJsonlBackend()


# ---------------------------------------------------------------------------
# Convenience loggers
# ---------------------------------------------------------------------------


def log_generation(record_dict: dict[str, Any], *, prefer_backend: str = "auto") -> None:
    get_backend(prefer_backend).log(
        "generation", record_dict, record_id=str(record_dict.get("record_id", "")),
    )


def log_judge(judge_result_dict: dict[str, Any], *, prefer_backend: str = "auto") -> None:
    get_backend(prefer_backend).log(
        "judge", judge_result_dict,
        record_id=str(judge_result_dict.get("record", {}).get("record_id", "")),
    )


def log_preference(pref_dict: dict[str, Any], *, prefer_backend: str = "auto") -> None:
    get_backend(prefer_backend).log(
        "preference", pref_dict, record_id=str(pref_dict.get("record_id", "")),
    )


def log_compile(compile_report_dict: dict[str, Any], *, prefer_backend: str = "auto") -> None:
    get_backend(prefer_backend).log(
        "compile", compile_report_dict,
        record_id=f"{compile_report_dict.get('domain', '')}:{compile_report_dict.get('to_version', '')}",
    )
