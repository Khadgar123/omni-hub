"""PreferenceStore → EvalPack graduation (v0.41).

Per Anthropic 2026-01 + the v0.40 review:

    "capability tasks with high pass rates graduate to become regression
     evals"  → graduation is human-gated via Proposal[T].

This module computes a *candidate* pack from PreferenceStore data,
emits a ``Proposal(kind=eval_pack_upgrade)``, and leaves the actual
write to ``Proposal.approve()`` + ``EvalStore.create_pack()`` (so the
flywheel matches the project's single-write-chokepoint convention).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..harness.preference import PreferenceStore
from ..proposals import PENDING, Proposal, ProposalStore
from .store import EvalCase, EvalClass


# Graduation thresholds (calibrated against the v0.41 doc's flywheel rule)
ACCEPTED_FLOOR = 100              # PreferenceStore[domain].accepted ≥ N
RECENT_TOP_N = 25                 # accepted spans → capability cases
ADVERSARIAL_N = 5                 # rejected spans → adversarial regression cases

# Patterns we treat as "trivially short" — skip these as not-really-claims.
_TRIVIAL_SPAN = re.compile(r"^\s*[a-zA-Z]{1,3}\s*$")


@dataclass(slots=True)
class GraduationCandidate:
    """One PreferenceStore-derived eval candidate ready for human review."""

    domain: str
    accepted_count: int
    rejected_count: int
    candidate_cases: list[dict[str, Any]] = field(default_factory=list)
    proposal_id: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def scan_preference(
    workspace: Path | str,
    domain: str,
    *,
    accepted_floor: int = ACCEPTED_FLOOR,
    top_n: int = RECENT_TOP_N,
    adversarial_n: int = ADVERSARIAL_N,
) -> GraduationCandidate | None:
    """Walk PreferenceStore for ``domain``.  Return None when below floor."""

    store = PreferenceStore(Path(workspace) / ".omni" / "preference")
    records = list(store.read(domain))
    if not records:
        return None

    accepted_count = sum(1 for r in records if r.decision == "accepted")
    rejected_count = sum(1 for r in records if r.decision == "rejected")
    if accepted_count < accepted_floor:
        return GraduationCandidate(
            domain=domain,
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            note=(
                f"below graduation floor ({accepted_count} < {accepted_floor}); "
                f"continue accumulating PreferenceRecords"
            ),
        )

    # Tally accepted span frequencies and pick top-N as capability cases.
    accepted_spans: list[str] = []
    rejected_spans: list[str] = []
    for r in records:
        if r.decision == "accepted":
            accepted_spans.extend(r.accepted_spans or [])
        else:
            rejected_spans.extend(r.rejected_spans or [])

    accepted_counter = Counter(s for s in accepted_spans if not _TRIVIAL_SPAN.match(s))
    rejected_counter = Counter(s for s in rejected_spans if not _TRIVIAL_SPAN.match(s))

    candidates: list[dict[str, Any]] = []
    for span, count in accepted_counter.most_common(top_n):
        # v0.42 P1: stable sha256(domain|span|class) replaces Python
        # built-in hash() so case ids are reproducible across processes
        # (PYTHONHASHSEED randomises hash() since 3.3).  Repeat-run
        # idempotency is part of the "version pinning" eval invariant.
        case = EvalCase(
            case_id=_stable_case_id(domain, span, "capability", prefix="grad"),
            domain=domain,
            eval_class=EvalClass.CAPABILITY,
            question=_question_from_span(span),
            expected=span,
            metadata={"graduated_from_preference": True,
                      "accepted_freq": count,
                      "promotion_source": "preference_store"},
            graduated_from=f"preference:{domain}:accepted_freq_{count}",
        )
        candidates.append(case.to_dict())

    for span, count in rejected_counter.most_common(adversarial_n):
        case = EvalCase(
            case_id=_stable_case_id(domain, span, "regression", prefix="adv"),
            domain=domain,
            eval_class=EvalClass.REGRESSION,
            question=_question_from_span(span),
            expected="(rejected baseline — must not produce this)",
            metadata={"graduated_from_preference": True,
                      "rejected_freq": count,
                      "promotion_source": "preference_store_adversarial",
                      "anti_pattern": span},
            graduated_from=f"preference:{domain}:rejected_freq_{count}",
        )
        candidates.append(case.to_dict())

    return GraduationCandidate(
        domain=domain,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        candidate_cases=candidates,
    )


def propose_pack_upgrade(
    workspace: Path | str,
    domain: str,
    new_version: str = "v0.2",
) -> GraduationCandidate:
    """Compute candidate + emit Proposal(kind=eval_pack_upgrade).

    The Proposal carries the candidate cases as its ``payload``; an
    operator approves via ``omni-hub propose-approve --id <pid>`` and
    the v0.X+1 EvalPack materialises on ``eval-promote --apply``.
    """

    candidate = scan_preference(workspace, domain)
    if candidate is None or not candidate.candidate_cases:
        return candidate or GraduationCandidate(
            domain=domain, accepted_count=0, rejected_count=0,
            note="no PreferenceStore data for this domain",
        )

    proposal_id = _stable_proposal_id(domain, new_version)
    proposal = Proposal(
        proposal_id=proposal_id,
        kind="eval_pack_upgrade",
        state=PENDING,
        title=f"Eval pack upgrade: {domain} → {new_version}",
        summary=(
            f"{candidate.accepted_count} accepted + "
            f"{candidate.rejected_count} rejected PreferenceRecords for "
            f"{domain!r}.  Candidate: {len(candidate.candidate_cases)} cases "
            f"(top accepted + adversarial rejected)."
        ),
        payload={
            "domain": domain,
            "new_version": new_version,
            "cases": candidate.candidate_cases,
            "stats": {
                "accepted_count": candidate.accepted_count,
                "rejected_count": candidate.rejected_count,
            },
        },
        source_path=f"evals/{domain}/{new_version}",
        suggested_action="approve_and_materialise",
        confidence=0.5,
    )
    store = ProposalStore(Path(workspace))
    store.store(proposal)
    candidate.proposal_id = proposal_id
    candidate.note = (
        f"proposal {proposal_id} pending; review via "
        f"`omni-hub propose-list --kind eval_pack_upgrade`"
    )
    return candidate


def _question_from_span(span: str) -> str:
    """Turn an accepted/rejected span into a question prompt.

    Heuristic until v0.42 brings an LLM-driven candidate-question step:
    extract first sentence; if it ends in a question mark, keep as-is;
    otherwise wrap in "How would you address: ...".
    """

    text = (span or "").strip()
    first_sentence = re.split(r"[.?!\n]", text, maxsplit=1)[0].strip()
    if first_sentence.endswith("?"):
        return first_sentence
    if len(text) < 80:
        return f"How would you address: {text}"
    return f"How would you address: {text[:120]}..."


def _stable_proposal_id(domain: str, version: str) -> str:
    """Stable IDs let repeat-runs idempotently update the same Proposal."""

    import hashlib

    key = f"eval_pack_upgrade:{domain}:{version}".encode("utf-8")
    return hashlib.sha256(key).hexdigest()[:16]


def _stable_case_id(
    domain: str, span: str, eval_class: str, *, prefix: str = "grad",
) -> str:
    """Reproducible case_id derived from sha256(domain|class|span).

    Python's built-in ``hash()`` is process-randomised since 3.3 via
    PYTHONHASHSEED, breaking the "repeat-run idempotent" eval
    invariant (v0.42 P1 review finding).  This sha256 derivation is
    stable across runs, machines, and Python versions.
    """

    import hashlib

    payload = f"{domain}|{eval_class}|{span}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:12]
    return f"{prefix}_{digest}"


__all__ = ["GraduationCandidate", "propose_pack_upgrade", "scan_preference"]
