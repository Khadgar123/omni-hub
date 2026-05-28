#!/usr/bin/env python3
"""[DEPRECATED — use scripts/follow_entity.py + config/entity-watchlist.yaml]

Aggregate the latest signals on a company across multiple sources.

Company analog of ``follow_person.py``: per-org profile points to the
company's blog RSS, SEC ticker, HN search term, Tavily / GDELT query,
optionally OpenCorporates registration data.

Usage::

    python3 scripts/follow_company.py anthropic
    python3 scripts/follow_company.py openai --sources rss,hn,tavily --limit 5
    python3 scripts/follow_company.py nvidia --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


COMPANIES: dict[str, dict[str, Any]] = {
    # ── AI labs ─────────────────────────────────────────────────
    "anthropic": {
        "display": "Anthropic",
        # Anthropic does NOT publish an RSS feed (all paths 404 as of 2026-05).
        # Rely on HN + Tavily + GDELT for latest news.
        "rss": [],
        "hn_query": "Anthropic",
        "tavily_query": "Anthropic latest announcement",
        "gdelt_query": "Anthropic AI",
        "openalex_query": "Anthropic",
    },
    "openai": {
        "display": "OpenAI",
        "rss": ["https://openai.com/blog/rss.xml"],
        "hn_query": "OpenAI",
        "tavily_query": "OpenAI latest announcement",
        "gdelt_query": "OpenAI",
        "openalex_query": "OpenAI",
    },
    "deepmind": {
        "display": "Google DeepMind",
        "rss": ["https://deepmind.google/blog/rss.xml"],
        "hn_query": "DeepMind",
        "tavily_query": "DeepMind latest research",
        "gdelt_query": "DeepMind",
        "openalex_query": "DeepMind",
    },
    "xai": {
        "display": "xAI",
        "rss": [],
        "hn_query": "xAI Grok",
        "tavily_query": "xAI Grok latest",
        "gdelt_query": "xAI Grok",
    },
    "mistral": {
        "display": "Mistral AI",
        "rss": [],
        "hn_query": "Mistral AI",
        "tavily_query": "Mistral AI latest",
        "gdelt_query": "Mistral AI",
        "openalex_query": "Mistral AI",
    },
    "deepseek": {
        "display": "DeepSeek",
        "rss": [],
        "hn_query": "DeepSeek",
        "tavily_query": "DeepSeek latest model",
        "gdelt_query": "DeepSeek AI",
        "openalex_query": "DeepSeek",
    },

    # ── Big tech (public) ───────────────────────────────────────
    "google": {
        "display": "Google / Alphabet",
        "rss": ["https://blog.google/rss/"],
        "hn_query": "Google",
        "tavily_query": "Google latest news",
        "gdelt_query": "Alphabet Google",
        "edgar_tickers": ["GOOGL"],
    },
    "microsoft": {
        "display": "Microsoft",
        "rss": ["https://blogs.microsoft.com/feed/"],
        "hn_query": "Microsoft",
        "tavily_query": "Microsoft latest news",
        "gdelt_query": "Microsoft",
        "edgar_tickers": ["MSFT"],
    },
    "meta": {
        "display": "Meta",
        "rss": ["https://about.fb.com/news/feed/"],
        "hn_query": "Meta Facebook",
        "tavily_query": "Meta AI latest",
        "gdelt_query": "Meta Facebook",
        "edgar_tickers": ["META"],
    },
    "apple": {
        "display": "Apple",
        "rss": ["https://www.apple.com/newsroom/rss-feed.rss"],
        "hn_query": "Apple",
        "tavily_query": "Apple latest announcement",
        "gdelt_query": "Apple",
        "edgar_tickers": ["AAPL"],
    },
    "nvidia": {
        "display": "NVIDIA",
        "rss": ["https://blogs.nvidia.com/feed/"],
        "hn_query": "NVIDIA",
        "tavily_query": "NVIDIA latest GPU AI",
        "gdelt_query": "NVIDIA",
        "edgar_tickers": ["NVDA"],
    },
    "tesla": {
        "display": "Tesla",
        "rss": [],
        "hn_query": "Tesla",
        "tavily_query": "Tesla latest news",
        "gdelt_query": "Tesla",
        "edgar_tickers": ["TSLA"],
    },
    "amazon": {
        "display": "Amazon",
        "rss": ["https://www.aboutamazon.com/news/rss"],
        "hn_query": "Amazon",
        "tavily_query": "Amazon latest news",
        "gdelt_query": "Amazon",
        "edgar_tickers": ["AMZN"],
    },

    # ── China ───────────────────────────────────────────────────
    "bytedance": {
        "display": "ByteDance",
        "rss": [],
        "hn_query": "ByteDance TikTok",
        "tavily_query": "ByteDance ByteDance Doubao Seedance latest",
        "gdelt_query": "ByteDance TikTok",
    },
    "moonshot": {
        "display": "Moonshot / Kimi",
        "rss": [],
        "hn_query": "Moonshot Kimi",
        "tavily_query": "Moonshot Kimi latest",
        "gdelt_query": "Kimi Moonshot",
    },
}


def _sys_path() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "src"))


def _safe(label: str, fn, *args, **kwargs):                       # noqa: ANN001
    try:
        return fn(*args, **kwargs) or []
    except Exception as exc:                                      # noqa: BLE001
        sys.stderr.write(f"  ⚠ {label}: {type(exc).__name__}: {str(exc)[:120]}\n")
        return []


def gather(key: str, sources: list[str], limit: int) -> list[dict]:
    _sys_path()
    profile = COMPANIES.get(key)
    if not profile:
        sys.stderr.write(
            f"unknown company: {key!r}; available: {', '.join(COMPANIES)}\n",
        )
        sys.exit(2)

    sys.stderr.write(f"# tracking: {profile['display']}\n")
    records: list[dict] = []

    if "rss" in sources:
        from omni_hub.retrieval.rss import RSSSource
        rss = RSSSource()
        for feed_url in profile.get("rss") or []:
            sys.stderr.write(f"  → rss: {feed_url}\n")
            recs = _safe("rss", rss.retrieve, feed_url, limit=limit)
            for r in recs:
                records.append({**r.to_dict(), "_via": "rss"})

    if "hn" in sources and profile.get("hn_query"):
        from omni_hub.retrieval.hackernews import HackerNewsSource
        sys.stderr.write(f"  → hackernews: {profile['hn_query']}\n")
        recs = _safe(
            "hackernews", HackerNewsSource().retrieve,
            profile["hn_query"], limit=limit,
        )
        for r in recs:
            records.append({**r.to_dict(), "_via": "hackernews"})

    if "gdelt" in sources and profile.get("gdelt_query"):
        from omni_hub.retrieval.gdelt import GDELTSource
        sys.stderr.write(f"  → gdelt: {profile['gdelt_query']}\n")
        recs = _safe(
            "gdelt", GDELTSource().retrieve,
            profile["gdelt_query"], limit=limit,
        )
        for r in recs:
            records.append({**r.to_dict(), "_via": "gdelt"})

    if "tavily" in sources and profile.get("tavily_query"):
        from omni_hub.retrieval.web_search import TavilySearchSource
        sys.stderr.write(f"  → tavily: {profile['tavily_query']}\n")
        recs = _safe(
            "tavily", TavilySearchSource().retrieve,
            profile["tavily_query"], limit=limit, domain="default",
        )
        for r in recs:
            records.append({**r.to_dict(), "_via": "tavily"})

    if "openalex" in sources and profile.get("openalex_query"):
        from omni_hub.retrieval.openalex import OpenAlexSource
        sys.stderr.write(f"  → openalex: {profile['openalex_query']}\n")
        recs = _safe(
            "openalex", OpenAlexSource().retrieve,
            profile["openalex_query"], limit=limit, domain="research",
        )
        for r in recs:
            records.append({**r.to_dict(), "_via": "openalex"})

    if "edgar" in sources and profile.get("edgar_tickers"):
        from omni_hub.retrieval.finance import EdgarSource
        for ticker in profile["edgar_tickers"]:
            sys.stderr.write(f"  → edgar: {ticker}\n")
            recs = _safe(
                "edgar", EdgarSource().retrieve,
                ticker, limit=limit, domain="finance",
            )
            for r in recs:
                records.append({**r.to_dict(), "_via": f"edgar:{ticker}"})

    return records


_ALL_SOURCES = ("rss", "hn", "gdelt", "tavily", "openalex", "edgar")


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("company", help=f"one of: {', '.join(COMPANIES)}")
    p.add_argument(
        "--sources",
        default=",".join(_ALL_SOURCES),
        help=f"comma-separated subset; default ALL ({','.join(_ALL_SOURCES)})",
    )
    p.add_argument("--limit", type=int, default=3)
    p.add_argument("--json", action="store_true",
                   help="emit raw JSON instead of markdown")
    args = p.parse_args()

    selected = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = set(selected) - set(_ALL_SOURCES)
    if unknown:
        sys.stderr.write(f"unknown sources: {unknown}\n")
        return 2

    records = gather(args.company, selected, args.limit)
    sys.stderr.write(f"\n# total: {len(records)} records\n\n")

    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return 0

    profile = COMPANIES[args.company]
    print(f"# Following: {profile['display']}\n")
    by_via: dict[str, list[dict]] = {}
    for r in records:
        by_via.setdefault(r["_via"], []).append(r)
    for via in sorted(by_via):
        recs = by_via[via]
        print(f"## via {via} ({len(recs)})\n")
        for r in recs[:args.limit]:
            title = (r.get("title") or "").strip()
            url = (r.get("url") or "").strip()
            snippet = (r.get("snippet") or "").strip()
            published = (r.get("metadata") or {}).get("published", "")
            line = f"- [{title}]({url})" if url else f"- {title}"
            if published:
                line += f"  _({published[:16]})_"
            print(line)
            if snippet:
                print(f"  > {snippet[:200]}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
