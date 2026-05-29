#!/usr/bin/env python3
"""Benchmark matrix — 7 domains × 3 query styles = 21 real cascade runs.

Query styles per domain:
* ``factoid`` — short factual lookup ("Claude 4 release date")
* ``analytical`` — comparative / why ("LLaMA 4 vs DeepSeek V4 strengths")
* ``temporal`` — time-bounded latest ("OpenAI announcements last 30 days")

For each (domain, query, query_id) we record:
* records returned (count, sources)
* RRF top-5 + BGE top-5 (rerank delta)
* e2e latency + per-source latency
* a 1-line snippet of the top-1 result (for human eyeball)

Output: JSON to /tmp/benchmark.json + Markdown to
.omni/reports/benchmark-<YYYY-MM-DD>.md
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path


PLAN: list[tuple[str, str, str, str]] = [
    # (domain, style, query, qid)
    # ai_progress
    ("ai_progress", "factoid",    "Claude 4.6 features release notes",       "ai-f1"),
    ("ai_progress", "analytical", "DeepSeek V4 vs Anthropic Claude 4 reasoning depth comparison", "ai-a1"),
    ("ai_progress", "temporal",   "OpenAI o-series latest model 2026",       "ai-t1"),
    # research
    ("research",    "factoid",    "diffusion transformer paper SiT 2024",    "res-f1"),
    ("research",    "analytical", "constitutional AI vs RLHF tradeoffs",     "res-a1"),
    ("research",    "temporal",   "ICLR 2026 vision language papers list",   "res-t1"),
    # finance
    ("finance",     "factoid",    "Federal Reserve 2026 interest rate decision",     "fin-f1"),
    ("finance",     "analytical", "NVDA vs TSLA Q1 2026 earnings comparison",        "fin-a1"),
    ("finance",     "temporal",   "S&P 500 sector rotation last 30 days",            "fin-t1"),
    # us_policy
    ("us_policy",   "factoid",    "Section 230 reform bill status",                  "uspol-f1"),
    ("us_policy",   "analytical", "Inflation Reduction Act semiconductor impact",    "uspol-a1"),
    ("us_policy",   "temporal",   "Federal Register AI rules last 90 days",          "uspol-t1"),
    # international_relations
    ("international_relations", "factoid",    "BRICS expansion 2026 members",         "ir-f1"),
    ("international_relations", "analytical", "Russia Ukraine war attrition vs negotiation status",
                                                                                       "ir-a1"),
    ("international_relations", "temporal",   "Israel Gaza ceasefire negotiations latest",
                                                                                       "ir-t1"),
    # enterprise
    ("enterprise",  "factoid",    "Anthropic Bun acquisition details",               "ent-f1"),
    ("enterprise",  "analytical", "NVIDIA Blackwell vs AMD MI400 enterprise adoption", "ent-a1"),
    ("enterprise",  "temporal",   "Tesla Q1 2026 8-K filings",                       "ent-t1"),
    # biomedical
    ("biomedical",  "factoid",    "AlphaFold 3 protein structure prediction",       "bio-f1"),
    ("biomedical",  "analytical", "GLP-1 drugs efficacy comparison weight loss",     "bio-a1"),
    ("biomedical",  "temporal",   "cancer immunotherapy clinical trial results 2026", "bio-t1"),
]


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    from omni_hub.retrieval import Cascade, builtin_sources
    from omni_hub.retrieval.cascade import DEFAULT_DOMAIN_CASCADES
    try:
        from omni_hub.retrieval.bge_reranker import bge_rerank
        BGE_AVAILABLE = True
    except ImportError:
        BGE_AVAILABLE = False

    sources = builtin_sources()
    cascade = Cascade(sources=sources, cascades=DEFAULT_DOMAIN_CASCADES)

    results: list[dict] = []
    e2e_times: list[float] = []
    domain_records: dict[str, list[int]] = defaultdict(list)
    style_records: dict[str, list[int]] = defaultdict(list)

    for domain, style, query, qid in PLAN:
        sys.stderr.write(f"\n=== [{qid}] {domain}/{style}: {query[:60]} ===\n")
        t0 = time.time()
        try:
            result = cascade.retrieve(
                query, domain=domain,
                per_source_limit=8, total_limit=15, fusion="rrf",
            )
        except Exception as exc:                                  # noqa: BLE001
            sys.stderr.write(f"  cascade error: {type(exc).__name__}: {exc}\n")
            continue
        dt = time.time() - t0
        e2e_times.append(dt)
        records = result.records or []
        domain_records[domain].append(len(records))
        style_records[style].append(len(records))

        rrf_top5 = [
            {"source": r.source, "title": r.title[:120], "url": r.url[:200], "score": r.score}
            for r in records[:5]
        ]

        bge_top5: list[dict] = []
        bge_delta_count = 0
        if BGE_AVAILABLE and records:
            try:
                ranked = bge_rerank(query, list(records), top_k=5)
                bge_top5 = [
                    {"source": r.source, "title": r.title[:120],
                     "url": r.url[:200], "score": r.score}
                    for r in ranked
                ]
                # Movement count: how many bge-top-5 weren't in rrf-top-5
                rrf_canonical = {r.canonical_id or r.title for r in records[:5]}
                bge_delta_count = sum(
                    1 for r in ranked
                    if (r.canonical_id or r.title) not in rrf_canonical
                )
            except Exception as exc:                              # noqa: BLE001
                sys.stderr.write(f"  bge rerank err: {exc}\n")

        results.append({
            "qid": qid,
            "domain": domain,
            "style": style,
            "query": query,
            "e2e_s": round(dt, 2),
            "record_count": len(records),
            "sources_tried": result.sources_tried,
            "sources_succeeded": result.sources_succeeded,
            "rrf_top5": rrf_top5,
            "bge_top5": bge_top5,
            "bge_delta_in_top5": bge_delta_count,                 # how many BGE pulled in from outside RRF top-5
        })
        sys.stderr.write(
            f"  records={len(records)}  sources_ok={len(result.sources_succeeded)}/"
            f"{len(result.sources_tried)}  e2e={dt:.1f}s"
        )
        if bge_top5:
            sys.stderr.write(f"  bge_delta_top5={bge_delta_count}")
        sys.stderr.write("\n")

    # Summary
    summary = {
        "total_queries": len(results),
        "queries_with_records": sum(1 for r in results if r["record_count"] > 0),
        "e2e_latency_p50_s": round(statistics.median(e2e_times), 2) if e2e_times else None,
        "e2e_latency_p95_s": round(
            statistics.quantiles(e2e_times, n=20)[-1] if len(e2e_times) >= 20 else max(e2e_times),
            2,
        ) if e2e_times else None,
        "records_by_domain": {d: sum(c) for d, c in domain_records.items()},
        "records_by_style": {s: sum(c) for s, c in style_records.items()},
        "bge_total_delta": sum(r.get("bge_delta_in_top5", 0) for r in results),
        "bge_available": BGE_AVAILABLE,
    }

    report = {"summary": summary, "queries": results}
    out_path = Path("/tmp/benchmark.json")
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.stderr.write(f"\n✅ JSON: {out_path}\n")

    # Markdown
    today = date.today().isoformat()
    md_dir = repo_root / ".omni" / "reports"
    md_dir.mkdir(parents=True, exist_ok=True)
    md_path = md_dir / f"benchmark-{today}.md"
    lines = [
        f"# Benchmark matrix — {today}",
        "",
        f"_{len(results)} queries · {summary['queries_with_records']} with records_",
        f"_e2e p50={summary['e2e_latency_p50_s']}s · p95={summary['e2e_latency_p95_s']}s_",
        f"_BGE rerank: total {summary['bge_total_delta']} delta-in-top5 across {len(results)} queries_",
        "",
        "## By domain",
        "",
        "| domain | total records (top 15 × N) |",
        "| --- | ---: |",
    ]
    for d, n in summary["records_by_domain"].items():
        lines.append(f"| {d} | {n} |")
    lines.extend([
        "",
        "## By query style",
        "",
        "| style | total records |",
        "| --- | ---: |",
    ])
    for s, n in summary["records_by_style"].items():
        lines.append(f"| {s} | {n} |")

    lines.extend(["", "## Per-query detail", ""])
    for r in results:
        lines.append(f"### {r['qid']} ({r['domain']}/{r['style']})")
        lines.append("")
        lines.append(f"**Query**: {r['query']}")
        lines.append("")
        lines.append(f"- records: {r['record_count']}")
        lines.append(f"- sources ok: {len(r['sources_succeeded'])}/{len(r['sources_tried'])}")
        lines.append(f"- e2e: {r['e2e_s']}s")
        lines.append(f"- BGE delta in top-5: {r.get('bge_delta_in_top5', 0)}")
        lines.append("")
        if r["bge_top5"]:
            lines.append("**BGE top-5**:")
            for i, item in enumerate(r["bge_top5"], 1):
                lines.append(f"{i}. `{item['source']}` ({item['score']:+.2f}) {item['title']}")
        else:
            lines.append("**RRF top-5**:")
            for i, item in enumerate(r["rrf_top5"], 1):
                lines.append(f"{i}. `{item['source']}` {item['title']}")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    sys.stderr.write(f"✅ Markdown: {md_path}\n")
    print(str(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
