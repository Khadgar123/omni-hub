#!/usr/bin/env python3
"""entity-timeline-build entrypoint — chronological timeline for an entity.

Aggregates an entity's signals (via follow_entity machinery) then sorts
them into a single time-ordered timeline, optionally synthesizing a
narrative summary at the top.

Usage::

    python3 scripts/entity_timeline_build.py karpathy
    python3 scripts/entity_timeline_build.py anthropic --sources rss,hn,tavily,gdelt
    python3 scripts/entity_timeline_build.py musk --synthesize --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# Recognised date fields in record metadata, in priority order.
_DATE_KEYS = ("published", "date", "created_at", "seendate", "date_start",
              "indexed_at", "update_date", "file_date", "published_date")


def _extract_date(record: dict) -> str:
    """Best-effort ISO-ish date string from a record's metadata."""

    meta = record.get("metadata", {}) or {}
    for k in _DATE_KEYS:
        v = meta.get(k)
        if v:
            s = str(v).strip()
            # Normalise GDELT 20260101T120000Z, ISO, "Mon, 12 Feb 2026", etc.
            m = re.search(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", s)
            if m:
                return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            # RFC822-ish "12 Feb 2026"
            m2 = re.search(r"(\d{1,2})\s+(\w{3,})\s+(\d{4})", s)
            if m2:
                return f"{m2.group(3)}-{m2.group(2)[:3]}-{m2.group(1):0>2}"
            return s[:16]
    return ""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("entity", help="entity_id from config/entity-watchlist.yaml")
    p.add_argument("--sources", default="rss,hn,gdelt,tavily,openalex,edgar,truth")
    p.add_argument("--limit", type=int, default=5, help="per-source cap")
    p.add_argument("--synthesize", action="store_true",
                   help="add an LLM narrative summary at the top")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "src"))
    sys.path.insert(0, str(repo_root / "scripts"))

    import follow_entity                                          # type: ignore

    entity = follow_entity._load_watchlist(
        repo_root / "config" / "entity-watchlist.yaml"
    ).get(args.entity)
    if not entity:
        sys.stderr.write(f"unknown entity: {args.entity}\n")
        return 2

    selected = [s.strip() for s in args.sources.split(",") if s.strip()]
    sys.stderr.write(f"# timeline for {entity['display']} ({entity.get('_bucket')})\n")
    records = follow_entity._gather_one(entity, selected, args.limit)

    # Sort by extracted date desc; undated records sink to the bottom.
    for r in records:
        r["_date"] = _extract_date(r)
    dated = [r for r in records if r["_date"]]
    undated = [r for r in records if not r["_date"]]
    dated.sort(key=lambda r: r["_date"], reverse=True)
    ordered = dated + undated

    narrative = ""
    if args.synthesize and ordered:
        from omni_hub.retrieval.base import RetrievalRecord
        from omni_hub.retrieval.synthesize import synthesize_answer
        rrecs = [
            RetrievalRecord(
                source=r.get("_via", r.get("source", "?")),
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("snippet", ""),
                metadata=r.get("metadata", {}),
            )
            for r in ordered[:10]
        ]
        syn = synthesize_answer(
            f"What has {entity['display']} been doing recently? Summarize the timeline.",
            rrecs, domain=entity.get("primary_domain", "default"), max_records=10,
        )
        narrative = syn.answer

    if args.json:
        print(json.dumps({
            "entity": args.entity, "display": entity["display"],
            "narrative": narrative,
            "timeline": [
                {"date": r["_date"], "via": r.get("_via", "?"),
                 "title": r.get("title", ""), "url": r.get("url", "")}
                for r in ordered
            ],
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"# Timeline: {entity['display']}\n")
    if narrative:
        print("## Summary\n")
        print(narrative)
        print()
    print(f"## Events ({len(ordered)})\n")
    for r in ordered:
        date = r["_date"] or "(undated)"
        via = r.get("_via", "?")
        title = (r.get("title") or "").strip()[:90]
        url = r.get("url", "")
        print(f"- **{date}** `{via}` [{title}]({url})" if url
              else f"- **{date}** `{via}` {title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
