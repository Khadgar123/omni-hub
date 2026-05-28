"""Argilla-compatible proposal review and feedback dataset bridge.

This module deliberately avoids importing ``argilla``.  The pinned Argilla
fork is the UI/runtime; this file is the local contract that lets Omni Hub
export Proposal records, ingest reviewed records, and turn those reviews into
the local preference store consumed by promptfoo/DSPy/GEPA flows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..proposals import APPROVED, REJECTED, Proposal, ProposalStore
from .preference import PreferenceRecord, PreferenceStore


SCHEMA_VERSION = 1
DEFAULT_DATASET = "omni_proposal_review_v1"
DECISION_CHOICES = ["approve", "edit", "reject", "insufficient_context"]
RATING_QUESTIONS = [
    "faithfulness",
    "citation_support",
    "information_density",
    "uncertainty_calibration",
]


@dataclass(slots=True)
class FeedbackDecision:
    """One reviewed Argilla record, normalized for local sync."""

    proposal_id: str
    decision: str
    reason: str = ""
    reviewer: str = "argilla"
    edited_text: str = ""
    domain: str = "general"
    skill_id: str = ""
    skill_version: str = "v0"
    ratings: dict[str, float] = field(default_factory=dict)


def build_dataset_settings(name: str = DEFAULT_DATASET) -> dict[str, Any]:
    """Return a versioned Argilla settings document.

    The shape is intentionally JSON-serializable instead of Argilla-SDK
    objects, so it can be committed, diffed, rendered by CLI, or translated
    by a later thin API client.
    """

    return {
        "name": name,
        "schema_version": SCHEMA_VERSION,
        "guidelines": (
            "Review each proposal as a candidate, not as source of truth. "
            "Approve only when evidence and wording are acceptable; edit when "
            "the idea is useful but the output needs correction; reject when "
            "it is unsupported, redundant, low-signal, or unsafe to promote."
        ),
        "fields": [
            {"name": "title", "type": "text", "required": True},
            {"name": "summary", "type": "text", "required": False},
            {"name": "candidate_text", "type": "text", "required": True},
            {"name": "source_paths", "type": "text", "required": False},
            {"name": "payload_json", "type": "text", "required": False},
        ],
        "questions": [
            {
                "name": "decision",
                "type": "label",
                "labels": DECISION_CHOICES,
                "required": True,
            },
            *[
                {
                    "name": question,
                    "type": "rating",
                    "values": [1, 2, 3, 4, 5],
                    "required": False,
                }
                for question in RATING_QUESTIONS
            ],
            {
                "name": "corrected_text",
                "type": "text",
                "required": False,
                "use_markdown": True,
            },
            {
                "name": "review_reason",
                "type": "text",
                "required": True,
                "use_markdown": True,
            },
        ],
        "metadata_properties": [
            {"name": "proposal_id", "type": "terms"},
            {"name": "kind", "type": "terms"},
            {"name": "state", "type": "terms"},
            {"name": "domain", "type": "terms"},
            {"name": "skill_id", "type": "terms"},
            {"name": "skill_version", "type": "terms"},
            {"name": "model", "type": "terms"},
            {"name": "source_task_id", "type": "terms"},
            {"name": "artifact_id", "type": "terms"},
            {"name": "created_at", "type": "terms"},
            {"name": "confidence", "type": "float"},
            {"name": "tokens_total", "type": "integer"},
            {"name": "cost_usd", "type": "float"},
            {"name": "schema_version", "type": "integer"},
        ],
    }


def proposal_to_record(
    proposal: Proposal,
    *,
    dataset: str = DEFAULT_DATASET,
    domain: str = "general",
    skill_id: str = "",
    skill_version: str = "v0",
) -> dict[str, Any]:
    """Convert one ``Proposal`` into an Argilla-ready record dictionary."""

    payload = proposal.payload
    candidate_text = _candidate_text(proposal)
    source_paths = _source_paths(proposal)
    metadata = {
        "proposal_id": proposal.proposal_id,
        "kind": proposal.kind,
        "state": proposal.state,
        "domain": domain,
        "skill_id": skill_id,
        "skill_version": skill_version,
        "model": _string(payload.get("model")),
        "source_task_id": proposal.source_task_id or "",
        "artifact_id": _string(payload.get("artifact_id")),
        "created_at": proposal.created_at,
        "confidence": float(proposal.confidence),
        "tokens_total": _int_or_zero(payload.get("tokens_total")),
        "cost_usd": _float_or_zero(payload.get("cost_usd")),
        "schema_version": SCHEMA_VERSION,
    }
    return {
        "dataset": dataset,
        "external_id": proposal.proposal_id,
        "fields": {
            "title": proposal.title,
            "summary": proposal.summary,
            "candidate_text": candidate_text,
            "source_paths": "\n".join(source_paths),
            "payload_json": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        },
        "metadata": metadata,
        "suggestions": [
            {"question_name": "decision", "value": _suggested_decision(proposal)},
            {
                "question_name": "review_reason",
                "value": proposal.suggested_action or "review proposal",
            },
        ],
    }


def export_proposals(
    proposals: Iterable[Proposal],
    output_path: Path,
    *,
    dataset: str = DEFAULT_DATASET,
    domain: str = "general",
    skill_id: str = "",
    skill_version: str = "v0",
) -> dict[str, Any]:
    """Write proposals as Argilla-ready JSONL records."""

    records = [
        proposal_to_record(
            proposal,
            dataset=dataset,
            domain=domain,
            skill_id=skill_id,
            skill_version=skill_version,
        )
        for proposal in proposals
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            fh.write("\n")
    return {"file": str(output_path), "count": len(records), "dataset": dataset}


def sync_feedback_file(
    input_path: Path,
    *,
    proposal_store: ProposalStore,
    preference_store: PreferenceStore,
    default_domain: str = "general",
) -> dict[str, Any]:
    """Sync reviewed Argilla JSONL records back into Proposal + Preference stores."""

    synced = skipped = approved = rejected = edited = 0
    errors: list[dict[str, str]] = []
    for line_no, raw in enumerate(_read_jsonl(input_path), start=1):
        try:
            feedback = feedback_from_record(raw, default_domain=default_domain)
            proposal = proposal_store.load(feedback.proposal_id)
        except Exception as exc:  # keep batch sync moving; report row failure.
            skipped += 1
            errors.append({"line": str(line_no), "error": str(exc)})
            continue

        preference_decision = _preference_decision(feedback.decision)
        reason = feedback.reason or feedback.decision
        if preference_decision in {"accepted", "edited"}:
            decided = proposal_store.approve(
                feedback.proposal_id,
                reason=reason,
                decided_by=feedback.reviewer,
            )
            approved += 1
        else:
            decided = proposal_store.reject(
                feedback.proposal_id,
                reason=reason,
                decided_by=feedback.reviewer,
            )
            rejected += 1

        if preference_decision == "edited":
            edited += 1
        preference_store.append(_preference_record(decided, feedback, preference_decision))
        synced += 1

    return {
        "input": str(input_path),
        "synced": synced,
        "skipped": skipped,
        "approved": approved,
        "rejected": rejected,
        "edited": edited,
        "errors": errors,
    }


def feedback_from_record(
    record: dict[str, Any],
    *,
    default_domain: str = "general",
) -> FeedbackDecision:
    """Normalize top-level or Argilla-exported response shapes."""

    metadata = dict(record.get("metadata", {}))
    values, reviewer = _response_values(record)
    proposal_id = _string(
        record.get("proposal_id")
        or record.get("external_id")
        or metadata.get("proposal_id")
    )
    decision = _normalize_decision(_string(values.get("decision") or record.get("decision")))
    if not proposal_id:
        raise ValueError("feedback record is missing proposal_id/external_id")
    if not decision:
        raise ValueError(f"feedback record {proposal_id} is missing decision")
    return FeedbackDecision(
        proposal_id=proposal_id,
        decision=decision,
        reason=_string(
            values.get("review_reason")
            or values.get("reason")
            or record.get("reason")
        ),
        reviewer=_string(record.get("reviewer") or reviewer or "argilla"),
        edited_text=_string(
            values.get("corrected_text")
            or values.get("edited_text")
            or record.get("edited_text")
        ),
        domain=_string(metadata.get("domain") or record.get("domain") or default_domain),
        skill_id=_string(metadata.get("skill_id") or record.get("skill_id")),
        skill_version=_string(metadata.get("skill_version") or record.get("skill_version") or "v0"),
        ratings={
            key: float(value)
            for key, value in values.items()
            if key in RATING_QUESTIONS and _is_number(value)
        },
    )


def _preference_record(
    proposal: Proposal,
    feedback: FeedbackDecision,
    decision: str,
) -> PreferenceRecord:
    candidate_text = _candidate_text(proposal)
    return PreferenceRecord(
        task_id=proposal.source_task_id or feedback.proposal_id,
        domain=feedback.domain,
        prompt_version=feedback.skill_version or "v0",
        candidate_text=candidate_text,
        decision=decision,
        accepted_spans=[feedback.edited_text or candidate_text]
        if decision in {"accepted", "edited"} and (feedback.edited_text or candidate_text)
        else [],
        rejected_spans=[candidate_text] if decision == "rejected" and candidate_text else [],
        edited_text=feedback.edited_text,
        reason=feedback.reason,
        reviewer=feedback.reviewer,
        judge_summary=feedback.ratings,
    )


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _candidate_text(proposal: Proposal) -> str:
    """Pick the reviewable text for an Argilla record.

    Generic keys win first (text / candidate_text / answer / content /
    edited_text — the keys agent workers populate).  Then kind-specific
    fallbacks: ``wiki_update`` carries the synthesis page body, and
    ``lint_finding`` carries a structured rule + finding summary.
    """

    payload = proposal.payload
    for key in ("text", "candidate_text", "answer", "content", "edited_text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value

    if proposal.kind == "wiki_update":
        body = payload.get("body")
        if isinstance(body, str) and body.strip():
            return body
        # Older shape: claims-only payload, no rendered body.
        claims = payload.get("claims") or []
        if isinstance(claims, list) and claims:
            lines = ["## Candidate claims", ""]
            for claim in claims:
                if not isinstance(claim, dict):
                    continue
                lines.append(
                    f"- `{claim.get('claim_id', '?')}` ({claim.get('confidence', 0)}) "
                    f"{claim.get('statement', '')}"
                )
            return "\n".join(lines)

    if proposal.kind == "lint_finding":
        rule = str(payload.get("rule", "")).strip() or "unknown_rule"
        severity = str(payload.get("severity", "")).strip() or "unknown"
        affected_paths = payload.get("affected_paths") or []
        affected_claims = payload.get("affected_claim_ids") or []
        lines = [
            f"## Lint finding ({rule}, severity={severity})",
            "",
            proposal.summary,
            "",
        ]
        if affected_paths:
            lines.append("### Affected pages")
            for path in affected_paths:
                lines.append(f"- `{path}`")
            lines.append("")
        if affected_claims:
            lines.append("### Affected claims")
            for cid in affected_claims:
                lines.append(f"- `{cid}`")
        return "\n".join(lines).rstrip()

    if proposal.summary.strip():
        return proposal.summary
    return proposal.title


def _source_paths(proposal: Proposal) -> list[str]:
    paths = list(proposal.source_paths)
    if proposal.source_path:
        paths.append(proposal.source_path)
    return list(dict.fromkeys(path for path in paths if path))


def _suggested_decision(proposal: Proposal) -> str:
    if proposal.state == APPROVED:
        return "approve"
    if proposal.state == REJECTED:
        return "reject"
    if proposal.kind in {"low_signal", "stale"}:
        return "reject"
    if proposal.kind == "lint_finding":
        # Lint findings default to approve = "fix this": the human acknowledges
        # the rule and will act.  reject = "ignore this finding".
        severity = str(proposal.payload.get("severity", "")).lower()
        if severity == "low":
            return "approve"           # still actionable but low priority
        return "approve"
    return "approve"


def _response_values(record: dict[str, Any]) -> tuple[dict[str, Any], str]:
    values: dict[str, Any] = {}
    reviewer = ""
    if "responses" in record and isinstance(record["responses"], list):
        responses = [r for r in record["responses"] if isinstance(r, dict)]
        if responses:
            response = responses[-1]
            reviewer = _string(
                response.get("user_id")
                or response.get("username")
                or response.get("user")
            )
            raw_values = response.get("values", {})
            if isinstance(raw_values, dict):
                values = {
                    str(key): _unwrap_value(value)
                    for key, value in raw_values.items()
                }
    for key in ("decision", "review_reason", "reason", "corrected_text", "edited_text"):
        if key in record and key not in values:
            values[key] = record[key]
    for key in RATING_QUESTIONS:
        if key in record and key not in values:
            values[key] = record[key]
    return values, reviewer


def _unwrap_value(value: Any) -> Any:
    if isinstance(value, dict):
        if "value" in value:
            return value["value"]
        if "values" in value:
            return _unwrap_value(value["values"])
    if isinstance(value, list) and value:
        return _unwrap_value(value[-1])
    return value


def _normalize_decision(value: str) -> str:
    clean = value.strip().lower()
    aliases = {
        "approved": "approve",
        "accepted": "approve",
        "accept": "approve",
        "edited": "edit",
        "needs_edit": "edit",
        "rejected": "reject",
        "refuse": "reject",
        "insufficient": "insufficient_context",
        "insufficient_context": "insufficient_context",
    }
    return aliases.get(clean, clean if clean in DECISION_CHOICES else "")


def _preference_decision(decision: str) -> str:
    if decision == "approve":
        return "accepted"
    if decision == "edit":
        return "edited"
    return "rejected"


def _string(value: Any) -> str:
    return "" if value is None else str(value)


def _int_or_zero(value: Any) -> int:
    if _is_number(value):
        return int(value)
    return 0


def _float_or_zero(value: Any) -> float:
    if _is_number(value):
        return float(value)
    return 0.0


def _is_number(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True
