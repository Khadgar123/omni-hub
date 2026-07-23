#!/usr/bin/env python3
"""longform-capture entrypoint — one URL → cleaned full-text evidence.

Auto-routes by URL shape:
* youtube.com / youtu.be → youtube_transcript
* anything else (static)  → trafilatura, with jina_reader fallback

Writes a vault/evidence/<domain>/ record (+ raw markdown) so the
captured long-form piece flows through the standard ingest chain.

Usage::

    python3 scripts/longform_capture.py https://karpathy.github.io/2026/02/12/microgpt/
    python3 scripts/longform_capture.py https://youtu.be/LCEmiRjPEtQ --domain ai_progress
    python3 scripts/longform_capture.py <url> --dry-run          # don't write, just show
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _infer_domain(url: str) -> str:
    u = url.lower()
    if any(s in u for s in ("arxiv.org", ".edu", "openreview", "semanticscholar")):
        return "research"
    if any(s in u for s in ("karpathy", "lilianweng", "anthropic.com", "openai.com",
                            "deepmind", "huggingface")):
        return "ai_progress"
    if any(s in u for s in ("sec.gov", "federalreserve", "stlouisfed")):
        return "finance"
    if any(s in u for s in ("federalregister", "congress.gov", "regulations.gov",
                            "whitehouse", "supremecourt")):
        return "us_policy"
    if any(s in u for s in ("gov.cn", "pbc.gov", "stats.gov.cn")):
        return "cn_policy"
    return "default"


def _capture(url: str, domain: str) -> dict | None:
    """Route URL → best connector → normalized record dict."""

    u = url.lower()
    is_youtube = ("youtube.com/watch" in u) or ("youtu.be/" in u) or ("/shorts/" in u)

    if is_youtube:
        from omni_hub.retrieval.youtube_transcript import YouTubeTranscriptSource
        recs = YouTubeTranscriptSource().retrieve(url, limit=1)
        connector = "youtube_transcript"
    else:
        # Try Trafilatura first; fall back to Jina Reader.
        recs = []
        connector = "trafilatura"
        try:
            from omni_hub.retrieval.trafilatura_source import TrafilaturaSource
            recs = TrafilaturaSource().retrieve(url, limit=1)
        except Exception as exc:                                 # noqa: BLE001
            sys.stderr.write(f"  trafilatura failed: {type(exc).__name__}\n")
        if not recs:
            try:
                from omni_hub.retrieval.jina_reader import JinaReaderFetcher
                recs = JinaReaderFetcher().retrieve(url, limit=1)
                connector = "jina_reader"
            except Exception as exc:                             # noqa: BLE001
                sys.stderr.write(f"  jina_reader failed: {type(exc).__name__}\n")

    if not recs:
        return None
    r = recs[0]
    full_text = (r.metadata or {}).get("full_text", "") or r.snippet
    return {
        "connector": connector,
        "title": r.title,
        "url": r.url or url,
        "snippet": r.snippet,
        "full_text": full_text,
        "metadata": r.metadata or {},
        "canonical_id": r.canonical_id or f"longform:{url}",
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("url", help="the long-form URL (blog / YouTube / docs)")
    p.add_argument("--domain", default="", help="vault/evidence/<domain>/ (auto-inferred if omitted)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    domain = args.domain or _infer_domain(args.url)
    sys.stderr.write(f"# capturing {args.url}\n#   domain={domain}\n")
    captured = _capture(args.url, domain)
    if not captured:
        sys.stderr.write("✗ capture failed (no content extracted)\n")
        return 3

    sys.stderr.write(
        f"  ✓ {captured['connector']}: {captured['title'][:70]} "
        f"({len(captured['full_text'])} chars)\n"
    )

    if args.dry_run:
        print(json.dumps({k: (v[:200] if isinstance(v, str) else v)
                          for k, v in captured.items()}, ensure_ascii=False, indent=2))
        return 0

    # Write evidence + raw
    run_id = datetime.now().strftime("longform-%Y%m%d-%H%M%S")
    digest = hashlib.sha256(captured["canonical_id"].encode()).hexdigest()[:10]
    ev_dir = repo_root / "vault" / "evidence" / domain
    raw_dir = repo_root / "vault" / "raw" / domain / run_id
    ev_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / f"001__{digest}.md"
    raw_path.write_text(
        f"---\nrun_id: {run_id}\nsource: {captured['connector']}\n"
        f"url: {captured['url']}\nfetched_at: {_utcnow()}\n---\n\n"
        f"# {captured['title']}\n\n{captured['full_text']}\n",
        encoding="utf-8",
    )
    ev = {
        "run_id": run_id, "record_idx": 1, "source": captured["connector"],
        "title": captured["title"], "url": captured["url"],
        "snippet": captured["full_text"][:2000],
        "canonical_id": captured["canonical_id"], "fetched_at": _utcnow(),
        "score": 0.0, "raw_path": str(raw_path.relative_to(repo_root)),
        "metadata": captured["metadata"],
    }
    ev_path = ev_dir / f"{run_id}__001__{digest}.json"
    ev_path.write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")

    sys.stderr.write(f"  → evidence: {ev_path.relative_to(repo_root)}\n")
    sys.stderr.write(f"  → raw:      {raw_path.relative_to(repo_root)}\n")
    sys.stderr.write(f"  next: omni-hub wiki-ingest (after seed_bridge) for {run_id}\n")
    print(run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
