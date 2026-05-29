#!/usr/bin/env python3
"""source-policy-grade entrypoint — score every source on 5 axes.

Reads source_policy.py + each connector's check() + live retrieve-doctor
status to produce a fitness-for-purpose grade table.  No network beyond
the optional --probe flag (which runs retrieve-doctor health checks).

Usage::

    python3 scripts/source_policy_grade.py              # static grade
    python3 scripts/source_policy_grade.py --probe      # + live health
    python3 scripts/source_policy_grade.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# Authority / freshness / legal-risk are editorial judgments encoded here
# (the audit asked for these axes).  cost_tier + batchable come from
# source_policy.PolicyEntry.  Scale: authority/freshness 0-10.
_GRADE: dict[str, dict] = {
    # source: (authority, freshness, legal_risk, batchable, note)
    "openalex":        {"authority": 9, "freshness": 8, "legal_risk": "low",  "batchable": True,  "note": "Crossref-synced, 250M works"},
    "arxiv":           {"authority": 8, "freshness": 9, "legal_risk": "low",  "batchable": True,  "note": "preprints, official API"},
    "semantic_scholar":{"authority": 8, "freshness": 5, "legal_risk": "low",  "batchable": True,  "note": "TLDR + embedding, monthly refresh"},
    "crossref":        {"authority": 9, "freshness": 7, "legal_risk": "low",  "batchable": True,  "note": "DOI source-of-truth"},
    "europe_pmc":      {"authority": 9, "freshness": 7, "legal_risk": "low",  "batchable": True,  "note": "biomedical full-text"},
    "pubmed":          {"authority": 10,"freshness": 7, "legal_risk": "low",  "batchable": True,  "note": "NCBI gold standard"},
    "edgar":           {"authority": 10,"freshness": 8, "legal_risk": "low",  "batchable": True,  "note": "SEC primary filings"},
    "fred":            {"authority": 10,"freshness": 8, "legal_risk": "low",  "batchable": True,  "note": "Fed macro series"},
    "federal_register":{"authority": 10,"freshness": 9, "legal_risk": "low",  "batchable": True,  "note": "US reg primary"},
    "courtlistener":   {"authority": 9, "freshness": 8, "legal_risk": "low",  "batchable": True,  "note": "court opinions"},
    "congress_gov":    {"authority": 10,"freshness": 9, "legal_risk": "low",  "batchable": True,  "note": "official Congress API"},
    "regulations_gov": {"authority": 10,"freshness": 9, "legal_risk": "low",  "batchable": True,  "note": "federal dockets"},
    "gdelt":           {"authority": 5, "freshness": 10,"legal_risk": "low",  "batchable": True,  "note": "real-time, noisy"},
    "ucdp":            {"authority": 10,"freshness": 3, "legal_risk": "low",  "batchable": True,  "note": "academic, 6mo lag"},
    "world_bank":      {"authority": 10,"freshness": 5, "legal_risk": "low",  "batchable": True,  "note": "official stats"},
    "imf":             {"authority": 10,"freshness": 5, "legal_risk": "low",  "batchable": True,  "note": "official stats"},
    "wikipedia":       {"authority": 6, "freshness": 6, "legal_risk": "low",  "batchable": True,  "note": "tertiary, CC-BY-SA"},
    "wikidata":        {"authority": 6, "freshness": 6, "legal_risk": "low",  "batchable": True,  "note": "structured entities"},
    "hf_daily_papers": {"authority": 7, "freshness": 10,"legal_risk": "low",  "batchable": False, "note": "community-curated AI"},
    "hackernews":      {"authority": 5, "freshness": 9, "legal_risk": "low",  "batchable": True,  "note": "YC/dev signal"},
    "bluesky":         {"authority": 4, "freshness": 10,"legal_risk": "low",  "batchable": True,  "note": "AT Protocol public"},
    "mastodon":        {"authority": 4, "freshness": 10,"legal_risk": "low",  "batchable": True,  "note": "Fediverse public"},
    "reddit":          {"authority": 4, "freshness": 9, "legal_risk": "med",  "batchable": True,  "note": "OAuth, TOS limits"},
    "truth_social":    {"authority": 4, "freshness": 9, "legal_risk": "med",  "batchable": False, "note": "via RSSHub"},
    "tavily":          {"authority": 6, "freshness": 9, "legal_risk": "low",  "batchable": False, "note": "AI-search, cleaned"},
    "exa":             {"authority": 6, "freshness": 9, "legal_risk": "low",  "batchable": False, "note": "neural/semantic"},
    "brave_search":    {"authority": 6, "freshness": 9, "legal_risk": "low",  "batchable": False, "note": "independent index"},
    "trafilatura":     {"authority": 5, "freshness": 5, "legal_risk": "med",  "batchable": False, "note": "any-URL extract"},
    "youtube_transcript":{"authority": 5,"freshness": 7,"legal_risk": "med", "batchable": False, "note": "captions, IP-blocked sometimes"},
    "rss":             {"authority": 6, "freshness": 9, "legal_risk": "low",  "batchable": False, "note": "first-party feeds"},
    "jina_reader":     {"authority": 5, "freshness": 6, "legal_risk": "med",  "batchable": False, "note": "JS-render extract"},
    "internet_archive":{"authority": 7, "freshness": 4, "legal_risk": "low",  "batchable": True,  "note": "archived snapshots"},
    "opencorporates":  {"authority": 8, "freshness": 5, "legal_risk": "low",  "batchable": True,  "note": "global registry"},
    "pixabay":         {"authority": 5, "freshness": 5, "legal_risk": "low",  "batchable": True,  "note": "CC images"},
    "crunchbase":      {"authority": 8, "freshness": 7, "legal_risk": "high", "batchable": False, "note": "paid, TOS strict"},
    "tushare":         {"authority": 8, "freshness": 8, "legal_risk": "low",  "batchable": True,  "note": "A-share data"},
}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--probe", action="store_true",
                   help="Also run live connector health (retrieve-doctor)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    from omni_hub.retrieval.source_policy import POLICIES, resolve_policy

    # Map source → its policy tier (lowest tier across policies it appears in)
    source_tier: dict[str, int] = {}
    for pol in POLICIES.values():
        for entry in pol.primary:
            cur = source_tier.get(entry.source)
            source_tier[entry.source] = entry.tier if cur is None else min(cur, entry.tier)

    live_status: dict[str, str] = {}
    if args.probe:
        from omni_hub.retrieval import builtin_sources
        for name, src in builtin_sources().items():
            try:
                status, _ = src.check()
            except Exception:                                    # noqa: BLE001
                status = "error"
            live_status[name] = status

    rows = []
    for src, grade in sorted(_GRADE.items(),
                             key=lambda kv: (-kv[1]["authority"], -kv[1]["freshness"])):
        rows.append({
            "source": src,
            "authority": grade["authority"],
            "freshness": grade["freshness"],
            "cost_tier": source_tier.get(src, "?"),
            "batchable": grade["batchable"],
            "legal_risk": grade["legal_risk"],
            "live": live_status.get(src, "-"),
            "note": grade["note"],
        })

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    # Markdown table
    print("# Source Policy Grade\n")
    print("| source | auth | fresh | tier | batch | legal | live | note |")
    print("| --- | ---: | ---: | ---: | :-: | :-: | :-: | --- |")
    for r in rows:
        print(f"| {r['source']} | {r['authority']} | {r['freshness']} | "
              f"{r['cost_tier']} | {'Y' if r['batchable'] else 'n'} | "
              f"{r['legal_risk']} | {r['live']} | {r['note']} |")

    # Headline insights
    top_auth = [r["source"] for r in rows if r["authority"] >= 9 and r["cost_tier"] == 0][:8]
    freshest = sorted(rows, key=lambda r: -r["freshness"])[:5]
    avoid_batch = [r["source"] for r in rows
                   if r["legal_risk"] == "high" or (not r["batchable"] and r["legal_risk"] == "med")]
    print("\n## Insights\n")
    print(f"- **Highest-authority free (tier 0)**: {', '.join(top_auth)}")
    print(f"- **Freshest**: {', '.join(r['source'] for r in freshest)}")
    print(f"- **Avoid for batch ingest** (legal/non-batchable): {', '.join(avoid_batch) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
