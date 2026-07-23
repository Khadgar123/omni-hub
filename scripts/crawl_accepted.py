#!/usr/bin/env python3
"""Conference accepted-paper crawler — ports resmax's multi-source accepted-
list logic into omni-hub.

For each venue+year in ``config/venues.yaml`` it fetches the OFFICIAL accepted
list (OpenReview ``venue_submissions`` / OpenAlex ``venue_works``), then dedups
across sources AND against the prior index with
``paper_identity.merge_papers`` — so a paper already present as an arXiv
preprint is UPDATED (venue/accepted backfilled) rather than duplicated (the
exact preprint↔accepted duplication the operator flagged).

Writes ``.omni/accepted/accepted_index.jsonl`` (the resmax ``accepted_index``
analog) and prints a per-run summary (raw vs deduped counts).

Usage::

    python3 scripts/crawl_accepted.py --venues iclr,neurips --limit 50
    python3 scripts/crawl_accepted.py --all --limit 200 --json
    python3 scripts/crawl_accepted.py --list           # show configured venues
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _need_yaml():  # noqa: ANN202
    try:
        import yaml
        return yaml
    except ImportError:
        sys.stderr.write("pip install pyyaml\n")
        sys.exit(2)


def load_venues(path: Path) -> dict:
    yaml = _need_yaml()
    with path.open(encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("venues", {}) or {}


def expand_entries(venues: dict, selected: list[str] | None) -> list[tuple[str, dict, int]]:
    """Flatten {venue_id: {years:[...]}} into (venue_id, entry, year) triples."""
    out: list[tuple[str, dict, int]] = []
    for vid, entry in venues.items():
        if selected and vid not in selected:
            continue
        for year in entry.get("years", []) or []:
            out.append((vid, entry, int(year)))
    return out


def crawl(
    entries: list[tuple[str, dict, int]],
    *,
    limit: int = 200,
    openreview_fetch=None,
    openalex_fetch=None,
):
    """Gather accepted lists for every (venue, year) and dedup via
    paper_identity.merge_papers.  Fetchers are injectable for testing.

    Returns ``(raw_records, merged_records)``.
    """
    from omni_hub.retrieval.paper_identity import merge_papers

    if openreview_fetch is None:
        from omni_hub.retrieval.openreview import OpenReviewSource
        _or = OpenReviewSource()
        openreview_fetch = lambda venueid: _or.venue_submissions(venueid, limit=limit)  # noqa: E731
    if openalex_fetch is None:
        from omni_hub.retrieval.openalex import OpenAlexSource
        _oa = OpenAlexSource()
        openalex_fetch = lambda **kw: _oa.venue_works(limit=limit, **kw)  # noqa: E731

    raw = []
    for vid, entry, year in entries:
        method = entry.get("method")
        try:
            if method == "openreview":
                venueid = str(entry.get("venueid_template", "")).format(year=year)
                got = openreview_fetch(venueid)
            elif method == "openalex":
                got = openalex_fetch(
                    source_id=str(entry.get("openalex_source_id", "")),
                    venue_name=str(entry.get("venue_name", "")),
                    year=year,
                )
            else:
                got = []
        except Exception as exc:  # noqa: BLE001 - one venue must not abort the run
            sys.stderr.write(f"  ⚠ {vid} {year}: {type(exc).__name__}: {str(exc)[:120]}\n")
            got = []
        sys.stderr.write(f"  → {vid} {year} ({method}): {len(got)} records\n")
        for rec in got:
            d = rec.to_dict() if hasattr(rec, "to_dict") else rec
            d.setdefault("metadata", {})["crawl_venue"] = vid
            d["metadata"]["crawl_year"] = year
            raw.append(rec)
    merged = merge_papers(raw)
    return raw, merged


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--venues", default="", help="comma-separated venue ids (default: all)")
    p.add_argument("--all", action="store_true", help="crawl all configured venues")
    p.add_argument("--limit", type=int, default=100, help="max records per venue+year")
    p.add_argument("--config", default="config/venues.yaml")
    p.add_argument("--list", action="store_true", help="list configured venues and exit")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    root = _repo_root()
    sys.path.insert(0, str(root / "src"))
    venues = load_venues(root / args.config)

    if args.list:
        for vid, e in sorted(venues.items()):
            print(f"  {vid:10s} {e.get('display',''):8s} {e.get('method',''):10s} "
                  f"years={e.get('years', [])}")
        return 0

    selected = [s.strip() for s in args.venues.split(",") if s.strip()] or None
    if not selected and not args.all:
        sys.stderr.write("specify --venues <ids> or --all\n")
        return 2
    entries = expand_entries(venues, selected)
    sys.stderr.write(f"# crawling {len(entries)} venue-editions\n")

    raw, merged = crawl(entries, limit=args.limit)

    out_dir = root / ".omni" / "accepted"
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "accepted_index.jsonl"
    with index_path.open("w", encoding="utf-8") as f:
        for rec in merged:
            f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")

    summary = {
        "venue_editions": len(entries),
        "raw_records": len(raw),
        "deduped_records": len(merged),
        "folded": len(raw) - len(merged),
        "index": str(index_path.relative_to(root)),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"\n# accepted index: {summary['deduped_records']} papers "
              f"({summary['folded']} duplicates folded) → {summary['index']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
