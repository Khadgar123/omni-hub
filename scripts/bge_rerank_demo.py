#!/usr/bin/env python3
"""BGE-reranker demo: compare RRF-fused vs BGE-reranked top results.

Runs a real cascade query, then re-scores the same records with
BAAI/bge-reranker-v2-m3 and shows side-by-side which docs the
cross-encoder thinks are more relevant.

Usage::

    python3 scripts/bge_rerank_demo.py
    python3 scripts/bge_rerank_demo.py --query "transformer attention" --domain research
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--query", default="LLM agents tool use 2026")
    p.add_argument("--domain", default="ai_progress")
    p.add_argument("--limit", type=int, default=15,
                   help="how many candidates to fetch + rerank")
    p.add_argument("--top", type=int, default=5,
                   help="show top N before / after")
    args = p.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    from omni_hub.retrieval import Cascade, builtin_sources
    from omni_hub.retrieval.cascade import DEFAULT_DOMAIN_CASCADES
    from omni_hub.retrieval.bge_reranker import bge_rerank, MODEL_NAME

    sources = builtin_sources()
    cascade = Cascade(
        sources=sources,
        cascades=DEFAULT_DOMAIN_CASCADES,
    )

    # 1) Run real cascade (RRF fused)
    sys.stderr.write(f"# cascade query: {args.query!r} (domain={args.domain})\n")
    t0 = time.time()
    result = cascade.retrieve(
        args.query,
        domain=args.domain,
        per_source_limit=args.limit,
        total_limit=args.limit,
        fusion="rrf",
    )
    t_fetch = time.time() - t0
    records = result.records
    sys.stderr.write(
        f"# fetched {len(records)} records in {t_fetch:.1f}s "
        f"from sources: {result.sources_tried}\n"
    )

    if not records:
        sys.stderr.write("no records — abort\n")
        return 2

    # 2) Capture RRF order
    rrf_top = [(r.title[:75], r.source, r.score) for r in records[:args.top]]

    # 3) Run BGE-reranker (lazy loads model on first call)
    sys.stderr.write(f"\n# loading {MODEL_NAME} (first call may download ~600MB)...\n")
    t0 = time.time()
    reranked = bge_rerank(args.query, list(records))
    t_rerank = time.time() - t0
    sys.stderr.write(f"# reranked in {t_rerank:.1f}s\n")

    bge_top = [(r.title[:75], r.source, r.score) for r in reranked[:args.top]]

    # 4) Side-by-side comparison
    print(f"# Query: {args.query}\n")
    print(f"## RRF top {args.top}\n")
    for i, (title, src, score) in enumerate(rrf_top, 1):
        print(f"{i}. [{src:18s}] (rrf={score:.3f})  {title}")
    print(f"\n## BGE top {args.top}\n")
    for i, (title, src, score) in enumerate(bge_top, 1):
        print(f"{i}. [{src:18s}] (bge={score:+.3f})  {title}")

    # 5) Show movement
    print("\n## Movement (how rank changed)\n")
    rrf_ids = [t[0] for t in rrf_top]
    bge_ids = [t[0] for t in bge_top]
    for new_rank, (title, _, _) in enumerate(bge_top, 1):
        old_rank = (rrf_ids.index(title) + 1) if title in rrf_ids else None
        if old_rank is None:
            label = f"NEW (was outside top-{args.top})"
        elif old_rank == new_rank:
            label = "—"
        elif old_rank > new_rank:
            label = f"↑ from #{old_rank}"
        else:
            label = f"↓ from #{old_rank}"
        print(f"  #{new_rank}: {label:30s} {title}")

    print(f"\n_RRF fetch {t_fetch:.1f}s | BGE rerank {t_rerank:.1f}s | "
          f"reranker={MODEL_NAME}_")
    return 0


if __name__ == "__main__":
    sys.exit(main())
