"""Retrieval plane — domain-aware federated search.

Public surface:

    Cascade         the dispatcher
    CascadeResult   what cascade.retrieve returns
    RetrievalRecord one snippet
    RetrievalSource Protocol every connector implements
    RetrievalError  raised by connectors on network / parse / auth failure

Individual connector modules (``jina_reader``, ``openalex``,
``semantic_scholar``, ``arxiv_api``, ``wikipedia``, ``gdelt``) live next
to this file.  They are imported lazily by ``builtin_sources()`` so the
``Cascade`` itself stays import-light.
"""

from __future__ import annotations

from .base import (
    RetrievalError,
    RetrievalRecord,
    RetrievalSource,
    normalize_records,
)
from .cache import TTLCache
from .cascade import Cascade, CascadeResult, DEFAULT_DOMAIN_CASCADES
from .citations import RenderResult, render_to_structured_citations, render_with_citations
from .evidence import EvidenceArtifact, EvidenceStore
from .graders import HeuristicGrader, LLMJudgeGrader
from .planner import RetrievalPlan, plan


def builtin_sources() -> dict[str, RetrievalSource]:
    """Return every registered source ready to plug into a :class:`Cascade`.

    Sources that need an env var / paid key / pinned fork instantiate
    unconditionally — the *probe* surface (``check()``) reports their
    tier and readiness, and the cascade fail-soft-skips at runtime when
    auth is missing.  This keeps the registry deterministic across
    machines and lets ``omni-hub retrieve-doctor`` give a complete view.

    Importing lazily keeps the package light when callers only need
    the cascade primitives.
    """

    from .archive import InternetArchiveSource, WaybackCDXSource
    from .arxiv_api import ArxivSource
    from .bilibili import BilibiliSource
    from .biomedical import EuropePMCSource, PubMedSource
    from .bluesky import BlueskySource
    from .business_intel import (
        CrunchbaseSource, LinkedInBrokerSource, OpenCorporatesSource,
    )
    from .cn_finance import TushareSource
    from .cn_policy import (
        CourtGovCnSource,
        GovCnSource,
        PBCGovCnSource,
        StatsGovCnSource,
    )
    from .crossref import CrossrefSource
    from .datacommons import DataCommonsSource
    from .exa import ExaSearchSource
    from .finance import EdgarSource, FREDSource
    from .gdelt import GDELTSource
    from .hackernews import HackerNewsSource
    from .hf_daily_papers import HFDailyPapersSource
    from .intl import ACLEDSource, IMFSource, WorldBankSource
    from .jina_reader import JinaReaderFetcher
    from .legal import CourtListenerSource
    from .mastodon import MastodonSource
    from .openalex import OpenAlexSource
    from .photo import PexelsSource, UnsplashSource
    from .pixabay import PixabaySource
    from .reddit import RedditSource
    from .rss import RSSSource
    from .trafilatura_source import TrafilaturaSource
    from .truth_social import TruthSocialSource
    from .ucdp import UCDPSource
    from .youtube_transcript import YouTubeTranscriptSource
    from .semantic_scholar import SemanticScholarSource
    from .twitterapi_io import TwitterApiIoSource
    from .us_gov import CongressGovSource, FederalRegisterSource, RegulationsGovSource
    from .web_search import BraveSearchSource, TavilySearchSource
    from .wechat_mp import WeChatMPSource
    from .wikidata import WikidataSource, WikidataSPARQLSource
    from .wikipedia import WikipediaSource
    from .xhs import XiaohongshuSource
    from .zhihu_weibo import WeiboSource, ZhihuSource

    return {
        s.name: s
        for s in (
            # tier 0 — no auth
            ArxivSource(),
            BilibiliSource(),
            BlueskySource(),                          # social_en, no auth
            CourtListenerSource(),
            RSSSource(),                              # generic RSS/Atom, no auth
            TrafilaturaSource(),                      # URL → cleaned article, local
            UCDPSource(),                             # conflict events, CC BY 4.0
            YouTubeTranscriptSource(),                # long-form audio/video transcripts
            CrossrefSource(),
            EuropePMCSource(),
            FederalRegisterSource(),
            GDELTSource(),
            HackerNewsSource(),                       # social_en / engineering, no auth
            HFDailyPapersSource(),
            InternetArchiveSource(),
            IMFSource(),
            JinaReaderFetcher(),
            MastodonSource(),                         # Fediverse public-search
            OpenAlexSource(),
            PubMedSource(),
            SemanticScholarSource(),
            WaybackCDXSource(),
            WikidataSource(),
            WikidataSPARQLSource(),
            WikipediaSource(),
            WorldBankSource(),
            EdgarSource(),
            # tier 1 — free key / self-hosted broker
            ACLEDSource(),
            BraveSearchSource(),
            CongressGovSource(),
            ExaSearchSource(),                        # neural/semantic search
            OpenCorporatesSource(),                   # global company registry, no auth
            PixabaySource(),                          # photography (5000/h free)
            RedditSource(),                           # social_en (OAuth credentials)
            TavilySearchSource(),                     # AI-Agent web search
            TruthSocialSource(),                      # via RSSHub
            CourtGovCnSource(),                       # v0.21 (RSSHub)
            DataCommonsSource(),
            FREDSource(),
            TushareSource(),                          # v0.22 (free token)
            GovCnSource(),                            # v0.21 (RSSHub)
            PBCGovCnSource(),                         # v0.21 (RSSHub)
            PexelsSource(),
            RegulationsGovSource(),
            StatsGovCnSource(),                       # v0.21 (RSSHub)
            UnsplashSource(),
            # tier 2 — paid key / pinned fork / broker
            CrunchbaseSource(),                       # v0.22 (paid key)
            LinkedInBrokerSource(),                   # v0.22 (broker)
            TwitterApiIoSource(),
            WeChatMPSource(),
            XiaohongshuSource(),
            ZhihuSource(),                            # v0.20
            WeiboSource(),                            # v0.20
        )
    }


__all__ = [
    "Cascade",
    "CascadeResult",
    "DEFAULT_DOMAIN_CASCADES",
    "EvidenceArtifact",
    "EvidenceStore",
    "HeuristicGrader",
    "LLMJudgeGrader",
    "RenderResult",
    "RetrievalError",
    "RetrievalPlan",
    "RetrievalRecord",
    "RetrievalSource",
    "TTLCache",
    "builtin_sources",
    "normalize_records",
    "plan",
    "render_to_structured_citations",
    "render_with_citations",
]
