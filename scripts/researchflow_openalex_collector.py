#!/usr/bin/env python3
"""Collect papers from OpenAlex → ResearchFlow's paper_list.csv.

OpenAlex is the freshest open academic metadata source (~250M works,
Crossref-synced daily, no API key, no recycle risk).  ResearchFlow ships
with only an arXiv collector — this script extends it with OpenAlex so
the ``paper_list.csv`` pipeline covers cross-discipline, post-arXiv, and
properly-published works.

Output rows are appended to ``paper_list.csv`` in the standard 12-column
schema (title, analysis_path, pdf_ref, venue, year, topics, methods,
datasets, tags, paper_link, project_link, source_note).  Existing rows
are deduplicated by title (case-insensitive) unless ``--no-dedup``.

Usage::

    python3 scripts/researchflow_openalex_collector.py \\
        --query "diffusion transformer video generation" \\
        --year 2026 --limit 30

    # Dry run (just print what would be appended):
    python3 scripts/researchflow_openalex_collector.py --query X --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path


OPENALEX_API = "https://api.openalex.org/works"
DEFAULT_MAILTO = "researchflow@example.com"
DEFAULT_OUTPUT = "agent-harness/researchflow/obsidian-vault/paper_list.csv"

CSV_COLUMNS = [
    "title", "analysis_path", "pdf_ref", "venue", "year",
    "topics", "methods", "datasets", "tags",
    "paper_link", "project_link", "source_note",
]


def _query_openalex(
    *,
    query: str,
    year: int | None,
    venue: str | None,
    limit: int,
    mailto: str,
) -> list[dict]:
    params: dict[str, str] = {
        "search": query,
        "per-page": str(min(limit, 200)),
        "mailto": mailto,
        "sort": "publication_date:desc",
    }
    filters: list[str] = []
    if year:
        filters.append(f"publication_year:{year}")
    if venue:
        filters.append(f"primary_location.source.display_name.search:{venue}")
    if filters:
        params["filter"] = ",".join(filters)

    url = f"{OPENALEX_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"researchflow-collector/1.0 (mailto:{mailto})"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    data = json.loads(body)
    return data.get("results", []) or []


def _work_to_row(work: dict) -> dict:
    title = (work.get("title") or "").strip()
    year = work.get("publication_year")

    primary = work.get("primary_location") or {}
    source_obj = primary.get("source") or {}
    venue = source_obj.get("display_name") or ""
    if not venue:
        venue = "preprint" if (work.get("type") == "article") else (work.get("type") or "")

    topics = work.get("topics") or []
    topic_names = [t.get("display_name", "").strip() for t in topics if t.get("display_name")]
    topics_str = "; ".join(topic_names)
    tags_str = "; ".join(
        "topic/" + n.lower().replace(" ", "_").replace("/", "_")
        for n in topic_names
    )

    doi = (work.get("doi") or "").strip()
    landing = primary.get("landing_page_url") or ""
    paper_link = doi or landing or (work.get("id") or "")

    return {
        "title": title,
        "analysis_path": "",
        "pdf_ref": "",
        "venue": venue,
        "year": str(year) if year else "",
        "topics": topics_str,
        "methods": "",
        "datasets": "",
        "tags": tags_str,
        "paper_link": paper_link,
        "project_link": "",
        "source_note": f"OpenAlex {work.get('id', '')}",
    }


def _existing_titles(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()
    titles: set[str] = set()
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = (row.get("title") or "").strip().lower()
            if t:
                titles.add(t)
    return titles


def _append_rows(csv_path: Path, rows: list[dict]) -> int:
    is_new = not csv_path.exists()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if is_new:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--query", required=True,
                        help="OpenAlex full-text search query")
    parser.add_argument("--year", type=int,
                        help="Filter to a specific publication year")
    parser.add_argument("--venue",
                        help="Filter by venue display name (substring match)")
    parser.add_argument("--limit", type=int, default=25,
                        help="Max results per request (<=200, default 25)")
    parser.add_argument(
        "--mailto",
        default=os.environ.get("OPENALEX_MAILTO", DEFAULT_MAILTO),
        help=("Email for OpenAlex polite pool (10x faster). "
              "Default reads env OPENALEX_MAILTO."),
    )
    parser.add_argument(
        "--output-csv", default=DEFAULT_OUTPUT,
        help=f"Path to paper_list.csv (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument("--no-dedup", action="store_true",
                        help="Skip title-based deduplication")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print rows that would be appended, do not write")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    csv_path = (repo_root / args.output_csv).resolve()

    works = _query_openalex(
        query=args.query,
        year=args.year,
        venue=args.venue,
        limit=args.limit,
        mailto=args.mailto,
    )
    sys.stderr.write(f"OpenAlex returned {len(works)} works\n")

    rows = [_work_to_row(w) for w in works if (w.get("title") or "").strip()]

    if not args.no_dedup:
        existing = _existing_titles(csv_path)
        before = len(rows)
        rows = [r for r in rows if r["title"].lower() not in existing]
        sys.stderr.write(
            f"deduped: {before - len(rows)} already in CSV, "
            f"{len(rows)} new\n"
        )

    if args.dry_run:
        preview = min(5, len(rows))
        for r in rows[:preview]:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        if len(rows) > preview:
            sys.stderr.write(f"... and {len(rows) - preview} more (not shown)\n")
        return 0

    if not rows:
        sys.stderr.write("no new rows to append\n")
        return 0

    n = _append_rows(csv_path, rows)
    sys.stderr.write(f"appended {n} rows to {csv_path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
