#!/usr/bin/env python3
"""[DEPRECATED — use scripts/follow_entity.py + config/entity-watchlist.yaml]

Aggregate the latest signals on a public figure across multiple sources.

For each tracked person, this script fans out to their relevant feeds
(blog RSS, Bluesky, HF Daily Papers as author, GDELT mentions, GitHub,
EDGAR for the companies they run, etc.) and unifies the results into a
single time-sorted list.

Profiles live in ``PEOPLE`` below — add a key to extend.  Each entry
is a dict mapping source-name → arg (URL / handle / company list).

Usage::

    python3 scripts/follow_person.py karpathy
    python3 scripts/follow_person.py musk --sources gdelt,edgar --limit 5
    python3 scripts/follow_person.py altman --json

Each source call is fail-soft: a single source dying logs to stderr and
the script keeps going.  This is important because at any given moment
some endpoints are unreachable (Bluesky from China, Twitter without
paid key, etc.).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# People profiles — extend by adding entries.  Each value is a dict whose
# keys correspond to source identifiers below.  ``None`` = source not
# applicable.  Use lists when one source covers multiple things (e.g.
# multiple RSS feeds, multiple EDGAR tickers).
# ---------------------------------------------------------------------------

PEOPLE: dict[str, dict[str, Any]] = {
    # === AI 研究者 / 工程师 ===
    "karpathy": {
        "display": "Andrej Karpathy",
        "rss": ["https://karpathy.github.io/feed.xml"],
        "bluesky_handle": "karpathy.bsky.social",
        "reddit_query": "Karpathy",
        "hn_query": "karpathy",
        "gdelt_query": "Andrej Karpathy",
        "openalex_query": "Karpathy",
        "hf_query": "Karpathy",
    },
    "lilianweng": {
        "display": "Lilian Weng",
        "rss": ["https://lilianweng.github.io/index.xml"],
        "reddit_query": "Lilian Weng",
        "hn_query": "lilianweng",
        "openalex_query": "Lilian Weng",
    },
    "simonw": {
        "display": "Simon Willison",
        "rss": ["https://simonwillison.net/atom/everything/"],
        "bluesky_handle": "simonw.bsky.social",
        "reddit_query": "Simon Willison",
        "hn_query": "simonw",
    },
    "hinton": {
        "display": "Geoffrey Hinton",
        "rss": [],
        "reddit_query": "Geoffrey Hinton",
        "hn_query": "Hinton",
        "gdelt_query": "Geoffrey Hinton",
        "openalex_query": "Geoffrey Hinton",
    },
    "ylecun": {
        "display": "Yann LeCun",
        "rss": [],
        "bluesky_handle": "ylecun.bsky.social",
        "reddit_query": "Yann LeCun",
        "hn_query": "Yann LeCun",
        "gdelt_query": "Yann LeCun Meta",
        "openalex_query": "Yann LeCun",
    },
    "bengio": {
        "display": "Yoshua Bengio",
        "rss": [],
        "reddit_query": "Yoshua Bengio",
        "hn_query": "Bengio",
        "gdelt_query": "Yoshua Bengio",
        "openalex_query": "Yoshua Bengio",
    },
    "ilya": {
        "display": "Ilya Sutskever",
        "rss": [],
        "reddit_query": "Ilya Sutskever",
        "hn_query": "Ilya Sutskever",
        "gdelt_query": "Ilya Sutskever SSI",
        "openalex_query": "Ilya Sutskever",
    },
    "jeff_dean": {
        "display": "Jeff Dean",
        "rss": [],
        "reddit_query": "Jeff Dean Google",
        "hn_query": "Jeff Dean",
        "gdelt_query": "Jeff Dean Google",
        "openalex_query": "Jeff Dean",
    },

    # === AI 公司 CEO ===
    "altman": {
        "display": "Sam Altman",
        "rss": ["https://blog.samaltman.com/posts.atom"],
        "reddit_query": "Sam Altman",
        "hn_query": "Sam Altman OpenAI",
        "gdelt_query": "Sam Altman OpenAI",
        "openalex_query": "Altman OpenAI",
    },
    "dario": {
        "display": "Dario Amodei",
        # Anthropic has no RSS as of 2026-05; rely on HN + Tavily.
        "rss": [],
        "reddit_query": "Dario Amodei",
        "hn_query": "Dario Amodei Anthropic",
        "gdelt_query": "Dario Amodei Anthropic",
        "openalex_query": "Dario Amodei",
    },
    "pichai": {
        "display": "Sundar Pichai",
        "rss": [],
        "reddit_query": "Sundar Pichai",
        "hn_query": "Sundar Pichai",
        "gdelt_query": "Sundar Pichai Google",
        "edgar_tickers": ["GOOGL"],
    },
    "nadella": {
        "display": "Satya Nadella",
        "rss": [],
        "reddit_query": "Satya Nadella",
        "hn_query": "Satya Nadella",
        "gdelt_query": "Satya Nadella Microsoft",
        "edgar_tickers": ["MSFT"],
    },
    "zuck": {
        "display": "Mark Zuckerberg",
        "rss": [],
        "reddit_query": "Mark Zuckerberg",
        "hn_query": "Zuckerberg",
        "gdelt_query": "Mark Zuckerberg Meta",
        "edgar_tickers": ["META"],
    },
    "musk": {
        "display": "Elon Musk",
        "rss": [],
        "reddit_query": "Elon Musk",
        "hn_query": "Elon Musk",
        "gdelt_query": "Elon Musk",
        "edgar_tickers": ["TSLA"],                                # SpaceX/xAI private
    },
    "bezos": {
        "display": "Jeff Bezos",
        "rss": [],
        "reddit_query": "Jeff Bezos",
        "hn_query": "Jeff Bezos",
        "gdelt_query": "Jeff Bezos Amazon Blue Origin",
        "edgar_tickers": ["AMZN"],
    },
    "huang": {
        "display": "Jensen Huang",
        "rss": [],
        "reddit_query": "Jensen Huang NVIDIA",
        "hn_query": "Jensen Huang",
        "gdelt_query": "Jensen Huang NVIDIA",
        "edgar_tickers": ["NVDA"],
    },

    # === 媒体 / 访谈 / 政策 ===
    "lex": {
        "display": "Lex Fridman",
        "rss": [],
        "reddit_query": "Lex Fridman",
        "hn_query": "Lex Fridman",
        "gdelt_query": "Lex Fridman",
    },
    "dwarkesh": {
        "display": "Dwarkesh Patel",
        "rss": ["https://www.dwarkesh.com/feed"],
        "reddit_query": "Dwarkesh Patel",
        "hn_query": "Dwarkesh",
    },
    "trump": {
        "display": "Donald Trump",
        "rss": [],
        "truth_social_handle": "realDonaldTrump",                  # via RSSHub
        "reddit_query": "Donald Trump",
        "hn_query": "Trump",
        "gdelt_query": "Donald Trump",
    },
}


def _sys_path() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "src"))


def _safe(label: str, fn, *args, **kwargs):                       # noqa: ANN001
    """Run ``fn(*args, **kwargs)`` and swallow exceptions, logging to stderr.

    Returns ``[]`` on failure.  This is the cascade pattern — one source
    dying never breaks the aggregate.
    """

    try:
        return fn(*args, **kwargs) or []
    except Exception as exc:                                      # noqa: BLE001
        sys.stderr.write(f"  ⚠ {label}: {type(exc).__name__}: {str(exc)[:120]}\n")
        return []


def gather(person_key: str, sources: list[str], limit: int) -> list[dict]:
    _sys_path()
    profile = PEOPLE.get(person_key)
    if not profile:
        sys.stderr.write(
            f"unknown person: {person_key!r}; available: {', '.join(PEOPLE)}\n",
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

    if "bluesky" in sources and profile.get("bluesky_handle"):
        from omni_hub.retrieval.bluesky import BlueskySource
        handle = profile["bluesky_handle"]
        sys.stderr.write(f"  → bluesky: from:{handle}\n")
        recs = _safe(
            "bluesky", BlueskySource().retrieve,
            f"from:{handle}", limit=limit, domain="social_en",
        )
        for r in recs:
            records.append({**r.to_dict(), "_via": "bluesky"})

    if "reddit" in sources and profile.get("reddit_query"):
        from omni_hub.retrieval.reddit import RedditSource
        sys.stderr.write(f"  → reddit: {profile['reddit_query']}\n")
        recs = _safe(
            "reddit", RedditSource().retrieve,
            profile["reddit_query"], limit=limit, domain="social_en",
        )
        for r in recs:
            records.append({**r.to_dict(), "_via": "reddit"})

    if "truth" in sources and profile.get("truth_social_handle"):
        from omni_hub.retrieval.truth_social import TruthSocialSource
        sys.stderr.write(f"  → truth_social: @{profile['truth_social_handle']}\n")
        recs = _safe(
            "truth_social", TruthSocialSource().retrieve,
            profile["truth_social_handle"], limit=limit, domain="social_en",
        )
        for r in recs:
            records.append({**r.to_dict(), "_via": "truth_social"})

    if "hn" in sources and profile.get("hn_query"):
        from omni_hub.retrieval.hackernews import HackerNewsSource
        sys.stderr.write(f"  → hackernews: {profile['hn_query']}\n")
        recs = _safe(
            "hackernews", HackerNewsSource().retrieve,
            profile["hn_query"], limit=limit, domain="social_en",
        )
        for r in recs:
            records.append({**r.to_dict(), "_via": "hackernews"})

    if "gdelt" in sources and profile.get("gdelt_query"):
        from omni_hub.retrieval.gdelt import GDELTSource
        sys.stderr.write(f"  → gdelt: {profile['gdelt_query']}\n")
        recs = _safe(
            "gdelt", GDELTSource().retrieve,
            profile["gdelt_query"], limit=limit, domain="international_relations",
        )
        for r in recs:
            records.append({**r.to_dict(), "_via": "gdelt"})

    if "openalex" in sources and profile.get("openalex_query"):
        from omni_hub.retrieval.openalex import OpenAlexSource
        sys.stderr.write(f"  → openalex: {profile['openalex_query']}\n")
        recs = _safe(
            "openalex", OpenAlexSource().retrieve,
            profile["openalex_query"], limit=limit, domain="research",
        )
        for r in recs:
            records.append({**r.to_dict(), "_via": "openalex"})

    if "hf" in sources and profile.get("hf_query"):
        from omni_hub.retrieval.hf_daily_papers import HFDailyPapersSource
        sys.stderr.write(f"  → hf: {profile['hf_query']}\n")
        recs = _safe(
            "hf", HFDailyPapersSource().retrieve,
            profile["hf_query"], limit=limit, domain="ai_progress",
        )
        for r in recs:
            records.append({**r.to_dict(), "_via": "hf"})

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

    if "tavily" in sources:
        from omni_hub.retrieval.web_search import TavilySearchSource
        q = f"{profile['display']} latest news"
        sys.stderr.write(f"  → tavily: {q}\n")
        recs = _safe(
            "tavily", TavilySearchSource().retrieve,
            q, limit=limit, domain="default",
        )
        for r in recs:
            records.append({**r.to_dict(), "_via": "tavily"})

    return records


_ALL_SOURCES = (
    "rss", "bluesky", "reddit", "hn", "truth",
    "gdelt", "openalex", "hf", "edgar", "tavily",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("person", help=f"one of: {', '.join(PEOPLE)}")
    parser.add_argument(
        "--sources",
        default=",".join(_ALL_SOURCES),
        help=f"comma-separated subset; default ALL ({','.join(_ALL_SOURCES)})",
    )
    parser.add_argument("--limit", type=int, default=5,
                        help="per-source result cap (default 5)")
    parser.add_argument("--json", action="store_true",
                        help="emit raw JSON instead of human-readable markdown")
    args = parser.parse_args()

    selected = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = set(selected) - set(_ALL_SOURCES)
    if unknown:
        sys.stderr.write(f"unknown source(s): {unknown}; valid: {_ALL_SOURCES}\n")
        return 2

    records = gather(args.person, selected, args.limit)
    sys.stderr.write(f"\n# total: {len(records)} records from {len(selected)} sources\n\n")

    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return 0

    # Markdown summary
    profile = PEOPLE[args.person]
    print(f"# Following: {profile['display']}\n")
    by_source: dict[str, list[dict]] = {}
    for r in records:
        by_source.setdefault(r["_via"], []).append(r)
    for via in sorted(by_source):
        recs = by_source[via]
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
