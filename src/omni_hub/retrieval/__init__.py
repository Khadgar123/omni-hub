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
    """Return all free, no-API-key-required sources ready to plug in.

    Importing lazily keeps the package light when callers only need
    the cascade primitives.
    """

    from .arxiv_api import ArxivSource
    from .gdelt import GDELTSource
    from .jina_reader import JinaReaderFetcher
    from .openalex import OpenAlexSource
    from .semantic_scholar import SemanticScholarSource
    from .wikipedia import WikipediaSource

    return {
        s.name: s
        for s in (
            ArxivSource(),
            GDELTSource(),
            JinaReaderFetcher(),
            OpenAlexSource(),
            SemanticScholarSource(),
            WikipediaSource(),
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
