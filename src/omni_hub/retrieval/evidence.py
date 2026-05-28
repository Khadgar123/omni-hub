"""Per-cascade-run evidence persistence — PaperQA2 / deep-research skill pattern.

Each `omni-hub retrieve` invocation can persist its full ``CascadeResult``
to ``.omni/retrieval/<run_id>/`` so:

* the harness ensemble can re-read provenance after context compaction,
* `propose-approve` / `propose-reject` can replay what was shown to the model,
* event_log remains the agent-step log; this is the *evidence* log.

Layout:

    .omni/retrieval/<run_id>/
        run_manifest.json   { query, domain, fusion, timestamps, source diagnostics }
        sources.json        deduped URLs + cite_ids
        evidence.jsonl      one RetrievalRecord per line

``run_id`` defaults to ``<timestamp>-<8-char hex>``, sortable.  Caller
can pin a custom run_id (e.g. ``task-42-retrieve-1``) to thread evidence
to a queue task.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .._storage import safe_workspace_path


EVIDENCE_DIR_REL = ".omni/retrieval"


def _new_run_id() -> str:
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"{ts}-{secrets.token_hex(4)}"


@dataclass(slots=True)
class EvidenceArtifact:
    run_id: str
    run_manifest_path: Path
    sources_path: Path
    evidence_path: Path
    record_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_manifest_path": str(self.run_manifest_path),
            "sources_path": str(self.sources_path),
            "evidence_path": str(self.evidence_path),
            "record_count": self.record_count,
        }


class EvidenceStore:
    """Write/read cascade evidence under ``.omni/retrieval/<run_id>/``."""

    def __init__(
        self,
        workspace: Path | str = ".",
        base_dir: str = EVIDENCE_DIR_REL,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.base_dir = safe_workspace_path(self.workspace, base_dir)

    def write(
        self,
        cascade_result_dict: dict[str, Any],
        *,
        run_id: str | None = None,
        extra_manifest: dict[str, Any] | None = None,
    ) -> EvidenceArtifact:
        """Persist a ``CascadeResult.to_dict()`` into a fresh run directory."""

        run_id = run_id or _new_run_id()
        run_dir = self.base_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        records = cascade_result_dict.get("records", [])

        evidence_path = run_dir / "evidence.jsonl"
        with evidence_path.open("w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

        sources_path = run_dir / "sources.json"
        seen_urls: list[str] = []
        for rec in records:
            url = rec.get("url") or ""
            if url and url not in seen_urls:
                seen_urls.append(url)
        sources_payload = {
            "count": len(seen_urls),
            "urls": seen_urls,
            "by_cite_id": {
                rec.get("cite_id", ""): {
                    "url": rec.get("url", ""),
                    "title": rec.get("title", ""),
                    "source": rec.get("source", ""),
                }
                for rec in records
                if rec.get("cite_id")
            },
        }
        sources_path.write_text(
            json.dumps(sources_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        manifest_path = run_dir / "run_manifest.json"
        manifest = {
            "run_id": run_id,
            "query": cascade_result_dict.get("query", ""),
            "domain": cascade_result_dict.get("domain", ""),
            "fusion": cascade_result_dict.get("fusion", ""),
            "record_count": len(records),
            "sources_tried": cascade_result_dict.get("sources_tried", []),
            "sources_succeeded": cascade_result_dict.get("sources_succeeded", []),
            "graded_dropped": cascade_result_dict.get("graded_dropped", 0),
            "errors": cascade_result_dict.get("errors", []),
            "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if extra_manifest:
            manifest.update(extra_manifest)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return EvidenceArtifact(
            run_id=run_id,
            run_manifest_path=manifest_path,
            sources_path=sources_path,
            evidence_path=evidence_path,
            record_count=len(records),
        )

    def read_manifest(self, run_id: str) -> dict[str, Any]:
        path = self.base_dir / run_id / "run_manifest.json"
        if not path.exists():
            raise FileNotFoundError(f"no evidence run at {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_runs(self, *, limit: int = 50) -> list[str]:
        if not self.base_dir.exists():
            return []
        runs = [p.name for p in self.base_dir.iterdir() if p.is_dir()]
        runs.sort(reverse=True)
        return runs[:limit]
