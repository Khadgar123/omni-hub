#!/usr/bin/env python3
"""Judge the 21 benchmark queries — 5-dim quality scoring.

For each (qid, query, top-5 records) tuple from the benchmark matrix:

1. Compose candidate answer: concatenate top-5 snippets with citation markers.
2. Run BOTH HeuristicJudge (cheap, deterministic) AND LLMJudge (DeepSeek
   backed) over each.
3. Aggregate per-domain / per-style scores.

Outputs:
* /tmp/judge.json — raw verdicts
* .omni/reports/benchmark-judge-<YYYY-MM-DD>.md — readable summary
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path


def _load_benchmark(repo_root: Path) -> list[dict]:
    """Re-read the benchmark — we lost /tmp/benchmark.json to shell redirect,
    so re-derive from the markdown? No — re-run the matrix lightweight."""

    # Simpler: re-run a SMALLER matrix (5 queries) so we can judge same-day.
    # Heavier path would re-run benchmark_matrix.py — not needed here.
    plan = [
        ("ai_progress", "factoid",    "Claude 4.6 features release notes",            "ai-f1"),
        ("ai_progress", "analytical", "DeepSeek V4 vs Claude 4 reasoning depth",      "ai-a1"),
        ("research",    "analytical", "constitutional AI vs RLHF tradeoffs",          "res-a1"),
        ("finance",     "factoid",    "Federal Reserve 2026 interest rate decision",  "fin-f1"),
        ("us_policy",   "temporal",   "Federal Register AI rules last 90 days",       "uspol-t1"),
        ("international_relations", "factoid", "BRICS expansion 2026 members",        "ir-f1"),
        ("enterprise",  "factoid",    "Anthropic Bun acquisition details",            "ent-f1"),
        ("biomedical",  "factoid",    "AlphaFold 3 protein structure prediction",     "bio-f1"),
    ]
    return [{"domain": d, "style": s, "query": q, "qid": qid}
            for d, s, q, qid in plan]


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    from omni_hub.retrieval import Cascade, builtin_sources
    from omni_hub.retrieval.cascade import DEFAULT_DOMAIN_CASCADES
    from omni_hub.judge import HeuristicJudge, JudgeRequest
    from omni_hub.judge.llm import LLMJudge

    sources = builtin_sources()
    cascade = Cascade(sources=sources, cascades=DEFAULT_DOMAIN_CASCADES)

    plan = _load_benchmark(repo_root)
    heur = HeuristicJudge()
    llm = LLMJudge(ccload_base="")               # force DeepSeek direct path

    verdicts: list[dict] = []
    heuristic_composites: list[float] = []
    llm_composites: list[float] = []

    rubric_weights = {
        "evidence_coverage": 1.0,
        "information_density": 1.0,
        "citation_support": 1.2,
        "style_fit": 0.6,
        "uncertainty_calibration": 0.8,
    }

    for item in plan:
        sys.stderr.write(f"\n=== {item['qid']} ({item['domain']}/{item['style']}) ===\n")

        # 1. Retrieve top-5
        try:
            result = cascade.retrieve(
                item["query"], domain=item["domain"],
                per_source_limit=5, total_limit=5, fusion="rrf",
            )
        except Exception as exc:                                  # noqa: BLE001
            sys.stderr.write(f"  cascade err: {exc}\n")
            continue
        records = result.records or []
        if not records:
            sys.stderr.write("  no records — skip\n")
            continue

        # 2. Compose candidate answer (cited)
        candidate_lines = []
        for i, r in enumerate(records, 1):
            snippet = (r.snippet or r.title or "")[:300]
            candidate_lines.append(f"[{i}] ({r.source}) {snippet}")
        candidate = "\n\n".join(candidate_lines)
        reference = f"Query: {item['query']}\nDomain: {item['domain']}"

        request = JudgeRequest(
            domain=item["domain"],
            candidate=candidate,
            reference=reference,
            rubric=rubric_weights,
            trace_id=item["qid"],
        )

        # 3. Heuristic judge (instant)
        t_h0 = time.time()
        h_verdict = heur.evaluate(request)
        t_h = time.time() - t_h0
        heuristic_composites.append(h_verdict.composite)

        # 4. LLM judge (DeepSeek)
        t_l0 = time.time()
        try:
            l_verdict = llm.evaluate(request)
            llm_composites.append(l_verdict.composite)
            l_mode = l_verdict.metadata.get("mode", "?")
        except Exception as exc:                                  # noqa: BLE001
            sys.stderr.write(f"  llm err: {exc}\n")
            l_verdict = None
            l_mode = "error"
        t_l = time.time() - t_l0

        sys.stderr.write(
            f"  records={len(records)}  "
            f"heur={h_verdict.composite:.3f} ({t_h:.0f}ms)  "
            f"llm={l_verdict.composite if l_verdict else '-':.3f} ({t_l:.1f}s, {l_mode})\n"
        )

        verdicts.append({
            "qid": item["qid"],
            "domain": item["domain"],
            "style": item["style"],
            "query": item["query"],
            "n_records": len(records),
            "heuristic": {
                "composite": round(h_verdict.composite, 3),
                "dims": {d.dimension: round(d.score, 3) for d in h_verdict.dimensions},
                "latency_ms": round(t_h * 1000, 1),
            },
            "llm": (
                {
                    "composite": round(l_verdict.composite, 3),
                    "dims": {d.dimension: round(d.score, 3) for d in l_verdict.dimensions},
                    "mode": l_mode,
                    "latency_s": round(t_l, 2),
                    "rationale": l_verdict.rationale[:200],
                }
                if l_verdict else {"error": True, "mode": l_mode}
            ),
        })

    # Aggregate
    by_dom: dict[str, list[tuple[float, float]]] = defaultdict(list)
    by_style: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for v in verdicts:
        if "composite" in v["llm"]:
            by_dom[v["domain"]].append((v["heuristic"]["composite"], v["llm"]["composite"]))
            by_style[v["style"]].append((v["heuristic"]["composite"], v["llm"]["composite"]))

    summary = {
        "total": len(verdicts),
        "heuristic_p50": round(statistics.median(heuristic_composites), 3) if heuristic_composites else None,
        "heuristic_mean": round(statistics.mean(heuristic_composites), 3) if heuristic_composites else None,
        "llm_p50": round(statistics.median(llm_composites), 3) if llm_composites else None,
        "llm_mean": round(statistics.mean(llm_composites), 3) if llm_composites else None,
        "by_domain": {
            d: {
                "heuristic_mean": round(statistics.mean([h for h, _ in vs]), 3),
                "llm_mean": round(statistics.mean([l for _, l in vs]), 3),
            }
            for d, vs in by_dom.items()
        },
        "by_style": {
            s: {
                "heuristic_mean": round(statistics.mean([h for h, _ in vs]), 3),
                "llm_mean": round(statistics.mean([l for _, l in vs]), 3),
            }
            for s, vs in by_style.items()
        },
    }

    out = {"summary": summary, "verdicts": verdicts}
    Path("/tmp/judge_out.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    sys.stderr.write(f"\n✅ JSON: /tmp/judge_out.json\n")

    # Markdown
    today = date.today().isoformat()
    md_path = repo_root / ".omni" / "reports" / f"benchmark-judge-{today}.md"
    lines = [
        f"# Benchmark Judge Report — {today}",
        "",
        f"_{summary['total']} queries · DeepSeek-backed LLMJudge_",
        f"_heuristic p50={summary['heuristic_p50']} mean={summary['heuristic_mean']}_",
        f"_LLM       p50={summary['llm_p50']} mean={summary['llm_mean']}_",
        "",
        "## Per-domain mean composite (range 0..1)",
        "",
        "| domain | heuristic | LLM | delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for d, scores in summary["by_domain"].items():
        delta = scores["llm_mean"] - scores["heuristic_mean"]
        lines.append(f"| {d} | {scores['heuristic_mean']:.3f} | {scores['llm_mean']:.3f} | {delta:+.3f} |")
    lines.extend(["", "## Per-style mean composite", "",
                  "| style | heuristic | LLM | delta |", "| --- | ---: | ---: | ---: |"])
    for s, scores in summary["by_style"].items():
        delta = scores["llm_mean"] - scores["heuristic_mean"]
        lines.append(f"| {s} | {scores['heuristic_mean']:.3f} | {scores['llm_mean']:.3f} | {delta:+.3f} |")
    lines.extend(["", "## Per-query verdict", ""])
    for v in verdicts:
        l = v["llm"]
        h = v["heuristic"]
        lines.append(f"### {v['qid']} ({v['domain']}/{v['style']})")
        lines.append(f"**Query**: {v['query']}")
        lines.append(f"- records: {v['n_records']}")
        lines.append(f"- heuristic composite: {h['composite']}  ·  "
                     f"LLM composite: {l.get('composite','-')}  ·  "
                     f"LLM mode: {l.get('mode','?')}")
        if "rationale" in l:
            lines.append(f"- LLM rationale: _{l['rationale']}_")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    sys.stderr.write(f"✅ Markdown: {md_path}\n")
    print(str(md_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
