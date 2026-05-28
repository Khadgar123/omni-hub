"""A/B test framework (v0.29).

Compares two candidate answers / two skill-prompt variants against a
shared evaluation reference, using the Judge framework from v0.23.

Stores every run in ``.omni/ab_tests.sqlite3`` (SQLite WAL) so the
v0.30 dogfood phase can compute lifetime win-rates per
domain×variant.  Promoting a winner is **always** human-gated: this
module emits findings, never writes SKILL.md directly.

Use cases:

* Two candidate prompt revisions for the same skill — score side-by-side.
* Two judges scoring the same candidate — useful for Judge calibration.
* Regression watch — last-known-good vs current candidate, daily.
"""

from __future__ import annotations

from .runner import (
    ABTestRunner,
    ABTestVerdict,
    Variant,
)
from .store import ABTestStore

__all__ = [
    "ABTestRunner",
    "ABTestStore",
    "ABTestVerdict",
    "Variant",
]
