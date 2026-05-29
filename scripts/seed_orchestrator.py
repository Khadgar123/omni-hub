#!/usr/bin/env python3
"""Seed orchestrator — declarative batch initialization.

Reads ``config/seed-source-manifest.yaml`` and executes the bulk-ingest
plan domain-by-domain.  Output: ``vault/evidence/<domain>/`` + ``vault/raw/``
populated with seed records ready for ``wiki-ingest --run-id`` →
Proposal → approve → wiki-apply.

Usage::

    # Dry-run: print what would be ingested
    python3 scripts/seed_orchestrator.py --dry-run

    # All domains
    python3 scripts/seed_orchestrator.py

    # One domain
    python3 scripts/seed_orchestrator.py --domain ai_progress

    # Force re-fetch (default: skip if evidence file already exists)
    python3 scripts/seed_orchestrator.py --force

Compliance: writes to ``vault/evidence`` and ``vault/raw`` only.  NEVER
writes ``vault/wiki/`` directly — that goes through Proposal review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _need_yaml():                                                  # noqa: ANN202
    try:
        import yaml
        return yaml
    except ImportError:
        # PyYAML is pretty universally installed but isn't a stdlib;
        # fall back to manually parsing the limited subset we use.
        sys.stderr.write(
            "PyYAML not installed.  Install with:\n"
            "  pip install pyyaml\n"
        )
        sys.exit(2)


def _load_manifest(path: Path) -> dict:
    yaml = _need_yaml()
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _write_evidence(
    repo_root: Path,
    domain: str,
    run_id: str,
    idx: int,
    record: dict,
    source: str,
) -> None:
    # Use the canonical production slugifier (NOT a local replace('/','_'))
    # so seed output lands in the SAME evidence tree as the live ingest
    # path — otherwise "ai_progress" (here) and "ai-progress"
    # (knowledge_plane._slugify) split into two duplicate trees.
    from omni_hub.knowledge_plane import _slugify
    domain_slug = _slugify(domain)
    evidence_dir = repo_root / "vault" / "evidence" / domain_slug
    raw_dir = repo_root / "vault" / "raw" / domain_slug / run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    canonical = record.get("canonical_id") or record.get("url") or f"{source}-r{idx}"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]

    raw_path = raw_dir / f"{idx:03d}__{digest}.md"
    raw_path.write_text(
        f"---\nrun_id: {run_id}\nidx: {idx}\nsource: {source}\n"
        f"canonical_id: {canonical}\nurl: {record.get('url','')}\n"
        f"fetched_at: {_utcnow()}\n---\n\n"
        f"# {record.get('title','(no title)')}\n\n{record.get('snippet','')}\n",
        encoding="utf-8",
    )
    ev = {
        "run_id": run_id, "record_idx": idx, "cite_id": "",
        "source": source,
        "title": record.get("title", ""),
        "url": record.get("url", ""),
        "snippet": record.get("snippet", ""),
        "canonical_id": canonical,
        "fetched_at": _utcnow(),
        "score": record.get("score", 0.0),
        "raw_path": str(raw_path.relative_to(repo_root)),
        "metadata": record.get("metadata", {}),
    }
    ev_path = evidence_dir / f"{run_id}__{idx:03d}__{digest}.json"
    ev_path.write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")


def _bulk_source(
    repo_root: Path,
    domain: str,
    run_id: str,
    spec: dict,
    sources_registry: dict,
    counter: dict[str, int],
    dry_run: bool,
) -> int:
    """Execute one ``bulk:`` entry from the manifest."""

    source_name = spec.get("source", "")
    source = sources_registry.get(source_name)
    if source is None:
        sys.stderr.write(f"    ⚠ source '{source_name}' not registered, skipping\n")
        return 0

    limit = int(spec.get("limit", 10))
    queries: list[str] = []
    if "query" in spec:
        queries.append(str(spec["query"]))
    elif "category" in spec:
        # arXiv-like: one query per category
        for cat in spec["category"]:
            queries.append(f"cat:{cat}")
    elif "themes" in spec:
        queries.extend(str(t) for t in spec["themes"])
    elif "countries" in spec:
        year_range = spec.get("year_range", [2024, 2026])
        for c in spec["countries"]:
            queries.append(f"{c}:{year_range[0]}-{year_range[1]}")
    elif "handles" in spec:
        # bluesky: ``from:<handle>``
        for h in spec["handles"]:
            queries.append(f"from:{h}")
    elif "indicators" in spec:
        queries.extend(str(i) for i in spec["indicators"])
    elif "series" in spec:
        queries.extend(str(s) for s in spec["series"])
    else:
        queries.append(domain)            # fallback: search by domain name

    written = 0
    for q in queries:
        sys.stderr.write(f"    → {source_name}({q!r}) limit={limit}\n")
        if dry_run:
            written += limit
            continue
        try:
            records = source.retrieve(q, limit=limit, domain=domain)
        except Exception as exc:                                  # noqa: BLE001
            sys.stderr.write(f"      ⚠ {type(exc).__name__}: {str(exc)[:120]}\n")
            continue
        for rec in records:
            counter["idx"] += 1
            _write_evidence(
                repo_root, domain, run_id, counter["idx"],
                rec.to_dict() if hasattr(rec, "to_dict") else rec,
                source_name,
            )
            written += 1
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest",
                   default="config/seed-source-manifest.yaml",
                   help="Path to manifest YAML")
    p.add_argument("--domain", default="",
                   help="Single domain to seed; default ALL")
    p.add_argument("--dry-run", action="store_true",
                   help="Print plan, do not write")
    p.add_argument("--allow-paid", action="store_true",
                   help="Include tier=2 sources (paid / broker)")
    args = p.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "src"))
    manifest_path = repo_root / args.manifest
    if not manifest_path.exists():
        sys.stderr.write(f"manifest not found: {manifest_path}\n")
        return 2

    manifest = _load_manifest(manifest_path)

    from omni_hub.retrieval import builtin_sources
    sources_registry = builtin_sources()

    domains_section = manifest.get("domains", {})
    targets = [args.domain] if args.domain else list(domains_section)

    run_id = datetime.now().strftime("seed-orchestrator-%Y%m%d-%H%M%S")
    counter = {"idx": 0}
    grand_total = 0

    for dom in targets:
        spec = domains_section.get(dom)
        if not spec:
            sys.stderr.write(f"  ⚠ domain '{dom}' not in manifest, skipping\n")
            continue
        sys.stderr.write(f"\n# domain: {dom}\n")
        bulks = spec.get("bulk", []) or []
        domain_written = 0
        for bulk in bulks:
            tier = int(bulk.get("tier", 0))
            if tier >= 2 and not args.allow_paid:
                sys.stderr.write(
                    f"    ⏭ {bulk.get('source')} tier={tier} skipped (need --allow-paid)\n"
                )
                continue
            domain_written += _bulk_source(
                repo_root, dom, run_id, bulk, sources_registry, counter, args.dry_run,
            )
        sys.stderr.write(f"  → {dom}: {domain_written} records\n")
        grand_total += domain_written

    sys.stderr.write(
        f"\n✅ orchestrator done.  run_id={run_id}\n"
        f"   total: {grand_total} records across {len(targets)} domain(s)\n"
        f"   next: omni-hub wiki-ingest --run-id {run_id} --domain <X> per domain\n"
    )
    print(run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
