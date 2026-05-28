"""Source Mesh — policy-driven source resolution.

Replaces "tool soup" with "source mesh".  Instead of each domain
hard-coding a flat list of sources, we group sources by **purpose**
(broad search, web extraction, social, conflict events, finance, etc.)
and let domains compose policies.

Three benefits over raw cascade lists:

1. **Tiered fallback**: tier 0 (free / self-host) → tier 1 (free quota /
   light paid) → tier 2 (paid).  Cascade tries higher tier first;
   skips tier 2 unless explicit ``--allow-paid`` / key present.

2. **Cross-domain reuse**: ``social_zh`` policy is shared by cooking,
   travel, marketing, fashion — they don't each duplicate
   ``xiaohongshu / weibo / zhihu / wechat_mp / bilibili`` lists.

3. **Capability-targeted**: a query like "Karpathy latest tweets" picks
   the *social_en* policy, not the *research* policy that just happens
   to also list HN.

This is the v0.42 architecture lift the audit flagged — "Source Mesh,
not Tool Soup".

Usage in code::

    from omni_hub.retrieval.source_policy import resolve_policy
    sources = resolve_policy("broad_search", allow_paid=False)
    # → ["searxng", "openserp", "brave_search", "tavily", "exa"]
    # (cascade then fan-outs whichever are actually reachable)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


SourceTier = Literal[0, 1, 2]


@dataclass(frozen=True)
class PolicyEntry:
    """One source within a policy bucket.

    ``tier`` 0 = free self-host / open API (no key);
            1 = free quota / personal key;
            2 = paid / broker / commercial.

    ``fail_soft`` = whether cascade should silently skip if the source
    is unreachable (default true for tier 0/1, false for tier 2 to
    surface key-config gaps).
    """

    source: str
    tier: SourceTier
    fail_soft: bool = True
    note: str = ""


@dataclass(frozen=True)
class Policy:
    """A purpose-grouped source list.

    ``primary`` = ordered list, cascade fans out all in parallel,
    fusion-ranks them.  Tier-2 entries are filtered out unless the
    caller passes ``allow_paid=True``.
    """

    name: str
    purpose: str
    primary: list[PolicyEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# v0.42 default policies — Source Mesh
# ---------------------------------------------------------------------------

POLICIES: dict[str, Policy] = {
    "broad_search": Policy(
        name="broad_search",
        purpose="generic web search; default for any factual / news lookup",
        primary=[
            # Tier 0 free defaults
            PolicyEntry("tavily", 1, note="AI-friendly cleaned content + LLM answer"),
            PolicyEntry("exa", 1, note="neural / semantic"),
            # Tier 1 free quotas
            PolicyEntry("brave_search", 1, note="independent Brave index, 2k/mo free"),
            # Tier 2 (skipped unless allow_paid)
            # SearXNG/OpenSERP self-host are future Tier 0 once deployed.
        ],
    ),

    "web_extract": Policy(
        name="web_extract",
        purpose="URL → cleaned content (long-form articles, blog posts)",
        primary=[
            PolicyEntry("trafilatura", 0, note="static HTML article extraction (best for blogs/news)"),
            PolicyEntry("jina_reader", 1, note="fallback when Trafilatura misses dynamic content"),
            # Future: crawl4ai (LLM-ready Markdown), scrapy (large-scale)
        ],
    ),

    "academic": Policy(
        name="academic",
        purpose="papers, citations, scholar metadata",
        primary=[
            PolicyEntry("openalex", 0, note="Crossref-synced daily, 250M works"),
            PolicyEntry("arxiv", 0, note="preprint PDFs"),
            PolicyEntry("semantic_scholar", 1, note="TLDR + SPECTER embedding + influentialCitations"),
            PolicyEntry("crossref", 0, note="DOI fallback"),
            PolicyEntry("europe_pmc", 0, note="biomedical full-text"),
        ],
    ),

    "social_en": Policy(
        name="social_en",
        purpose="English social media: trends, person mentions, communities",
        primary=[
            PolicyEntry("hackernews", 0, note="YC/AI/dev discussion, auto-indexes video & podcast links"),
            PolicyEntry("bluesky", 0, note="AT Protocol public search, no auth"),
            PolicyEntry("mastodon", 0, note="Fediverse hashtag timeline, no auth"),
            PolicyEntry("reddit", 1, note="OAuth credentials needed"),
            PolicyEntry("x_twitter", 2, fail_soft=False, note="TwitterAPI.io paid"),
        ],
    ),

    "social_zh": Policy(
        name="social_zh",
        purpose="Chinese social media: 小红书 / 微博 / 知乎 / 公众号 / B站",
        primary=[
            PolicyEntry("bilibili", 0, note="public API"),
            PolicyEntry("wechat_mp", 1, note="WeMPRss broker"),
            # Tier 2: broker stubs requiring local binary install
            PolicyEntry("xiaohongshu", 2, fail_soft=False, note="xhs CLI broker"),
            PolicyEntry("weibo", 2, fail_soft=False, note="weibo broker"),
            PolicyEntry("zhihu", 2, fail_soft=False, note="zhihu broker"),
        ],
    ),

    "long_form": Policy(
        name="long_form",
        purpose="blogs, interviews, podcasts, books (high-signal long content)",
        primary=[
            PolicyEntry("rss", 0, note="generic RSS/Atom feed reader"),
            PolicyEntry("trafilatura", 0, note="extract any URL's main article"),
            PolicyEntry("internet_archive", 0, note="archived versions"),
            # Future: youtube_transcript, openlibrary, googlebooks
        ],
    ),

    "conflict_events": Policy(
        name="conflict_events",
        purpose="armed conflict events, sanctions, geopolitical incidents",
        primary=[
            PolicyEntry("gdelt", 0, note="real-time global news events"),
            PolicyEntry("ucdp", 0, note="Uppsala conflict data, CC BY 4.0 academic-grade"),
            PolicyEntry("acled", 1, fail_soft=False, note="non-commercial free key"),
        ],
    ),

    "finance_us": Policy(
        name="finance_us",
        purpose="US filings / macro / market metadata",
        primary=[
            PolicyEntry("edgar", 0, note="SEC public filings"),
            PolicyEntry("fred", 1, note="St. Louis Fed macro time series"),
        ],
    ),

    "finance_cn": Policy(
        name="finance_cn",
        purpose="A-share quotes / Chinese macro / Caixin news",
        primary=[
            # AKShare native via scripts/akshare_query.py (not a cascade source)
            # — keep finance_cn policy minimal until we wrap AKShare as a connector
            PolicyEntry("tushare", 1, fail_soft=False, note="needs token"),
        ],
    ),

    "us_policy_sources": Policy(
        name="us_policy_sources",
        purpose="US federal regulation / Congress / SCOTUS",
        primary=[
            PolicyEntry("federal_register", 0),
            PolicyEntry("courtlistener", 0, note="authenticated 5000/h"),
            PolicyEntry("congress_gov", 1, fail_soft=False, note="DATA_GOV_API_KEY"),
            PolicyEntry("regulations_gov", 1, fail_soft=False, note="DATA_GOV_API_KEY"),
        ],
    ),

    "cn_policy_sources": Policy(
        name="cn_policy_sources",
        purpose="China ministerial filings / PBoC / NBS",
        primary=[
            PolicyEntry("gov_cn", 1, note="via RSSHub"),
            PolicyEntry("stats_gov_cn", 1, note="via RSSHub"),
            PolicyEntry("court_gov_cn", 1, note="via RSSHub"),
            PolicyEntry("pbc_gov_cn", 1, note="via RSSHub"),
        ],
    ),

    "stats_macro": Policy(
        name="stats_macro",
        purpose="global statistics / macro indicators",
        primary=[
            PolicyEntry("world_bank", 0),
            PolicyEntry("imf", 0),
            PolicyEntry("fred", 1, note="US"),
            PolicyEntry("data_commons", 1, fail_soft=False),
        ],
    ),

    "entity_concept": Policy(
        name="entity_concept",
        purpose="entity definitions / concept anchors",
        primary=[
            PolicyEntry("wikipedia", 0),
            PolicyEntry("wikidata", 0),
            PolicyEntry("internet_archive", 0),
        ],
    ),

    "image_media": Policy(
        name="image_media",
        purpose="photo / illustration / video search",
        primary=[
            PolicyEntry("pixabay", 1, note="5000/h free"),
            PolicyEntry("unsplash", 1, fail_soft=False, note="50/h, paid above"),
            PolicyEntry("pexels", 1, fail_soft=False),
        ],
    ),
}


def resolve_policy(
    policy_name: str,
    *,
    allow_paid: bool = False,
) -> list[str]:
    """Return the cascade-ready source list for a policy.

    Filters out tier-2 entries unless ``allow_paid=True``.  Source names
    align with ``omni_hub.retrieval.builtin_sources()`` keys.
    """

    policy = POLICIES.get(policy_name)
    if policy is None:
        return []
    return [
        e.source for e in policy.primary
        if allow_paid or e.tier < 2
    ]


def policies_for_domain(domain: str) -> list[str]:
    """Reverse map: which policies should compose a domain's cascade?

    Domains can use multiple policies (e.g. research = academic +
    long_form + entity_concept).  Cascade combines them while
    deduplicating sources.
    """

    return _DOMAIN_TO_POLICIES.get(domain, [])


_DOMAIN_TO_POLICIES: dict[str, list[str]] = {
    "research": ["academic", "long_form", "entity_concept"],
    "ai_progress": ["academic", "social_en", "long_form", "broad_search", "entity_concept"],
    "engineering": ["broad_search", "social_en", "academic", "entity_concept"],
    "biomedical": ["academic", "entity_concept"],
    "finance": ["finance_us", "broad_search", "entity_concept"],
    "us_policy": ["us_policy_sources", "broad_search", "entity_concept"],
    "cn_policy": ["cn_policy_sources", "broad_search", "entity_concept"],
    "law": ["us_policy_sources", "entity_concept"],
    "international_relations": ["conflict_events", "stats_macro", "broad_search", "entity_concept"],
    "statistics": ["stats_macro", "entity_concept"],
    "agent_systems": ["academic", "social_en", "broad_search", "entity_concept"],
    "social_en": ["social_en", "broad_search"],
    "social_zh": ["social_zh"],
    "marketing": ["social_zh", "broad_search", "entity_concept"],
    "enterprise": ["finance_us", "broad_search", "entity_concept"],
    "photography": ["image_media", "entity_concept"],
    "fashion": ["entity_concept"],
    "cooking": ["social_zh", "entity_concept"],
    "travel": ["social_zh", "entity_concept"],
    "fitness_wellness": ["academic", "entity_concept"],
    "chat_relationships": [],
    "meta": [],
    "default": ["broad_search", "entity_concept"],
}


def domain_sources_via_policy(domain: str, *, allow_paid: bool = False) -> list[str]:
    """Compose a domain's full source list by union'ing its policies.

    Preserves the first-seen order across policies (so the most
    relevant policy's head sources stay first), and de-dups.
    """

    seen: set[str] = set()
    out: list[str] = []
    for pname in policies_for_domain(domain):
        for source in resolve_policy(pname, allow_paid=allow_paid):
            if source not in seen:
                seen.add(source)
                out.append(source)
    return out


__all__ = [
    "Policy", "PolicyEntry", "POLICIES",
    "resolve_policy", "policies_for_domain", "domain_sources_via_policy",
]
