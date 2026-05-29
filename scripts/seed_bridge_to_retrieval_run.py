#!/usr/bin/env python3
"""Bridge seed_orchestrator output → .omni/retrieval/<run_id>/ format.

seed_orchestrator drops individual evidence JSON files under
``vault/evidence/<domain>/<run_id>__<idx>__<digest>.json``, but
``omni-hub wiki-ingest`` expects the cascade ``--persist-evidence``
shape: a directory ``.omni/retrieval/<run_id>/`` containing
``run_manifest.json`` + ``evidence.jsonl`` + ``sources.json``.

This script walks all seed-* prefixes and produces the bridge.
Idempotent: re-running just rewrites manifests with current data.

Usage::

    python3 scripts/seed_bridge_to_retrieval_run.py
    # then for each printed run_id:
    omni-hub wiki-ingest --run-id <run_id>
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


SEED_PREFIXES = ("seed-orchestrator-", "seed-arxiv-", "seed-wiki-")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    evidence_root = repo_root / "vault" / "evidence"
    retrieval_root = repo_root / ".omni" / "retrieval"
    retrieval_root.mkdir(parents=True, exist_ok=True)

    if not evidence_root.exists():
        sys.stderr.write("vault/evidence missing — nothing to bridge\n")
        return 1

    # Group all evidence files by (run_id, domain).
    grouped: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for ev_file in evidence_root.rglob("*.json"):
        name = ev_file.name
        # Filename pattern: <run_id>__<idx>__<digest>.json
        m = re.match(r"^(seed-[a-zA-Z0-9_-]+?)__\d+__[0-9a-f]+\.json$", name)
        if not m:
            continue
        run_id = m.group(1)
        domain = ev_file.parent.name
        grouped[(run_id, domain)].append(ev_file)

    if not grouped:
        sys.stderr.write("no seed-* evidence files found\n")
        return 1

    written_runs: list[str] = []
    for (run_id, domain), files in sorted(grouped.items()):
        files = sorted(files)
        # P0.4: namespace the bridge dir by (run_id, domain) so an all-domain
        # seed that shares a single run_id can't overwrite its own evidence.
        bridge_run_id = f"{run_id}__{domain}"
        bridge_dir = retrieval_root / bridge_run_id
        bridge_dir.mkdir(parents=True, exist_ok=True)

        # Compose evidence.jsonl from all individual files.
        sources_count: dict[str, int] = defaultdict(int)
        with (bridge_dir / "evidence.jsonl").open("w", encoding="utf-8") as f:
            for ev_path in files:
                try:
                    ev = json.loads(ev_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                # Normalise to retrieve --persist-evidence flat record shape.
                # seed_orchestrator stores wrapper {run_id, record_idx, ...,
                # source, title, url, snippet, metadata}; we strip wrapper.
                flat = {
                    "source": ev.get("source", ""),
                    "title": ev.get("title", ""),
                    "url": ev.get("url", ""),
                    "snippet": ev.get("snippet", ""),
                    "score": ev.get("score") or 0.0,
                    "fetched_at": ev.get("fetched_at", _utcnow()),
                    "domain": domain,
                    "canonical_id": ev.get("canonical_id", ""),
                    "metadata": ev.get("metadata", {}),
                }
                sources_count[flat["source"]] += 1
                f.write(json.dumps(flat, ensure_ascii=False) + "\n")

        # run_manifest.json
        manifest = {
            "run_id": bridge_run_id,
            "query": f"seed:{run_id}",
            "domain": domain,
            "fusion": "seed",
            "record_count": sum(sources_count.values()),
            "sources_tried": sorted(sources_count.keys()),
            "sources_succeeded": sorted(sources_count.keys()),
            "sources_failed": [],
            "fetched_at": _utcnow(),
            "origin": "seed_orchestrator",
        }
        (bridge_dir / "run_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # sources.json — per-source counts for downstream visibility
        (bridge_dir / "sources.json").write_text(
            json.dumps(dict(sources_count), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        sys.stderr.write(
            f"  ✓ {bridge_run_id}: {manifest['record_count']} records "
            f"across {len(sources_count)} sources\n"
        )
        written_runs.append(bridge_run_id)

    sys.stderr.write(f"\n✅ wrote {len(written_runs)} bridge directories\n")
    sys.stderr.write("Next step:\n")
    for r in written_runs:
        sys.stderr.write(f"  omni-hub wiki-ingest --run-id {r}\n")
    # Also print run_ids on stdout for scripting
    for r in written_runs:
        print(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
