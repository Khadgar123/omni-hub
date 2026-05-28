"""Atomic-claim + citation enforcement.

Implements the *output-layer* grounding contract described in
``docs/agent-system-development-plan.md`` and ``docs/self-evolution-harness.md``:

    1. Split candidate text into atomic claims (one assertion per sentence).
    2. For each claim, locate citations like ``[1]``, ``[src:foo]`` or
       ``(Smith 2024)``.
    3. Compute:
        - ``citation_density`` = cited_claims / total_claims
        - ``nugget_density`` = informative_claims / total_claims
        - ``low_signal_spans`` = claims that match low-signal heuristics
    4. Emit a ``GroundingReport`` that downstream judges and DSPy compile can
       consume.

This module is stdlib-only.  Heavier ground-truth checking (does the claim
actually appear in the cited source?) is a Phase-2 add-on that belongs in
``promptfoo`` evaluators; here we provide structural verification only.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Iterable


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------

# Sentence split: keep it conservative (avoid splitting on abbreviations).
# We split BEFORE the next sentence start: uppercase Latin or Chinese char.
# We deliberately do NOT split before "[" or "(" because those are usually
# citation markers and should stay with the preceding claim.
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?。！？])\s+(?=[A-Z一-鿿])"
)

# Citation forms recognised:
#   [1] [12] [src:foo] [bar-2024]
#   (Smith 2024)  (Smith and Lee, 2024)  (Smith et al., 2024)
_CITATION_RE = re.compile(
    r"""(
        \[[A-Za-z0-9_\-:.]{1,40}\]                       # bracket citation
      | \([A-Z][A-Za-z\-]+(?:\s+(?:and|et al\.?|&)\s+[A-Z][A-Za-z\-]+)*,?\s+\d{4}[a-z]?\)
    )""",
    re.VERBOSE,
)

# Low-signal phrases — bilingual.  Each match counts as one low-signal hit.
_LOW_SIGNAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bit is well[- ]known\b",
        r"\bobviously\b",
        r"\bclearly\b",
        r"\bin recent years\b",
        r"\bplays? an? important role\b",
        r"\bsignificant(ly)?\b",
        r"\bnumerous studies\b",
        r"\bvarious approaches\b",
        r"\bin this paper, we propose\b",
        r"\bcomprehensive\b",
        r"众所周知",
        r"显然",
        r"近年来",
        r"具有重要意义",
        r"广泛应用",
        r"取得了显著",
        r"综合性",
    )
)


def _split_claims(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text or "") if p.strip()]
    # Also split on Chinese full stops standing alone with no surrounding latin
    out: list[str] = []
    for part in parts:
        out.extend(s.strip() for s in re.split(r"(?<=[。！？])", part) if s.strip())
    # Deduplicate adjacent empty entries
    return [c for c in out if c]


def _find_citations(claim: str) -> list[str]:
    return [m.group(0) for m in _CITATION_RE.finditer(claim)]


def _is_low_signal(claim: str) -> tuple[bool, list[str]]:
    hits = [pat.pattern for pat in _LOW_SIGNAL_PATTERNS if pat.search(claim)]
    return (len(hits) > 0, hits)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClaimAnalysis:
    text: str
    citations: list[str] = field(default_factory=list)
    is_low_signal: bool = False
    low_signal_tags: list[str] = field(default_factory=list)

    @property
    def cited(self) -> bool:
        return bool(self.citations)


@dataclass(slots=True)
class GroundingReport:
    total_claims: int
    cited_claims: int
    low_signal_claims: int
    citation_density: float
    nugget_density: float
    claims: list[ClaimAnalysis] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_claims": self.total_claims,
            "cited_claims": self.cited_claims,
            "low_signal_claims": self.low_signal_claims,
            "citation_density": self.citation_density,
            "nugget_density": self.nugget_density,
            "claims": [asdict(c) for c in self.claims],
        }

    @property
    def passes_minimum_grounding(self) -> bool:
        """Default gate used by ``judge_ensemble`` evidence_coverage scorer."""

        return (
            self.total_claims > 0
            and self.citation_density >= 0.8
            and self.nugget_density >= 0.6
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def analyze_grounding(text: str) -> GroundingReport:
    """Run the structural grounding analysis on a candidate text."""

    claims_raw = _split_claims(text)
    analyses: list[ClaimAnalysis] = []
    for raw in claims_raw:
        low, tags = _is_low_signal(raw)
        analyses.append(
            ClaimAnalysis(
                text=raw,
                citations=_find_citations(raw),
                is_low_signal=low,
                low_signal_tags=tags,
            )
        )

    total = len(analyses)
    cited = sum(1 for c in analyses if c.cited)
    low_signal = sum(1 for c in analyses if c.is_low_signal)
    informative = total - low_signal

    return GroundingReport(
        total_claims=total,
        cited_claims=cited,
        low_signal_claims=low_signal,
        citation_density=(cited / total) if total else 0.0,
        nugget_density=(informative / total) if total else 0.0,
        claims=analyses,
    )


def low_signal_spans(text: str) -> Iterable[str]:
    """Convenience helper: return only the low-signal sentences as strings.

    Used by ``preference`` module to suggest negative examples automatically.
    """

    for analysis in analyze_grounding(text).claims:
        if analysis.is_low_signal:
            yield analysis.text
