"""Pure deterministic audit contract for Discord media recovery evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import ipaddress
import json
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit

from .discord_media_recovery import (
    LEGACY_ZERO_BYTE_RECLASSIFICATION_KIND,
    HTTP_COMPENSATION_TERMINAL_REASONS,
    LEGACY_RETRY_TRIGGER,
    RESOLUTION_RETRY_TRIGGER,
    TRANSIENT_RESOLUTION_DETAILS,
    UNRESOLVED_RESOLUTION_DETAILS,
    _RESOLUTION_TERMINAL_REASONS,
    reclassified_zero_byte_attempt_numbers,
    validate_media_record_attempt_consistency,
)


MEDIA_RECOVERY_AUDIT_VERSION = 2
MEDIA_RECOVERY_AUDIT_KIND = "discord_media_resolution_recovery_audit"
MEDIA_RECOVERY_AUDIT_FILENAME = "media-recovery-audit.json"

_RETRY_TRIGGERS = frozenset({LEGACY_RETRY_TRIGGER, RESOLUTION_RETRY_TRIGGER})
_HTTP_COMPENSATION_REASONS = HTTP_COMPENSATION_TERMINAL_REASONS
_BINARY_STATUSES = frozenset({"complete", "captured_with_warning"})
_COVERED_STATUSES = _BINARY_STATUSES | {"reference_only"}
_RECORD_STATUSES = frozenset(
    {
        "complete",
        "captured_with_warning",
        "reference_only",
        "failed",
        "in_progress",
        "not_requested",
    }
)
_ATTEMPT_STATUSES = frozenset(
    {
        "complete",
        "captured_with_warning",
        "reference_only",
        "failed",
        "in_progress",
        "interrupted",
    }
)
_HARD_MEDIA_FAILURE_REASONS = frozenset(
    {
        "logical_identity_conflict",
        "size_limit_exceeded",
        "declared_media_type_mismatch",
        "media_type_mismatch",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_HOST_LABEL_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_BLOB_EXTENSION_PATTERN = re.compile(r"^[a-z0-9]+$")
_ROW_FIELDS = frozenset(
    {
        "row_id",
        "item_kind",
        "logical_key",
        "candidate_url_sha256",
        "candidate_host",
        "attempt_number",
        "retry_trigger",
        "evidence_reclassification",
        "status",
        "terminal_reason",
        "failure_detail",
        "actual_bytes",
        "binary_captured",
        "final_record_status",
        "final_record_terminal_reason",
        "disposition",
    }
)
_COUNT_KEYS = (
    "rows_total",
    "attempt_rows",
    "record_rows",
    "legacy_attempt_rows",
    "legacy_zero_byte_reclassification_rows",
    "typed_resolution_attempt_rows",
    "http_400_404_415_attempt_rows",
    "binary_captured_attempt_rows",
    "candidate_failed_record_covered_attempt_rows",
    "current_failed_records",
    "current_reference_only_records",
    "resolution_retry_pending_records",
    "resolution_retry_exhausted_records",
    "resolution_unresolved_records",
    "resolution_invalid_answer_records",
    "unsafe_records",
    "http_compensation_records",
    "hard_media_failure_records",
    "other_media_failure_records",
    "unresolved_blockers",
)
_FAILED_RECORD_BUCKETS = {
    "resolution_retry_pending": "resolution_retry_pending_records",
    "resolution_retry_exhausted_blocker": "resolution_retry_exhausted_records",
    "resolution_unresolved_blocker": "resolution_unresolved_records",
    "resolution_invalid_answer_blocker": "resolution_invalid_answer_records",
    "unsafe_blocker": "unsafe_records",
    "http_compensation_blocker": "http_compensation_records",
    "hard_media_failure_blocker": "hard_media_failure_records",
    "other_media_failure_blocker": "other_media_failure_records",
}


def canonical_media_recovery_audit_bytes(
    value: object,
    *,
    newline: bool = True,
) -> bytes:
    """Encode a value using the audit contract's canonical UTF-8 JSON form."""

    if not isinstance(newline, bool):
        raise ValueError("Discord media audit newline option is invalid")
    try:
        content = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("Discord media audit value is not canonical JSON") from exc
    return content + (b"\n" if newline else b"")


def rebuild_media_recovery_rows(
    records: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    """Rebuild the exact attempt/record row union from asset records."""

    if not isinstance(records, Mapping):
        raise ValueError("Discord media audit records are invalid")
    rows: list[dict[str, object]] = []
    row_ids: set[str] = set()
    for mapping_key, record in records.items():
        logical_key, reclassified_zero_attempts = _validate_record(
            mapping_key,
            record,
        )
        final_status = record["status"]
        final_reason = record["terminal_reason"]
        attempts = record["attempt_history"]
        assert isinstance(attempts, list)
        for attempt_number, attempt in enumerate(attempts, start=1):
            assert isinstance(attempt, Mapping)
            evidence_reclassification = (
                LEGACY_ZERO_BYTE_RECLASSIFICATION_KIND
                if attempt_number in reclassified_zero_attempts
                else None
            )
            if not _attempt_selected(
                attempt,
                evidence_reclassification=evidence_reclassification,
            ):
                continue
            row = _build_row(
                item_kind="attempt",
                logical_key=logical_key,
                candidate_url=attempt["url"],
                attempt_number=attempt_number,
                retry_trigger=attempt.get("retry_trigger"),
                evidence_reclassification=evidence_reclassification,
                status=attempt["status"],
                terminal_reason=attempt["terminal_reason"],
                failure_detail=attempt.get("failure_detail"),
                actual_bytes=attempt["actual_bytes"],
                sha256=attempt["sha256"],
                blob_path=attempt["blob_path"],
                final_record_status=final_status,
                final_record_terminal_reason=final_reason,
            )
            _append_unique_row(rows, row_ids, row)
        if final_status in {"failed", "reference_only"}:
            row = _build_row(
                item_kind="record",
                logical_key=logical_key,
                candidate_url=record["url"],
                attempt_number=None,
                retry_trigger=None,
                evidence_reclassification=None,
                status=final_status,
                terminal_reason=final_reason,
                failure_detail=None,
                actual_bytes=record["actual_bytes"],
                sha256=record["sha256"],
                blob_path=record["blob_path"],
                final_record_status=final_status,
                final_record_terminal_reason=final_reason,
            )
            _append_unique_row(rows, row_ids, row)
    rows.sort(key=_row_sort_key)
    return rows


def rebuild_media_recovery_counts(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, int]:
    """Derive all and only the fixed counters from validated rows."""

    if isinstance(rows, (str, bytes, bytearray)) or not isinstance(rows, Sequence):
        raise ValueError("Discord media audit rows are invalid")
    counts = {key: 0 for key in _COUNT_KEYS}
    row_ids: set[str] = set()
    final_by_key: dict[str, tuple[str, str | None]] = {}
    record_keys: set[str] = set()
    for row in rows:
        _validate_row(row)
        row_id = row["row_id"]
        assert isinstance(row_id, str)
        if row_id in row_ids:
            raise ValueError("Discord media audit row ID is duplicated")
        row_ids.add(row_id)
        logical_key = row["logical_key"]
        final_status = row["final_record_status"]
        final_reason = row["final_record_terminal_reason"]
        assert isinstance(logical_key, str)
        assert isinstance(final_status, str)
        final_fact = (final_status, final_reason)
        previous_final = final_by_key.setdefault(logical_key, final_fact)
        if previous_final != final_fact:
            raise ValueError("Discord media audit final record evidence conflicts")

        counts["rows_total"] += 1
        if row["item_kind"] == "attempt":
            counts["attempt_rows"] += 1
            if row["retry_trigger"] == LEGACY_RETRY_TRIGGER:
                counts["legacy_attempt_rows"] += 1
            if (
                row["evidence_reclassification"]
                == LEGACY_ZERO_BYTE_RECLASSIFICATION_KIND
            ):
                counts["legacy_zero_byte_reclassification_rows"] += 1
            if (
                row["retry_trigger"] in _RETRY_TRIGGERS
                or row["terminal_reason"] in _RESOLUTION_TERMINAL_REASONS
            ):
                counts["typed_resolution_attempt_rows"] += 1
            if row["terminal_reason"] in _HTTP_COMPENSATION_REASONS:
                counts["http_400_404_415_attempt_rows"] += 1
            if row["disposition"] == "binary_captured":
                counts["binary_captured_attempt_rows"] += 1
            if row["disposition"] == "candidate_failed_record_covered":
                counts["candidate_failed_record_covered_attempt_rows"] += 1
            continue

        if logical_key in record_keys:
            raise ValueError("Discord media audit record row is duplicated")
        record_keys.add(logical_key)
        counts["record_rows"] += 1
        if row["status"] == "reference_only":
            counts["current_reference_only_records"] += 1
            continue
        counts["current_failed_records"] += 1
        counts["unresolved_blockers"] += 1
        bucket = _FAILED_RECORD_BUCKETS.get(row["disposition"])
        if bucket is None:
            raise ValueError("Discord media audit failed record has no count bucket")
        counts[bucket] += 1

    for logical_key, (final_status, _) in final_by_key.items():
        should_have_record = final_status in {"failed", "reference_only"}
        if (logical_key in record_keys) != should_have_record:
            raise ValueError("Discord media audit record row partition is incomplete")
    if counts["rows_total"] != counts["attempt_rows"] + counts["record_rows"]:
        raise ValueError("Discord media audit row count partition is invalid")
    if counts["record_rows"] != (
        counts["current_failed_records"] + counts["current_reference_only_records"]
    ):
        raise ValueError("Discord media audit record status partition is invalid")
    failed_partition = sum(counts[key] for key in _FAILED_RECORD_BUCKETS.values())
    if failed_partition != counts["current_failed_records"]:
        raise ValueError("Discord media audit failed record partition is invalid")
    if counts["unresolved_blockers"] != counts["current_failed_records"]:
        raise ValueError("Discord media audit unresolved blocker count is invalid")
    return counts


def build_media_recovery_audit(
    *,
    run_id: str,
    request_sha256: str,
    policy_inputs_sha256: str | None,
    asset_index_sha256: str,
    records: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    """Build the fixed top-level media recovery audit artifact."""

    if not isinstance(run_id, str) or not run_id:
        raise ValueError("Discord media audit run identity is invalid")
    _require_utf8(run_id, "run identity")
    _validate_sha256(request_sha256, "request")
    if policy_inputs_sha256 is not None:
        _validate_sha256(policy_inputs_sha256, "policy inputs")
    _validate_sha256(asset_index_sha256, "asset index")
    rows = rebuild_media_recovery_rows(records)
    return {
        "version": MEDIA_RECOVERY_AUDIT_VERSION,
        "kind": MEDIA_RECOVERY_AUDIT_KIND,
        "run_id": run_id,
        "request_sha256": request_sha256,
        "policy_inputs_sha256": policy_inputs_sha256,
        "asset_index_sha256": asset_index_sha256,
        "counts": rebuild_media_recovery_counts(rows),
        "items": rows,
    }


def _validate_record(
    mapping_key: object,
    record: object,
) -> tuple[str, frozenset[int]]:
    if not isinstance(mapping_key, str) or not mapping_key:
        raise ValueError("Discord media audit logical key is invalid")
    _require_utf8(mapping_key, "logical key")
    if not isinstance(record, Mapping):
        raise ValueError("Discord media audit record is invalid")
    logical_key = record.get("logical_key")
    if logical_key != mapping_key:
        raise ValueError("Discord media audit record logical key mismatch")
    if not isinstance(logical_key, str) or not logical_key:
        raise ValueError("Discord media audit record logical key is invalid")
    _validate_candidate_url(record.get("url"))
    status = record.get("status")
    terminal_reason = record.get("terminal_reason")
    _validate_status_reason(
        status,
        terminal_reason,
        allowed=_RECORD_STATUSES,
        record_context=True,
    )
    record_failure_detail = record.get("failure_detail")
    if record_failure_detail is not None:
        _validate_failure_detail(terminal_reason, record_failure_detail)
    _validate_evidence(
        status=status,
        actual_bytes=record.get("actual_bytes"),
        sha256=record.get("sha256"),
        blob_path=record.get("blob_path"),
    )
    attempts = record.get("attempt_history")
    if not isinstance(attempts, list):
        raise ValueError("Discord media audit attempt history is invalid")
    reclassified_zero_attempts = reclassified_zero_byte_attempt_numbers(record)
    for attempt_number, attempt in enumerate(attempts, start=1):
        _validate_attempt(
            attempt,
            reclassified_zero=attempt_number in reclassified_zero_attempts,
        )
    validate_media_record_attempt_consistency(record)
    return logical_key, reclassified_zero_attempts


def _validate_attempt(
    attempt: object,
    *,
    reclassified_zero: bool,
) -> None:
    if not isinstance(attempt, Mapping):
        raise ValueError("Discord media audit attempt is invalid")
    _validate_candidate_url(attempt.get("url"))
    status = attempt.get("status")
    terminal_reason = attempt.get("terminal_reason")
    _validate_status_reason(status, terminal_reason, allowed=_ATTEMPT_STATUSES)
    retry_trigger = attempt.get("retry_trigger")
    if retry_trigger is not None and (
        not isinstance(retry_trigger, str) or retry_trigger not in _RETRY_TRIGGERS
    ):
        raise ValueError("Discord media audit retry trigger is invalid")
    failure_detail = attempt.get("failure_detail")
    _validate_failure_detail(terminal_reason, failure_detail)
    if retry_trigger in _RETRY_TRIGGERS and status in {"in_progress", "interrupted"}:
        expected_reason = None if status == "in_progress" else "interrupted"
        if terminal_reason != expected_reason or failure_detail is not None:
            raise ValueError("Discord media audit pending retry evidence is invalid")
    _validate_evidence(
        status=status,
        actual_bytes=attempt.get("actual_bytes"),
        sha256=attempt.get("sha256"),
        blob_path=attempt.get("blob_path"),
        allow_reclassified_zero=reclassified_zero,
    )


def _validate_status_reason(
    status: object,
    terminal_reason: object,
    *,
    allowed: frozenset[str],
    record_context: bool = False,
) -> None:
    if not isinstance(status, str) or status not in allowed:
        raise ValueError("Discord media audit status is invalid")
    if terminal_reason is not None and (
        not isinstance(terminal_reason, str) or not terminal_reason
    ):
        raise ValueError("Discord media audit terminal reason is invalid")
    if status == "in_progress":
        allowed_reasons = {None, "interrupted"}
        if record_context:
            allowed_reasons.add("candidate_urls_changed")
        if terminal_reason not in allowed_reasons:
            raise ValueError("Discord media audit in-progress reason is invalid")
    elif status == "not_requested":
        if not record_context or terminal_reason != "asset_download_disabled":
            raise ValueError("Discord media audit not-requested reason is invalid")
    elif status == "interrupted":
        if terminal_reason != "interrupted":
            raise ValueError("Discord media audit interrupted reason is invalid")
    elif terminal_reason is None:
        raise ValueError("Discord media audit terminal outcome has no reason")
    if terminal_reason in _RESOLUTION_TERMINAL_REASONS and status != "failed":
        raise ValueError("Discord media audit resolution outcome status is invalid")


def _validate_failure_detail(
    terminal_reason: object,
    failure_detail: object,
) -> None:
    if failure_detail is not None and not isinstance(failure_detail, str):
        raise ValueError("Discord media audit failure detail is invalid")
    expected: frozenset[str]
    if terminal_reason in {
        "media_resolution_failed_transient",
        "media_resolution_retry_exhausted",
    }:
        expected = TRANSIENT_RESOLUTION_DETAILS
    elif terminal_reason == "media_resolution_unresolved":
        expected = UNRESOLVED_RESOLUTION_DETAILS
    elif terminal_reason == "media_resolution_invalid_answer":
        expected = frozenset({"resolver_invalid_answer"})
    else:
        expected = frozenset()
    if expected:
        if failure_detail not in expected:
            raise ValueError("Discord media audit failure taxonomy is invalid")
    elif failure_detail is not None:
        raise ValueError("Discord media audit contains unstable failure detail")


def _validate_evidence(
    *,
    status: object,
    actual_bytes: object,
    sha256: object,
    blob_path: object,
    allow_reclassified_zero: bool = False,
) -> None:
    if (
        isinstance(actual_bytes, bool)
        or not isinstance(actual_bytes, int)
        or actual_bytes < 0
    ):
        raise ValueError("Discord media audit byte count is invalid")
    paired_identity = sha256 is not None or blob_path is not None
    if paired_identity and not _is_valid_blob_identity(sha256, blob_path):
        raise ValueError("Discord media audit blob identity is invalid")
    if status in _BINARY_STATUSES and not (
        actual_bytes > 0 and paired_identity
    ) and not (
        allow_reclassified_zero
        and status == "complete"
        and actual_bytes == 0
        and paired_identity
    ):
        raise ValueError("Discord media audit captured binary evidence is invalid")


def _validate_candidate_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Discord media audit candidate URL is invalid")
    _require_utf8(value, "candidate URL")
    return value


def _attempt_selected(
    attempt: Mapping[str, object],
    *,
    evidence_reclassification: object,
) -> bool:
    retry_trigger = attempt.get("retry_trigger")
    terminal_reason = attempt.get("terminal_reason")
    return (
        evidence_reclassification == LEGACY_ZERO_BYTE_RECLASSIFICATION_KIND
        or
        (
            isinstance(retry_trigger, str)
            and retry_trigger in _RETRY_TRIGGERS
        )
        or (
            isinstance(terminal_reason, str)
            and (
                terminal_reason in _RESOLUTION_TERMINAL_REASONS
                or terminal_reason in _HTTP_COMPENSATION_REASONS
            )
        )
    )


def _build_row(
    *,
    item_kind: str,
    logical_key: str,
    candidate_url: object,
    attempt_number: int | None,
    retry_trigger: object,
    evidence_reclassification: object,
    status: object,
    terminal_reason: object,
    failure_detail: object,
    actual_bytes: object,
    sha256: object,
    blob_path: object,
    final_record_status: object,
    final_record_terminal_reason: object,
) -> dict[str, object]:
    exact_url = _validate_candidate_url(candidate_url)
    binary_captured = _is_binary_captured(
        status=status,
        actual_bytes=actual_bytes,
        sha256=sha256,
        blob_path=blob_path,
    )
    identity: dict[str, object] = {
        "item_kind": item_kind,
        "logical_key": logical_key,
    }
    if item_kind == "attempt":
        identity["attempt_number"] = attempt_number
    row_id = hashlib.sha256(
        canonical_media_recovery_audit_bytes(identity, newline=False)
    ).hexdigest()
    disposition = _disposition(
        item_kind=item_kind,
        retry_trigger=retry_trigger,
        evidence_reclassification=evidence_reclassification,
        status=status,
        terminal_reason=terminal_reason,
        failure_detail=failure_detail,
        binary_captured=binary_captured,
        final_record_status=final_record_status,
    )
    return {
        "row_id": row_id,
        "item_kind": item_kind,
        "logical_key": logical_key,
        "candidate_url_sha256": hashlib.sha256(exact_url.encode("utf-8")).hexdigest(),
        "candidate_host": _safe_candidate_host(exact_url),
        "attempt_number": attempt_number,
        "retry_trigger": retry_trigger,
        "evidence_reclassification": evidence_reclassification,
        "status": status,
        "terminal_reason": terminal_reason,
        "failure_detail": failure_detail,
        "actual_bytes": actual_bytes,
        "binary_captured": binary_captured,
        "final_record_status": final_record_status,
        "final_record_terminal_reason": final_record_terminal_reason,
        "disposition": disposition,
    }


def _is_binary_captured(
    *,
    status: object,
    actual_bytes: object,
    sha256: object,
    blob_path: object,
) -> bool:
    return bool(
        status in _BINARY_STATUSES
        and isinstance(actual_bytes, int)
        and not isinstance(actual_bytes, bool)
        and actual_bytes > 0
        and isinstance(sha256, str)
        and _SHA256_PATTERN.fullmatch(sha256) is not None
        and _is_valid_blob_identity(sha256, blob_path)
    )


def _safe_candidate_host(candidate_url: str) -> str | None:
    try:
        parsed = urlsplit(candidate_url)
        host = parsed.hostname
        parsed.port
    except (TypeError, ValueError, UnicodeError):
        return None
    if not isinstance(host, str) or not host:
        return None
    canonical = host.lower()
    if canonical.endswith("."):
        canonical = canonical[:-1]
    if not canonical or canonical.endswith("."):
        return None
    if not _is_safe_host_syntax(canonical):
        return None
    return canonical


def _is_safe_host_syntax(canonical: str) -> bool:
    if "%" in canonical:
        return False
    try:
        ipaddress.ip_address(canonical)
    except ValueError:
        try:
            canonical.encode("ascii")
        except UnicodeError:
            return False
        if len(canonical) > 253:
            return False
        labels = canonical.split(".")
        if any(_HOST_LABEL_PATTERN.fullmatch(label) is None for label in labels):
            return False
    return True


def _is_valid_blob_identity(sha256: object, blob_path: object) -> bool:
    if (
        not isinstance(sha256, str)
        or _SHA256_PATTERN.fullmatch(sha256) is None
        or not isinstance(blob_path, str)
        or not blob_path
        or "\\" in blob_path
    ):
        return False
    try:
        blob_path.encode("utf-8")
    except UnicodeError:
        return False
    raw_parts = blob_path.split("/")
    relative = PurePosixPath(blob_path)
    parts = relative.parts
    if (
        relative.is_absolute()
        or len(raw_parts) != 4
        or any(part in {"", ".", ".."} for part in raw_parts)
        or len(parts) != 4
        or parts[:2] != ("assets", "sha256")
        or parts[2] != sha256[:2]
    ):
        return False
    filename_prefix = sha256 + "."
    filename = parts[3]
    return (
        filename.startswith(filename_prefix)
        and _BLOB_EXTENSION_PATTERN.fullmatch(filename[len(filename_prefix) :])
        is not None
    )


def _disposition(
    *,
    item_kind: object,
    retry_trigger: object,
    evidence_reclassification: object,
    status: object,
    terminal_reason: object,
    failure_detail: object,
    binary_captured: object,
    final_record_status: object,
) -> str:
    if (
        item_kind == "attempt"
        and evidence_reclassification
        == LEGACY_ZERO_BYTE_RECLASSIFICATION_KIND
        and status == "complete"
        and not binary_captured
    ):
        return "legacy_zero_byte_reclassified_not_binary"
    if binary_captured and status == "complete":
        return "binary_captured"
    if binary_captured and status == "captured_with_warning":
        return "captured_with_warning"
    if status == "reference_only":
        return "reference_only_not_binary"
    if (
        item_kind == "attempt"
        and status == "failed"
        and final_record_status in _COVERED_STATUSES
    ):
        return "candidate_failed_record_covered"
    if (
        item_kind == "attempt"
        and retry_trigger in _RETRY_TRIGGERS
        and failure_detail is None
        and (
            (status == "in_progress" and terminal_reason is None)
            or (status == "interrupted" and terminal_reason == "interrupted")
        )
    ):
        return "resolution_retry_pending"
    if terminal_reason == "media_resolution_failed_transient":
        return "resolution_retry_pending"
    if terminal_reason == "media_resolution_retry_exhausted":
        return "resolution_retry_exhausted_blocker"
    if terminal_reason == "media_resolution_unresolved":
        return "resolution_unresolved_blocker"
    if terminal_reason == "media_resolution_invalid_answer":
        return "resolution_invalid_answer_blocker"
    if terminal_reason == "unsafe_media_url":
        return "unsafe_blocker"
    if terminal_reason in _HTTP_COMPENSATION_REASONS:
        return "http_compensation_blocker"
    if terminal_reason in _HARD_MEDIA_FAILURE_REASONS:
        return "hard_media_failure_blocker"
    if status == "failed":
        return "other_media_failure_blocker"
    raise ValueError("Discord media audit outcome has no disposition")


def _append_unique_row(
    rows: list[dict[str, object]],
    row_ids: set[str],
    row: dict[str, object],
) -> None:
    row_id = row["row_id"]
    assert isinstance(row_id, str)
    if row_id in row_ids:
        raise ValueError("Discord media audit row ID is duplicated")
    row_ids.add(row_id)
    rows.append(row)


def _row_sort_key(row: Mapping[str, object]) -> tuple[bytes, int, int]:
    logical_key = row["logical_key"]
    item_kind = row["item_kind"]
    attempt_number = row["attempt_number"]
    assert isinstance(logical_key, str)
    return (
        logical_key.encode("utf-8"),
        0 if item_kind == "attempt" else 1,
        attempt_number if isinstance(attempt_number, int) else 0,
    )


def _validate_row(row: object) -> None:
    if not isinstance(row, Mapping) or set(row) != _ROW_FIELDS:
        raise ValueError("Discord media audit row fields are invalid")
    logical_key = row["logical_key"]
    item_kind = row["item_kind"]
    attempt_number = row["attempt_number"]
    if not isinstance(logical_key, str) or not logical_key:
        raise ValueError("Discord media audit row logical key is invalid")
    if not isinstance(item_kind, str) or item_kind not in {"attempt", "record"}:
        raise ValueError("Discord media audit row kind is invalid")
    if item_kind == "attempt":
        if (
            isinstance(attempt_number, bool)
            or not isinstance(attempt_number, int)
            or attempt_number < 1
        ):
            raise ValueError("Discord media audit attempt number is invalid")
        identity = {
            "item_kind": "attempt",
            "logical_key": logical_key,
            "attempt_number": attempt_number,
        }
    else:
        if (
            attempt_number is not None
            or row["retry_trigger"] is not None
            or row["evidence_reclassification"] is not None
            or row["failure_detail"] is not None
        ):
            raise ValueError("Discord media audit record row fields are invalid")
        identity = {"item_kind": "record", "logical_key": logical_key}
    expected_id = hashlib.sha256(
        canonical_media_recovery_audit_bytes(identity, newline=False)
    ).hexdigest()
    if row["row_id"] != expected_id:
        raise ValueError("Discord media audit row identity is invalid")
    _validate_sha256(row["candidate_url_sha256"], "candidate URL")
    candidate_host = row["candidate_host"]
    if candidate_host is not None and (
        not isinstance(candidate_host, str)
        or not candidate_host
        or candidate_host != candidate_host.lower()
        or candidate_host.endswith(".")
        or not _is_safe_host_syntax(candidate_host)
    ):
        raise ValueError("Discord media audit candidate host is invalid")
    status = row["status"]
    terminal_reason = row["terminal_reason"]
    _validate_status_reason(
        status,
        terminal_reason,
        allowed=_ATTEMPT_STATUSES if item_kind == "attempt" else _RECORD_STATUSES,
        record_context=item_kind == "record",
    )
    retry_trigger = row["retry_trigger"]
    if retry_trigger is not None and (
        not isinstance(retry_trigger, str) or retry_trigger not in _RETRY_TRIGGERS
    ):
        raise ValueError("Discord media audit row retry trigger is invalid")
    evidence_reclassification = row["evidence_reclassification"]
    if evidence_reclassification not in {
        None,
        LEGACY_ZERO_BYTE_RECLASSIFICATION_KIND,
    }:
        raise ValueError("Discord media audit evidence reclassification is invalid")
    if item_kind == "attempt":
        _validate_failure_detail(terminal_reason, row["failure_detail"])
    actual_bytes = row["actual_bytes"]
    if (
        isinstance(actual_bytes, bool)
        or not isinstance(actual_bytes, int)
        or actual_bytes < 0
    ):
        raise ValueError("Discord media audit row byte count is invalid")
    binary_captured = row["binary_captured"]
    if not isinstance(binary_captured, bool):
        raise ValueError("Discord media audit binary flag is invalid")
    if binary_captured != (
        status in _BINARY_STATUSES and actual_bytes > 0
    ):
        raise ValueError("Discord media audit binary flag conflicts with evidence")
    if evidence_reclassification is not None and (
        item_kind != "attempt"
        or status != "complete"
        or actual_bytes != 0
        or binary_captured
    ):
        raise ValueError("Discord media audit reclassified evidence is invalid")
    final_status = row["final_record_status"]
    final_reason = row["final_record_terminal_reason"]
    _validate_status_reason(
        final_status,
        final_reason,
        allowed=_RECORD_STATUSES,
        record_context=True,
    )
    if item_kind == "record" and (
        status not in {"failed", "reference_only"}
        or final_status != status
        or final_reason != terminal_reason
    ):
        raise ValueError("Discord media audit record row outcome is invalid")
    if item_kind == "attempt" and not (
        retry_trigger in _RETRY_TRIGGERS
        or evidence_reclassification == LEGACY_ZERO_BYTE_RECLASSIFICATION_KIND
        or terminal_reason in _RESOLUTION_TERMINAL_REASONS
        or terminal_reason in _HTTP_COMPENSATION_REASONS
    ):
        raise ValueError("Discord media audit attempt row is outside the audit union")
    expected_disposition = _disposition(
        item_kind=item_kind,
        retry_trigger=retry_trigger,
        evidence_reclassification=evidence_reclassification,
        status=status,
        terminal_reason=terminal_reason,
        failure_detail=row["failure_detail"],
        binary_captured=binary_captured,
        final_record_status=final_status,
    )
    if row["disposition"] != expected_disposition:
        raise ValueError("Discord media audit row disposition is invalid")


def _validate_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"Discord media audit {label} hash is invalid")


def _require_utf8(value: str, label: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"Discord media audit {label} is not valid UTF-8") from exc
