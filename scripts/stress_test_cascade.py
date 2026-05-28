#!/usr/bin/env python3
"""Cascade stress test — 5 domains × 10 queries = 50 retrieves.

Measures:
* per-source success rate
* per-source p50 / p95 latency
* end-to-end retrieve latency
* records returned per query

Output:
* stderr: progress
* stdout: JSON summary report

Designed to surface real connector flakiness (not just one-shot
``retrieve-doctor`` health pings).
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path


TEST_PLAN: dict[str, list[str]] = {
    "research": [
        "transformer attention 2025",
        "diffusion models survey",
        "reinforcement learning from human feedback",
        "open-vocabulary detection",
        "vision language models scaling laws",
        "chain of thought reasoning",
        "constitutional AI alignment",
        "model interpretability circuits",
        "neural architecture search",
        "few-shot learning meta-learning",
    ],
    "ai_progress": [
        "Claude 4 features",
        "Anthropic latest announcement",
        "OpenAI o-series reasoning",
        "Karpathy software 2.0",
        "DeepSeek V4 benchmarks",
        "Gemini multimodal 2026",
        "Llama 4 open source",
        "AI agent autonomy 2026",
        "Sora video generation update",
        "NVIDIA Blackwell GPU",
    ],
    "finance": [
        "Federal Reserve interest rate decision",
        "S&P 500 sector rotation",
        "NVDA earnings report",
        "Tesla 10-K cash flow",
        "Apple services revenue",
        "Microsoft cloud growth",
        "yield curve inversion",
        "inflation expectations CPI",
        "Berkshire Hathaway portfolio",
        "regional bank stress test",
    ],
    "us_policy": [
        "AI executive order Biden",
        "Trump AI policy 2026",
        "Section 230 reform",
        "antitrust Big Tech investigation",
        "Inflation Reduction Act semiconductor",
        "FCC broadband rules",
        "SCOTUS major cases 2026",
        "FDA cybersecurity medical devices",
        "ITC export control AI chips",
        "EPA climate disclosure",
    ],
    "international_relations": [
        "China US tariffs 2026",
        "Russia Ukraine ceasefire negotiations",
        "Israel Hamas Gaza",
        "Taiwan strait military",
        "EU AI Act enforcement",
        "BRICS expansion",
        "South China Sea Philippines",
        "Iran nuclear talks",
        "Middle East peace deal",
        "ASEAN summit Indonesia",
    ],
}


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    from omni_hub.retrieval import Cascade, builtin_sources
    from omni_hub.retrieval.cascade import DEFAULT_DOMAIN_CASCADES

    sources = builtin_sources()
    cascade = Cascade(sources=sources, cascades=DEFAULT_DOMAIN_CASCADES)

    # Per-source success / failure / latency tracking.
    per_source_attempts: dict[str, int] = defaultdict(int)
    per_source_success: dict[str, int] = defaultdict(int)
    per_source_records: dict[str, int] = defaultdict(int)
    e2e_latencies: list[float] = []
    queries_total = 0
    queries_returned_records = 0

    for domain, queries in TEST_PLAN.items():
        for q in queries:
            queries_total += 1
            t0 = time.time()
            try:
                result = cascade.retrieve(
                    q,
                    domain=domain,
                    per_source_limit=5,
                    total_limit=10,
                    fusion="rrf",
                )
                dt = time.time() - t0
                e2e_latencies.append(dt)
                if result.records:
                    queries_returned_records += 1
                # Source-level attempts
                for src in result.sources_tried or []:
                    per_source_attempts[src] += 1
                for src in result.sources_succeeded or []:
                    per_source_success[src] += 1
                # Per-source record counts
                src_counts: dict[str, int] = defaultdict(int)
                for r in result.records or []:
                    src_counts[r.source] += 1
                for s, c in src_counts.items():
                    per_source_records[s] += c
                sys.stderr.write(
                    f"  [{domain:12s}] q={q[:40]!r:42s} "
                    f"records={len(result.records or []):2d} "
                    f"e2e={dt:.1f}s\n"
                )
            except Exception as exc:                              # noqa: BLE001
                dt = time.time() - t0
                e2e_latencies.append(dt)
                sys.stderr.write(
                    f"  [{domain:12s}] q={q[:40]!r:42s} CASCADE_ERROR "
                    f"{type(exc).__name__}: {str(exc)[:80]}\n"
                )

    # Summary
    all_sources = sorted(set(per_source_attempts.keys()))
    rows = []
    for s in all_sources:
        att = per_source_attempts[s]
        succ = per_source_success[s]
        recs = per_source_records[s]
        rate = (succ / att * 100) if att else 0.0
        rows.append({
            "source": s,
            "attempts": att,
            "successes": succ,
            "success_rate_pct": round(rate, 1),
            "total_records": recs,
            "avg_records_per_success": round(recs / max(succ, 1), 1),
        })
    rows.sort(key=lambda r: (-r["attempts"], r["source"]))

    e2e_summary: dict[str, float] = {}
    if e2e_latencies:
        e2e_summary["p50_s"] = round(statistics.median(e2e_latencies), 2)
        e2e_summary["p95_s"] = round(
            statistics.quantiles(e2e_latencies, n=20)[-1]
            if len(e2e_latencies) >= 20 else max(e2e_latencies),
            2,
        )
        e2e_summary["mean_s"] = round(statistics.mean(e2e_latencies), 2)
        e2e_summary["max_s"] = round(max(e2e_latencies), 2)

    report = {
        "queries_total": queries_total,
        "queries_with_records": queries_returned_records,
        "e2e_latency": e2e_summary,
        "per_source": rows,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
