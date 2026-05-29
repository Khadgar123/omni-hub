#!/usr/bin/env python3
"""Bulk PDF corpus downloader for accepted papers — resumable, rate-limited,
incremental.

Reads ``.omni/accepted/accepted_index.jsonl`` (from ``scripts/crawl_accepted.py``),
resolves the best PDF URL per paper (arXiv > open-access > OpenReview >
publisher full-text link), and downloads to ``vault/raw/papers/<canonical>.pdf``.
This is the corpus-acquisition front of the research closed-loop: the PDFs it
lands are what ResearchFlow / MinerU then deep-parse into evidence → claims.

Designed for the ~65k top-AI-conference corpus (~130 GB):

* **RESUMABLE**   — skips files already on disk; a re-run only fetches missing.
* **INCREMENTAL** — re-crawl + re-run later pulls only newly-accepted papers.
* **RATE-LIMITED**— per-host min interval (arXiv 3 s/req; others 1 s), polite.
* **FAIL-SOFT**   — per-paper errors go to the manifest, never abort the run.
* **BUDGETED**    — ``--max-gb`` stops cleanly at a disk budget; ``--dry-run``
                    resolves URLs + reports count / estimated size, no download.

NOTE: the full 65k pull is a multi-HOUR/day job (arXiv 1 req/3 s ≈ 50 h for
65k).  Run it on a real network and let it resume across sessions.

Usage::

    python3 scripts/download_pdfs.py --dry-run                 # resolve + estimate
    python3 scripts/download_pdfs.py --limit 100               # first 100
    python3 scripts/download_pdfs.py --max-gb 130              # full, disk-capped
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

_HOST_MIN_INTERVAL = {"arxiv.org": 3.0, "export.arxiv.org": 3.0}
_DEFAULT_MIN_INTERVAL = 1.0
_AVG_PDF_BYTES = 2_000_000  # ~2 MB/paper, for dry-run size estimates


def resolve_pdf_url(rec: dict) -> str:
    """Best downloadable PDF URL for a paper record.

    arXiv first (free, reliable, version-stable) — the merged record carries
    ``arxiv_base_id`` for most AI papers via the identity bridge; then explicit
    open-access PDF, then OpenReview, then a publisher full-text PDF link.
    """
    md = rec.get("metadata") or {}
    cid = str(rec.get("canonical_id") or "")

    aid = md.get("arxiv_base_id") or md.get("arxiv_id") or (
        cid[len("arxiv:"):] if cid.startswith("arxiv:") else ""
    )
    if aid:
        return f"https://arxiv.org/pdf/{str(aid).strip()}.pdf"

    for k in ("oa_pdf_url", "open_access_pdf", "pdf_url"):
        v = md.get(k)
        if v:
            return str(v)

    for link in (md.get("full_text_links") or []):
        u = str((link or {}).get("url", ""))
        ct = str((link or {}).get("content_type", ""))
        if u and ("pdf" in ct.lower() or u.lower().endswith(".pdf")):
            return u

    fid = md.get("forum_id") or (
        cid[len("openreview:"):] if cid.startswith("openreview:") else ""
    )
    if fid:
        return f"https://openreview.net/pdf?id={str(fid).strip()}"
    return ""


def _safe_name(canonical_id: str, idx: int) -> str:
    s = canonical_id or f"paper_{idx}"
    cleaned = "".join(c if (c.isalnum() or c in "._-") else "_" for c in s)
    return cleaned[:120] + ".pdf"


def _default_fetch(url: str, timeout: float = 30.0) -> bytes:
    import urllib.request
    req = urllib.request.Request(
        url, headers={"User-Agent": "omni-hub-paper-corpus/0.1"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def download_corpus(
    records,
    out_dir: Path,
    *,
    limit: int = 0,
    max_bytes: int = 0,
    dry_run: bool = False,
    fetch=_default_fetch,
    sleep=time.sleep,
    clock=time.monotonic,
    manifest_path: Path | None = None,
):
    """Download PDFs for ``records`` into ``out_dir``.  Pure-ish + injectable
    (``fetch``/``sleep``/``clock``) so it is unit-testable without network.

    Returns ``(stats, manifest)``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    last_hit: dict[str, float] = {}
    total_bytes = 0
    stats = {
        "resolved": 0, "no_url": 0, "skipped_existing": 0,
        "downloaded": 0, "failed": 0, "bytes": 0, "would_download": 0,
    }
    manifest: list[dict] = []

    for i, rec in enumerate(records):
        if limit and (stats["downloaded"] + stats["skipped_existing"]) >= limit:
            break
        cid = str(rec.get("canonical_id") or f"idx_{i}")
        url = resolve_pdf_url(rec)
        if not url:
            stats["no_url"] += 1
            manifest.append({"canonical_id": cid, "status": "no_url"})
            continue
        stats["resolved"] += 1
        target = out_dir / _safe_name(cid, i)
        if target.exists() and target.stat().st_size > 0:
            stats["skipped_existing"] += 1
            continue
        if dry_run:
            stats["would_download"] += 1
            manifest.append({"canonical_id": cid, "url": url, "status": "would_download"})
            continue
        if max_bytes and total_bytes >= max_bytes:
            manifest.append({"canonical_id": cid, "url": url, "status": "budget_reached"})
            break

        host = urlparse(url).netloc.lower()
        interval = _HOST_MIN_INTERVAL.get(host, _DEFAULT_MIN_INTERVAL)
        prev = last_hit.get(host)
        if prev is not None:
            deficit = interval - (clock() - prev)
            if deficit > 0:
                sleep(deficit)
        last_hit[host] = clock()

        try:
            data = fetch(url)
        except Exception as exc:  # noqa: BLE001 - one paper must not abort the run
            stats["failed"] += 1
            manifest.append({"canonical_id": cid, "url": url, "status": "failed",
                             "error": str(exc)[:120]})
            continue
        target.write_bytes(data)
        total_bytes += len(data)
        stats["downloaded"] += 1
        stats["bytes"] += len(data)
        manifest.append({"canonical_id": cid, "url": url, "status": "ok",
                         "bytes": len(data), "path": str(target)})

    if manifest_path is not None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with manifest_path.open("w", encoding="utf-8") as f:
            for m in manifest:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
    return stats, manifest


def _load_index(path: Path):
    if not path.exists():
        sys.stderr.write(f"index not found: {path}\n"
                         f"run scripts/crawl_accepted.py first\n")
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--index", default=".omni/accepted/accepted_index.jsonl")
    p.add_argument("--out", default="vault/raw/papers")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--max-gb", type=float, default=0.0, help="disk budget (GB)")
    p.add_argument("--dry-run", action="store_true",
                   help="resolve URLs + estimate size, do not download")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    root = Path(__file__).resolve().parent.parent
    records = _load_index(root / args.index)
    if not records:
        return 2
    stats, _ = download_corpus(
        records,
        root / args.out,
        limit=args.limit,
        max_bytes=int(args.max_gb * 1e9),
        dry_run=args.dry_run,
        manifest_path=root / ".omni" / "accepted" / "pdf_manifest.jsonl",
    )
    if args.dry_run:
        est_gb = stats["would_download"] * _AVG_PDF_BYTES / 1e9
        stats["estimated_gb"] = round(est_gb, 1)
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(f"# papers={len(records)} resolved={stats['resolved']} "
              f"no_url={stats['no_url']} downloaded={stats['downloaded']} "
              f"skipped={stats['skipped_existing']} failed={stats['failed']}")
        if args.dry_run:
            print(f"# would download {stats['would_download']} PDFs "
                  f"(~{stats.get('estimated_gb', 0)} GB at ~2 MB/paper)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
