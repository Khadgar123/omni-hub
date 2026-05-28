"""Judge LLM framework (v0.23).

A Judge consumes a candidate answer + reference context + rubric and
returns a :class:`JudgeVerdict` (score per dimension + composite +
rationale).  Judges are the *evaluation* half of the Skill Evolution
Layer (the *learning* half is :mod:`omni_hub.harness.dspy_compile`).

Two implementations ship:

* :class:`HeuristicJudge` — stdlib-only, scores using lexical features
  (citation density, length-fit, distinct-source count, vocabulary
  overlap with reference).  Deterministic, no LLM call, no API key.
  Default — always available.

* :class:`LLMJudge` — picks the first available channel in this order:
  local ccLoad gateway (``OMNI_CCLOAD_BASE``) → DeepSeek direct
  (``DEEPSEEK_API_KEY`` env or ``.omni/secrets.json::omni-hub/api/deepseek/default``)
  → Anthropic SDK (``ANTHROPIC_API_KEY`` + ``anthropic`` package).
  When none are available it degrades to the HeuristicJudge so the
  cascade never throws on missing config.

Use cases:

* ``omni-hub judge-evaluate --domain finance --candidate <text>`` —
  one-shot scoring against the domain rubric.
* ``harness-compile-skill --judge llm`` (v0.23+) — re-rank
  PreferenceStore exemplars by Judge composite, replacing the
  v0.16 lexical-only ranker.

The Protocol is intentionally narrow so v0.29 A/B-test wrapper +
v0.28 cross-skill knowledge-transfer can route to multiple Judges and
ensemble verdicts.
"""

from __future__ import annotations

from .base import (
    DimensionScore,
    Judge,
    JudgeRequest,
    JudgeVerdict,
    Judges,
    composite_score,
)
from .heuristic import HeuristicJudge
from .llm import LLMJudge

__all__ = [
    "DimensionScore",
    "HeuristicJudge",
    "Judge",
    "JudgeRequest",
    "JudgeVerdict",
    "Judges",
    "LLMJudge",
    "composite_score",
]
