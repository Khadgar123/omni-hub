#!/usr/bin/env python3
"""Before/after: raw-concat vs synthesized answer, both LLM-judged.

For each query: cascade → (A) raw-concat candidate, (B) synthesized
candidate.  Judge BOTH with the DeepSeek LLMJudge.  Report the delta —
this is the v0.45 quality proof.
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date
from pathlib import Path


PLAN = [
    ("ai_progress", "Claude 4.6 features release notes",            "ai-f1"),
    ("ai_progress", "DeepSeek V4 vs Claude 4 reasoning depth",      "ai-a1"),
    ("research",    "constitutional AI vs RLHF tradeoffs",          "res-a1"),
    ("finance",     "Federal Reserve 2026 interest rate decision",  "fin-f1"),
    ("us_policy",   "Federal Register AI rules last 90 days",       "uspol-t1"),
    ("international_relations", "BRICS expansion 2026 members",      "ir-f1"),
    ("enterprise",  "Anthropic Bun acquisition details",            "ent-f1"),
    ("biomedical",  "AlphaFold 3 protein structure prediction",     "bio-f1"),
]

RUBRIC = {
    "evidence_coverage": 1.0,
    "information_density": 1.0,
    "citation_support": 1.2,
    "style_fit": 0.6,
    "uncertainty_calibration": 0.8,
}


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    from omni_hub.retrieval import Cascade, builtin_sources
    from omni_hub.retrieval.cascade import DEFAULT_DOMAIN_CASCADES
    from omni_hub.retrieval.synthesize import synthesize_answer
    from omni_hub.judge import JudgeRequest
    from omni_hub.judge.llm import LLMJudge

    cascade = Cascade(builtin_sources(), cascades=DEFAULT_DOMAIN_CASCADES)
    llm = LLMJudge(ccload_base="")               # DeepSeek direct

    rows = []
    raw_scores, syn_scores = [], []

    for domain, query, qid in PLAN:
        sys.stderr.write(f"\n=== {qid} ({domain}) ===\n")
        result = cascade.retrieve(query, domain=domain,
                                  per_source_limit=5, total_limit=8, fusion="rrf")
        records = result.records or []
        if not records:
            sys.stderr.write("  no records\n")
            continue

        # (A) raw-concat candidate
        raw = "\n\n".join(
            f"[{i}] ({r.source}) {(r.snippet or r.title or '')[:300]}"
            for i, r in enumerate(records[:5], 1)
        )
        # (B) synthesized candidate
        syn = synthesize_answer(query, records, domain=domain, max_records=8)

        ref = f"Query: {query}\nDomain: {domain}"
        v_raw = llm.evaluate(JudgeRequest(domain=domain, candidate=raw,
                                          reference=ref, rubric=RUBRIC, trace_id=f"{qid}-raw"))
        v_syn = llm.evaluate(JudgeRequest(domain=domain, candidate=syn.answer,
                                          reference=ref, rubric=RUBRIC, trace_id=f"{qid}-syn"))
        raw_scores.append(v_raw.composite)
        syn_scores.append(v_syn.composite)
        delta = v_syn.composite - v_raw.composite
        sys.stderr.write(
            f"  raw={v_raw.composite:.3f}  syn={v_syn.composite:.3f}  "
            f"delta={delta:+.3f}  syn_mode={syn.mode}\n"
        )
        rows.append({
            "qid": qid, "domain": domain, "query": query,
            "raw_composite": round(v_raw.composite, 3),
            "syn_composite": round(v_syn.composite, 3),
            "delta": round(delta, 3),
            "syn_mode": syn.mode,
            "syn_cited_n": syn.cited_n,
        })

    summary = {
        "n": len(rows),
        "raw_mean": round(statistics.mean(raw_scores), 3) if raw_scores else None,
        "syn_mean": round(statistics.mean(syn_scores), 3) if syn_scores else None,
        "mean_delta": round(statistics.mean([r["delta"] for r in rows]), 3) if rows else None,
        "improved": sum(1 for r in rows if r["delta"] > 0),
        "regressed": sum(1 for r in rows if r["delta"] < 0),
    }

    today = date.today().isoformat()
    md = repo_root / ".omni" / "reports" / f"synth-compare-{today}.md"
    lines = [
        f"# Synthesis before/after — {today}",
        "",
        f"_{summary['n']} queries · DeepSeek LLMJudge_",
        f"_raw-concat mean={summary['raw_mean']} → synthesized mean={summary['syn_mean']} "
        f"(Δ {summary['mean_delta']:+.3f})_",
        f"_improved: {summary['improved']}/{summary['n']}  ·  regressed: {summary['regressed']}_",
        "",
        "| qid | domain | raw | synth | Δ | mode |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for r in rows:
        lines.append(f"| {r['qid']} | {r['domain']} | {r['raw_composite']} | "
                     f"{r['syn_composite']} | {r['delta']:+.3f} | {r['syn_mode']} |")
    md.write_text("\n".join(lines), encoding="utf-8")
    sys.stderr.write(f"\n✅ {md}\n")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
