"""Eval flywheel (v0.41).

The evaluation plane that the v0.40 review asked for: every domain +
functional skill ships a versioned **EvalPack** (capability / regression
/ calibration cases per Anthropic's 2026-01 eval guidance), and the
PreferenceStore graduation loop turns accepted user spans into the next
version of the bench.

The plane shape mirrors :mod:`omni_hub.knowledge_plane` — typed
dataclasses, JSONL on disk, atomic version bumps, Proposal-gated
upgrades.  Stdlib only.

Three SOTA invariants this code encodes (per ``docs/eval-flywheel-v0.41.md``):

* **Three eval classes** (Anthropic 2026-01): ``capability`` /
  ``regression`` / ``calibration`` — explicit on every EvalCase.
* **80 / 20 holdout discipline** — public ``seed.jsonl`` + private
  ``holdout-private.jsonl`` (gitignored).  Holdout once burned, rotate.
* **Graduation rule**: ``PreferenceStore[domain].accepted ≥ N`` →
  ``Proposal(kind=eval_pack_upgrade)`` → human review → new ``v0.X+1/``.
  Never auto-promote.
"""

from __future__ import annotations

from .promote import GraduationCandidate, propose_pack_upgrade
from .run import CaseResult, EvalRun, EvalRunner
from .store import (
    EVAL_ROOT,
    EvalCase,
    EvalClass,
    EvalPack,
    EvalStore,
)

__all__ = [
    "CaseResult",
    "EVAL_ROOT",
    "EvalCase",
    "EvalClass",
    "EvalPack",
    "EvalRun",
    "EvalRunner",
    "EvalStore",
    "GraduationCandidate",
    "propose_pack_upgrade",
]
