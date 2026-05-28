#!/usr/bin/env python3
"""Follow any entity (person / company / institution / topic) declaratively.

Reads ``config/entity-watchlist.yaml`` and aggregates the latest signals
for the requested entity across all configured sources (RSS / Bluesky /
HN / Reddit / GDELT / Tavily / OpenAlex / EDGAR / Truth Social / Mastodon).

This is the v0.42 replacement for the per-person ``follow_person.py``
profiles and per-company ``follow_company.py`` profiles — the audit
flagged maintaining N files of human-curated source lists as the wrong
abstraction.  Entities live in YAML; the script is data-driven.

Usage::

    python3 scripts/follow_entity.py karpathy
    python3 scripts/follow_entity.py musk --sources rss,edgar,hn,tavily
    python3 scripts/follow_entity.py anthropic --limit 3
    python3 scripts/follow_entity.py scotus --json
    python3 scripts/follow_entity.py --list                 # show all entities
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _need_yaml():                                                  # noqa: ANN202
    try:
        import yaml
        return yaml
    except ImportError:
        sys.stderr.write("pip install pyyaml\n")
        sys.exit(2)


def _load_watchlist(path: Path) -> dict:
    yaml = _need_yaml()
    with path.open(encoding="utf-8") as f:
        wl = yaml.safe_load(f)
    # Flatten people / companies / institutions / topics → single map.
    out: dict[str, dict] = {}
    for bucket in ("people", "companies", "institutions", "topics"):
        for k, v in (wl.get(bucket) or {}).items():
            v["_bucket"] = bucket
            out[k] = v
    return out


def _safe(label: str, fn, *args, **kwargs):                       # noqa: ANN001
    try:
        return fn(*args, **kwargs) or []
    except Exception as exc:                                      # noqa: BLE001
        sys.stderr.write(f"  ⚠ {label}: {type(exc).__name__}: {str(exc)[:120]}\n")
        return []


_SOURCE_DISPATCH = {
    # source_name → (config_key, retrieve_query_builder)
    "rss": ("rss", lambda url: url),                             # one feed URL → one query
    "bluesky": ("bluesky_handle", lambda h: f"from:{h}"),
    "hn": ("hn_query", lambda q: q),
    "reddit": ("reddit_query", lambda q: q),
    "gdelt": ("gdelt_query", lambda q: q),
    "openalex": ("openalex_query", lambda q: q),
    "hf": ("hf_query", lambda q: q),
    "tavily": ("tavily_query", lambda q: q),
    "edgar": ("edgar_tickers", lambda t: t),                     # list → multi
    "truth": ("truth_social_handle", lambda h: h),
    "mastodon": ("mastodon_tag", lambda t: f"#{t}"),
}


def _gather_one(
    entity: dict,
    sources_selected: list[str],
    limit: int,
) -> list[dict]:
    """Run all enabled sources for one entity → list of records."""

    records: list[dict] = []
    src_config = entity.get("sources", {}) or {}

    def _import(module: str, attr: str):
        m = __import__(f"omni_hub.retrieval.{module}", fromlist=[attr])
        return getattr(m, attr)

    # Dispatch by source name.  Each branch is fail-soft.
    if "rss" in sources_selected:
        feeds = src_config.get("rss") or []
        if feeds:
            RSS = _import("rss", "RSSSource")
            rss = RSS()
            for url in feeds:
                sys.stderr.write(f"  → rss: {url}\n")
                for r in _safe("rss", rss.retrieve, url, limit=limit):
                    records.append({**r.to_dict(), "_via": "rss"})

    if "bluesky" in sources_selected and src_config.get("bluesky_handle"):
        BSky = _import("bluesky", "BlueskySource")
        h = src_config["bluesky_handle"]
        sys.stderr.write(f"  → bluesky: from:{h}\n")
        for r in _safe("bluesky", BSky().retrieve, f"from:{h}", limit=limit, domain="social_en"):
            records.append({**r.to_dict(), "_via": "bluesky"})

    if "hn" in sources_selected and src_config.get("hn_query"):
        HN = _import("hackernews", "HackerNewsSource")
        q = src_config["hn_query"]
        sys.stderr.write(f"  → hn: {q}\n")
        for r in _safe("hn", HN().retrieve, q, limit=limit):
            records.append({**r.to_dict(), "_via": "hackernews"})

    if "reddit" in sources_selected and src_config.get("reddit_query"):
        Rd = _import("reddit", "RedditSource")
        for r in _safe("reddit", Rd().retrieve, src_config["reddit_query"],
                       limit=limit, domain="social_en"):
            records.append({**r.to_dict(), "_via": "reddit"})

    if "gdelt" in sources_selected and src_config.get("gdelt_query"):
        Gd = _import("gdelt", "GDELTSource")
        for r in _safe("gdelt", Gd().retrieve, src_config["gdelt_query"],
                       limit=limit, domain="international_relations"):
            records.append({**r.to_dict(), "_via": "gdelt"})

    if "openalex" in sources_selected and src_config.get("openalex_query"):
        OA = _import("openalex", "OpenAlexSource")
        for r in _safe("openalex", OA().retrieve, src_config["openalex_query"],
                       limit=limit, domain="research"):
            records.append({**r.to_dict(), "_via": "openalex"})

    if "hf" in sources_selected and src_config.get("hf_query"):
        HF = _import("hf_daily_papers", "HFDailyPapersSource")
        for r in _safe("hf", HF().retrieve, src_config["hf_query"],
                       limit=limit, domain="ai_progress"):
            records.append({**r.to_dict(), "_via": "hf"})

    if "tavily" in sources_selected and src_config.get("tavily_query"):
        Tv = _import("web_search", "TavilySearchSource")
        for r in _safe("tavily", Tv().retrieve, src_config["tavily_query"],
                       limit=limit, domain="default"):
            records.append({**r.to_dict(), "_via": "tavily"})

    if "edgar" in sources_selected and src_config.get("edgar_tickers"):
        Ed = _import("finance", "EdgarSource")
        ed = Ed()
        for ticker in src_config["edgar_tickers"]:
            for r in _safe(f"edgar:{ticker}", ed.retrieve, ticker,
                           limit=limit, domain="finance"):
                records.append({**r.to_dict(), "_via": f"edgar:{ticker}"})

    if "truth" in sources_selected and src_config.get("truth_social_handle"):
        Ts = _import("truth_social", "TruthSocialSource")
        for r in _safe("truth_social", Ts().retrieve,
                       src_config["truth_social_handle"], limit=limit, domain="social_en"):
            records.append({**r.to_dict(), "_via": "truth_social"})

    if "mastodon" in sources_selected and src_config.get("mastodon_tag"):
        Ms = _import("mastodon", "MastodonSource")
        for r in _safe("mastodon", Ms().retrieve,
                       f"#{src_config['mastodon_tag']}", limit=limit, domain="social_en"):
            records.append({**r.to_dict(), "_via": "mastodon"})

    return records


_ALL_SOURCES = list(_SOURCE_DISPATCH.keys())


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("entity", nargs="?", default="",
                   help="entity_id (use --list to see all)")
    p.add_argument("--watchlist",
                   default="config/entity-watchlist.yaml")
    p.add_argument("--sources",
                   default=",".join(_ALL_SOURCES),
                   help=f"comma-separated; default ALL ({','.join(_ALL_SOURCES)})")
    p.add_argument("--limit", type=int, default=3)
    p.add_argument("--list", action="store_true",
                   help="list all entities and exit")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    wl_path = repo_root / args.watchlist
    if not wl_path.exists():
        sys.stderr.write(f"watchlist not found: {wl_path}\n")
        return 2
    entities = _load_watchlist(wl_path)

    if args.list or not args.entity:
        # Group by bucket for readability
        by_bucket: dict[str, list[tuple[str, dict]]] = {}
        for k, v in entities.items():
            by_bucket.setdefault(v.get("_bucket", "other"), []).append((k, v))
        for bucket in sorted(by_bucket):
            print(f"\n# {bucket}")
            for k, v in sorted(by_bucket[bucket]):
                print(f"  - {k:18s} {v.get('display', '')}")
        return 0

    entity = entities.get(args.entity)
    if not entity:
        sys.stderr.write(
            f"unknown entity: {args.entity!r}; available: {', '.join(sorted(entities))}\n"
        )
        return 2

    selected = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = set(selected) - set(_ALL_SOURCES)
    if unknown:
        sys.stderr.write(f"unknown sources: {unknown}\n")
        return 2

    sys.stderr.write(
        f"# tracking: {entity['display']} ({entity.get('_bucket','?')}/"
        f"{entity.get('primary_domain','?')})\n"
    )
    records = _gather_one(entity, selected, args.limit)
    sys.stderr.write(f"\n# total: {len(records)} records\n\n")

    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return 0

    print(f"# Following: {entity['display']}\n")
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
