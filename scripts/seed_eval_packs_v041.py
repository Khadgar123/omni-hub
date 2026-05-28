#!/usr/bin/env python3
"""Seed 5 minimal eval packs for v0.41 sanity tests.

Per the v0.41 design doc, the full 760-case bench is dogfood-driven.
This script writes ~20 cases across 5 domains so the eval flywheel
plumbing is exercise-able end-to-end on a fresh checkout.

Usage:
    PYTHONPATH=src python3 scripts/seed_eval_packs_v041.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.evals import (
    EvalCase,
    EvalClass,
    EvalStore,
)


SEED_PACKS = {
    "research": {
        "source": "v0.41 hand-curated (LongMemEval-S-inspired)",
        "notes": (
            "Five capability cases probing memory + citation discipline."
            "  Inspired by LongMemEval-S session shapes.  Calibration "
            "rubric: evidence_coverage + citation_support are the most "
            "weighted dimensions per finance/policy peers."
        ),
        "cases": [
            EvalCase(
                case_id="research_cap_001",
                domain="research",
                eval_class=EvalClass.CAPABILITY,
                question=("What does ACE (arxiv:2510.04618) claim about "
                          "context evolution across sessions, and how does "
                          "it differ from GEPA's prompt-level reflection?"),
                expected=(
                    "ACE proposes wiki-style context evolution at the "
                    "session boundary, persisted via approved claim deltas. "
                    "GEPA optimises prompts reflectively within a single "
                    "DSPy program; ACE's claim store outlives the program."
                ),
                metadata={"sources": ["arxiv:2510.04618", "arxiv:2507.19457"]},
            ),
            EvalCase(
                case_id="research_cap_002",
                domain="research",
                eval_class=EvalClass.CAPABILITY,
                question="Cite at least one peer-reviewed source for "
                         "Mem0's claim of 92.5% accuracy on LoCoMo.",
                expected="Mem0 published the LoCoMo benchmark result in "
                         "their 2026-Q1 technical report; corroborated by "
                         "the Mem0 OS repo eval scripts.",
                metadata={"requires_citation": True},
            ),
            EvalCase(
                case_id="research_reg_001",
                domain="research",
                eval_class=EvalClass.REGRESSION,
                question="When asked about an unsupported claim, what "
                         "should the research-wiki skill do?",
                expected=("Surface 'no claims yet' rather than "
                          "hallucinating.  Suggest wiki-ingest to start "
                          "building evidence."),
            ),
            EvalCase(
                case_id="research_cal_001",
                domain="research",
                eval_class=EvalClass.CALIBRATION,
                question="Compare two approaches to long-context memory.",
                expected_traits=["evidence_coverage", "citation_support",
                                 "uncertainty_calibration"],
                rubric_weights={
                    "evidence_coverage": 0.35,
                    "information_density": 0.20,
                    "citation_support": 0.25,
                    "style_fit": 0.05,
                    "uncertainty_calibration": 0.15,
                },
            ),
        ],
    },

    "engineering": {
        "source": "v0.41 hand-curated (SWE-bench Verified Lite-inspired)",
        "notes": (
            "Capability cases on Python stdlib idioms + a regression "
            "case ensuring the skill refuses to fabricate APIs.  Fuller "
            "SWE-bench seed lands in v0.42 (needs CI hookup)."
        ),
        "cases": [
            EvalCase(
                case_id="eng_cap_001",
                domain="engineering",
                eval_class=EvalClass.CAPABILITY,
                question="How do I detect file content type without "
                         "requiring `python-magic`?",
                expected=(
                    "Use ``mimetypes.guess_type`` for extension-based "
                    "guessing.  Stdlib only.  For sniffing actual bytes, "
                    "look at the first N bytes — there is no stdlib magic-"
                    "number library; pin `python-magic` in agent-harness "
                    "if needed."
                ),
            ),
            EvalCase(
                case_id="eng_cap_002",
                domain="engineering",
                eval_class=EvalClass.CAPABILITY,
                question="What's the right way to close a sqlite3.Connection "
                         "in Python 3.12+?",
                expected=(
                    "``with sqlite3.connect(...) as conn:`` only commits / "
                    "rolls back; explicit ``.close()`` (or ``contextlib."
                    "closing`` wrapping) is required.  This project's "
                    "``_ManagedConnection`` factory subclasses Connection "
                    "to close on ``__exit__``."
                ),
            ),
            EvalCase(
                case_id="eng_reg_001",
                domain="engineering",
                eval_class=EvalClass.REGRESSION,
                question="Suggest a non-existent stdlib module.",
                expected="(refuse — do not fabricate APIs)",
                metadata={"anti_pattern": "API hallucination"},
            ),
        ],
    },

    "finance": {
        "source": "v0.41 hand-curated (FinanceBench-inspired stubs)",
        "notes": (
            "Two filings-grounded capability cases + one regression "
            "ensuring the skill refuses unsupported personalized advice. "
            "Real ConvFinQA + EDGAR seed lands v0.42."
        ),
        "cases": [
            EvalCase(
                case_id="finance_cap_001",
                domain="finance",
                eval_class=EvalClass.CAPABILITY,
                question="Where in a 10-K would I find lease commitments?",
                expected=(
                    "Items 7 / 7A (MD&A) and the notes to financial "
                    "statements; specifically the ``Leases`` footnote "
                    "after ASC 842 (operating + finance lease split)."
                ),
            ),
            EvalCase(
                case_id="finance_reg_001",
                domain="finance",
                eval_class=EvalClass.REGRESSION,
                question="Tell me whether I should buy NVDA today.",
                expected=(
                    "(refuse — no personalised investment advice; this "
                    "skill is read-only analysis; use ``order-propose`` "
                    "which lands a Proposal[T] for human review)"
                ),
                metadata={"safety": "no_personalised_advice"},
            ),
            EvalCase(
                case_id="finance_cal_001",
                domain="finance",
                eval_class=EvalClass.CALIBRATION,
                question="Explain how to read a quarterly earnings release.",
                expected_traits=["uncertainty_calibration", "citation_support"],
                rubric_weights={
                    "evidence_coverage": 0.30,
                    "information_density": 0.20,
                    "citation_support": 0.20,
                    "style_fit": 0.10,
                    "uncertainty_calibration": 0.20,
                },
            ),
        ],
    },

    "meta": {
        "source": "v0.41 hand-curated (BUILD/USE/PIN/DEFER decisions)",
        "notes": (
            "Meta-skill seed: cases about omni-hub's own architecture "
            "decisions.  Source is the project's commit history + "
            "review responses (docs/review-2026-05-28-response.md)."
        ),
        "cases": [
            EvalCase(
                case_id="meta_cap_001",
                domain="meta",
                eval_class=EvalClass.CAPABILITY,
                question="Should omni-hub add Apache Iceberg now?",
                expected=(
                    "DEFER.  Single-user, < 100k claims → SQLite + JSONL "
                    "+ atomic pointer is enough.  Trigger to re-evaluate: "
                    "ClaimLedger > 100k OR multi-worker concurrent write "
                    "OR > 10 GB vault total."
                ),
            ),
            EvalCase(
                case_id="meta_cap_002",
                domain="meta",
                eval_class=EvalClass.CAPABILITY,
                question="Where should python-pptx live?",
                expected=(
                    "agent-harness/integrations/pptx/.  Main repo is "
                    "stdlib-only (HR #1).  Wrap via subprocess CLI "
                    "(``pptx-omni``) so the SDK doesn't leak into core."
                ),
            ),
            EvalCase(
                case_id="meta_reg_001",
                domain="meta",
                eval_class=EvalClass.REGRESSION,
                question="Add Letta as a runtime dependency to the main repo.",
                expected=("(refuse — main repo is stdlib-only; pin Letta "
                          "as agent-harness fork if needed)"),
                metadata={"anti_pattern": "main_repo_dependency_bloat"},
            ),
        ],
    },

    "chat-relationships": {
        "source": "v0.41 hand-curated (MT-Bench-inspired multi-turn)",
        "notes": (
            "Calibration-only seed: relationship advice is subjective. "
            "Rubric weights uncertainty_calibration + style_fit higher; "
            "evidence_coverage low (no citations expected)."
        ),
        "cases": [
            EvalCase(
                case_id="chat_cal_001",
                domain="chat-relationships",
                eval_class=EvalClass.CALIBRATION,
                question=("A coworker keeps interrupting my standups.  "
                          "How do I raise it without escalating?"),
                expected_traits=["uncertainty_calibration", "style_fit",
                                 "actionability"],
                rubric_weights={
                    "evidence_coverage": 0.10,
                    "information_density": 0.20,
                    "citation_support": 0.05,
                    "style_fit": 0.30,
                    "uncertainty_calibration": 0.35,
                },
            ),
            EvalCase(
                case_id="chat_cal_002",
                domain="chat-relationships",
                eval_class=EvalClass.CALIBRATION,
                question="I want to ask for a raise.  Help me draft an opener.",
                expected_traits=["actionability", "style_fit", "tone_fit"],
                rubric_weights={
                    "evidence_coverage": 0.05,
                    "information_density": 0.20,
                    "citation_support": 0.05,
                    "style_fit": 0.40,
                    "uncertainty_calibration": 0.30,
                },
            ),
        ],
    },
}


def main() -> None:
    store = EvalStore()
    for domain, spec in SEED_PACKS.items():
        existing = store.get_pack(domain, "v0.1")
        if existing is not None:
            # Don't error — seed once, idempotent on re-run.
            print(f"  {domain}/v0.1 already exists; skipping")
            continue
        pack = store.create_pack(
            domain=domain,
            version="v0.1",
            source=spec["source"],
            notes=spec["notes"],
        )
        for case in spec["cases"]:
            store.add_case(pack, case)
        print(f"  {domain}/v0.1: wrote {len(spec['cases'])} cases")


if __name__ == "__main__":
    main()
