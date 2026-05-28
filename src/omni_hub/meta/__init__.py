"""Meta — self-iteration skill (v0.28).

Cross-skill knowledge transfer harness.  Scans PreferenceStore across
all 19 domains, detects winning patterns (terms common in
``accepted_spans``, rare in ``rejected_spans``), and proposes
cross-skill prompt updates for domains where the same pattern is
under-represented.

This is the **meta** vertical-skill's primary execution path.  It
emits Proposal(kind=cross_skill_transfer) records — humans approve
before any SKILL.md gets updated.

No LLM call: heuristics + token statistics across PreferenceRecords.
Tier-up to LLMJudge ranking is the v0.31 follow-up.
"""

from __future__ import annotations

from .cross_skill import (
    CrossSkillFinding,
    CrossSkillTransfer,
    PatternSignal,
)

__all__ = [
    "CrossSkillFinding",
    "CrossSkillTransfer",
    "PatternSignal",
]
