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
    from .business_intel import CrunchbaseSource, LinkedInBrokerSource
    from .cn_finance import TushareSource
    from .cn_policy import (
        CourtGovCnSource,
        GovCnSource,
        PBCGovCnSource,
        StatsGovCnSource,
    )
    from .crossref import CrossrefSource
    from .datacommons import DataCommonsSource
    from .finance import EdgarSource, FREDSource
    from .gdelt import GDELTSource
    from .hf_daily_papers import HFDailyPapersSource
    from .intl import ACLEDSource, IMFSource, WorldBankSource
    from .jina_reader import JinaReaderFetcher
    from .legal import CourtListenerSource
    from .openalex import OpenAlexSource
    from .photo import PexelsSource, UnsplashSource
    from .semantic_scholar import SemanticScholarSource
    from .twitterapi_io import TwitterApiIoSource
    from .us_gov import CongressGovSource, FederalRegisterSource, RegulationsGovSource
    from .web_search import BraveSearchSource
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
            CourtListenerSource(),
            CrossrefSource(),
            EuropePMCSource(),
            FederalRegisterSource(),
            GDELTSource(),
            HFDailyPapersSource(),
            InternetArchiveSource(),
            IMFSource(),
            JinaReaderFetcher(),
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
