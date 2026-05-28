#!/usr/bin/env python3
"""Seed pack: stream arXiv abstracts → vault/evidence via the official API.

Uses the existing ``ArxivSource`` connector to pull recent abstracts by
category — no extra dependencies, respects arXiv's 1-req/3s rate limit.

Compared to ``seed_arxiv_hf.py`` (HF dataset):
- ✅ no HF account / no dataset namespace flakiness
- ✅ truly recent (last 7-30 days from arXiv's live listing)
- ⚠️  smaller batch (arXiv API caps ~200/request; pagination needed for >200)

Usage::

    python3 scripts/seed_arxiv.py --category cs.AI --limit 50 --domain ai_progress
    python3 scripts/seed_arxiv.py --category cs.CL,cs.LG --limit 30 --domain research
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


QUERY_URL = "https://export.arxiv.org/api/query"
ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}
RATE_LIMIT_SEC = 3.5                                              # 1 req / 3s + 0.5 buffer


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_category(category: str, max_results: int, timeout: int = 30) -> list[dict]:
    """One arXiv API call: list-by-category, sorted by submitted date desc."""

    params = {
        "search_query": f"cat:{category}",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": str(min(max_results, 200)),
    }
    url = f"{QUERY_URL}?{urllib.parse.urlencode(params)}"
    sys.stderr.write(f"  → arXiv API ({category})\n")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "omni-hub/0.42 seed-arxiv"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        xml_bytes = resp.read()
    root = ET.fromstring(xml_bytes)
    entries = root.findall("atom:entry", ATOM_NS)
    out: list[dict] = []
    for e in entries:
        arxiv_id_url = (e.findtext("atom:id", default="", namespaces=ATOM_NS) or "").strip()
        # arxiv_id_url is http://arxiv.org/abs/2401.12345v1
        arxiv_id = arxiv_id_url.rsplit("/", 1)[-1] if arxiv_id_url else ""
        title = (e.findtext("atom:title", default="", namespaces=ATOM_NS) or "").strip().replace("\n", " ")
        summary = (e.findtext("atom:summary", default="", namespaces=ATOM_NS) or "").strip()
        published = (e.findtext("atom:published", default="", namespaces=ATOM_NS) or "").strip()
        authors_els = e.findall("atom:author/atom:name", ATOM_NS)
        authors = ", ".join((a.text or "").strip() for a in authors_els[:8])
        cats_els = e.findall("atom:category", ATOM_NS)
        cats = " ".join(c.attrib.get("term", "") for c in cats_els)
        primary = e.find("arxiv:primary_category", ATOM_NS)
        primary_cat = primary.attrib.get("term", "") if primary is not None else ""
        out.append({
            "arxiv_id": arxiv_id,
            "title": title,
            "summary": summary,
            "published": published,
            "authors": authors,
            "categories": cats,
            "primary_category": primary_cat,
        })
    return out


def _write_evidence(
    repo_root: Path,
    domain: str,
    run_id: str,
    idx: int,
    paper: dict,
) -> None:
    domain_slug = domain.lower().replace("/", "_")
    evidence_dir = repo_root / "vault" / "evidence" / domain_slug
    raw_dir = repo_root / "vault" / "raw" / domain_slug / run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    arxiv_id = paper["arxiv_id"]
    canonical = f"arxiv:{arxiv_id}"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]
    url = f"https://arxiv.org/abs/{arxiv_id}"

    raw_path = raw_dir / f"{idx:03d}__{digest}.md"
    raw_path.write_text(
        f"---\nrun_id: {run_id}\nidx: {idx}\nsource: arxiv\n"
        f"canonical_id: {canonical}\nurl: {url}\n"
        f"fetched_at: {_utcnow()}\n"
        f"published: {paper['published']}\n"
        f"primary_category: {paper['primary_category']}\n"
        f"---\n\n"
        f"# {paper['title']}\n\n"
        f"**Authors:** {paper['authors']}\n\n"
        f"## Abstract\n\n{paper['summary']}\n",
        encoding="utf-8",
    )

    ev = {
        "run_id": run_id, "record_idx": idx, "cite_id": "",
        "source": "arxiv", "title": paper["title"], "url": url,
        "snippet": paper["summary"][:2000],
        "canonical_id": canonical, "fetched_at": _utcnow(),
        "score": 0.0, "raw_path": str(raw_path.relative_to(repo_root)),
        "metadata": {
            "arxiv_id": arxiv_id,
            "authors": paper["authors"],
            "categories": paper["categories"],
            "primary_category": paper["primary_category"],
            "published": paper["published"],
        },
    }
    ev_path = evidence_dir / f"{run_id}__{idx:03d}__{digest}.json"
    ev_path.write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--category", required=True,
                   help="arXiv category (or comma-separated). e.g. cs.AI,cs.CL")
    p.add_argument("--limit", type=int, default=50,
                   help="Per-category cap (default 50, max 200/req)")
    p.add_argument("--domain", default="research",
                   help="vault/evidence/<domain>/ destination")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    categories = [c.strip() for c in args.category.split(",") if c.strip()]
    run_id = datetime.now().strftime("seed-arxiv-%Y%m%d-%H%M%S")
    total = 0

    for ci, cat in enumerate(categories):
        if ci > 0:
            time.sleep(RATE_LIMIT_SEC)
        try:
            papers = _fetch_category(cat, args.limit)
        except Exception as exc:                                  # noqa: BLE001
            sys.stderr.write(f"  ⚠ {cat}: {type(exc).__name__}: {exc}\n")
            continue
        sys.stderr.write(f"  got {len(papers)} papers from {cat}\n")
        for paper in papers:
            if not paper.get("arxiv_id"):
                continue
            total += 1
            if not args.dry_run:
                _write_evidence(repo_root, args.domain, run_id, total, paper)

    sys.stderr.write(
        f"\n✅ done. wrote {total} papers under run_id={run_id}\n"
        f"   evidence: vault/evidence/{args.domain}/\n"
        f"   raw:      vault/raw/{args.domain}/{run_id}/\n"
    )
    print(run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
