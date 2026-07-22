"""Verified, immutable publication of local Discord reply resolutions.

The artifact is derived from raw pages plus their deterministic message-evidence
rows.  It intentionally contains only Discord IDs, hashes, reason codes, and
counts; message bodies and URLs never enter the model.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any, Mapping

from .discord_message_evidence import extract_message_evidence
from .discord_reference_resolution import resolve_local_references


MESSAGE_REFERENCE_RESOLUTION_AUDIT_VERSION = 1
MESSAGE_REFERENCE_RESOLUTION_AUDIT_KIND = (
    "discord_message_reference_resolution_audit"
)
MESSAGE_REFERENCE_RESOLUTION_AUDIT_DIRECTORY = (
    "message-reference-resolution-audits"
)
MESSAGE_REFERENCE_RESOLUTION_MAX_DEPTH = 8

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MESSAGE_STREAM = re.compile(r"^(messages|pins)_([0-9]+)$")
_SIDECAR_PATH = re.compile(
    r"^message-reference-resolution-audits/([0-9a-f]{64})\.json$"
)
_DESCRIPTOR_FIELDS = frozenset({"version", "path", "sha256", "counts"})
_EVIDENCE_DESCRIPTOR_V1_FIELDS = frozenset(
    {
        "schema_version",
        "path",
        "sha256",
        "raw_page_path",
        "raw_page_sha256",
        "root_messages",
        "partial_messages",
        "nodes",
        "media_occurrences",
        "references",
        "diagnostics",
    }
)
_EVIDENCE_DESCRIPTOR_V2_FIELDS = _EVIDENCE_DESCRIPTOR_V1_FIELDS | frozenset(
    {
        "stream",
        "channel_id",
        "page_number",
        "fetched_at",
        "diagnostics_by_severity",
        "pin_events",
    }
)


def build_message_reference_resolution_audit(
    *,
    run_root: str | os.PathLike[str],
    checkpoint: Mapping[str, Any],
    run_id: str,
    request_sha256: str,
    max_depth: int = 8,
    _anchor: "_RootAnchor | None" = None,
) -> dict[str, Any]:
    """Rebuild a deterministic resolution audit from verified local evidence."""

    if not isinstance(run_id, str) or not run_id:
        raise ValueError("Discord reference audit run identity is invalid")
    if max_depth != MESSAGE_REFERENCE_RESOLUTION_MAX_DEPTH:
        raise ValueError("Discord reference audit max_depth is invalid")
    if not _valid_sha(request_sha256):
        raise ValueError("Discord reference audit request hash is invalid")
    if not isinstance(checkpoint, Mapping):
        raise ValueError("Discord reference audit checkpoint is invalid")
    if checkpoint.get("run_id") != run_id:
        raise ValueError("Discord reference audit checkpoint run identity differs")
    checkpoint_request_sha256 = checkpoint.get("request_sha256")
    if (
        not _valid_sha(checkpoint_request_sha256)
        or checkpoint_request_sha256 != request_sha256
    ):
        raise ValueError("Discord reference audit checkpoint request hash differs")
    streams = checkpoint.get("streams")
    if not isinstance(streams, Mapping):
        raise ValueError("Discord reference audit checkpoint streams are invalid")
    owns_anchor = _anchor is None
    anchor = _anchor or _RootAnchor.open(run_root)
    try:
        return _build_message_reference_resolution_audit_anchored(
            anchor=anchor,
            streams=streams,
            run_id=run_id,
            request_sha256=request_sha256,
            max_depth=max_depth,
        )
    finally:
        if owns_anchor:
            anchor.close()


def _build_message_reference_resolution_audit_anchored(
    *,
    anchor: "_RootAnchor",
    streams: Mapping[str, Any],
    run_id: str,
    request_sha256: str,
    max_depth: int,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    reference_row_states: list[dict[str, Any]] = []
    page_commitments: list[dict[str, Any]] = []
    root_message_count = 0
    raw_error_diagnostics = 0
    raw_reference_errors = 0
    raw_partial_messages = 0
    effective_partial_messages = 0
    raw_diagnostics_by_severity = {"error": 0, "warning": 0, "info": 0}
    raw_diagnostic_codes_by_severity: dict[str, dict[str, int]] = {
        "error": {},
        "warning": {},
        "info": {},
    }

    for stream_name in sorted(streams):
        match = _MESSAGE_STREAM.fullmatch(stream_name)
        if match is None:
            continue
        state = streams[stream_name]
        if not isinstance(state, Mapping):
            raise ValueError("Discord reference audit message stream state is invalid")
        page_hashes = state.get("page_hashes")
        page_states = state.get("page_states")
        pages = state.get("pages")
        if (
            not isinstance(page_hashes, list)
            or any(not _valid_sha(value) for value in page_hashes)
            or isinstance(pages, bool)
            or not isinstance(pages, int)
            or pages != len(page_hashes)
            or not isinstance(page_states, list)
            or len(page_states) != len(page_hashes)
        ):
            raise ValueError("Discord reference audit page ledger is invalid")
        channel_id = match.group(2)
        stream_valid_messages = 0
        stream_invalid_items = 0
        for page_number, raw_sha256 in enumerate(page_hashes, start=1):
            page_state = page_states[page_number - 1]
            descriptor = (
                page_state.get("message_evidence")
                if isinstance(page_state, Mapping)
                else None
            )
            if not isinstance(descriptor, Mapping):
                raise ValueError(
                    "Discord reference audit message evidence descriptor is missing"
                )
            raw_relative = Path("pages", stream_name, f"{page_number:06d}.json")
            evidence_relative = Path(
                "message-evidence", stream_name, f"{page_number:06d}.jsonl"
            )
            _validate_descriptor(
                descriptor,
                stream_name=stream_name,
                channel_id=channel_id,
                page_number=page_number,
                raw_relative=raw_relative,
                raw_sha256=raw_sha256,
                evidence_relative=evidence_relative,
            )
            raw_content = anchor.read_regular(raw_relative, "raw page")
            if _sha256(raw_content) != raw_sha256:
                raise ValueError("Discord reference audit raw page hash mismatch")
            try:
                raw_document = json.loads(raw_content)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Discord reference audit raw page JSON is invalid") from exc
            _validate_descriptor_acquisition(descriptor, raw_document)
            messages, invalid_items = _page_messages(
                raw_document,
                stream_name=stream_name,
                channel_id=channel_id,
            )
            stream_valid_messages += len(messages)
            stream_invalid_items += invalid_items
            expected_rows: list[dict[str, Any]] = []
            page_records: list[dict[str, Any]] = []
            for message, pointer, pin_event in messages:
                evidence = asdict(
                    extract_message_evidence(
                        message,
                        stream=stream_name,
                        evidence_path=raw_relative.as_posix(),
                        evidence_sha256=raw_sha256,
                        json_pointer=pointer,
                    )
                )
                row = _evidence_row(
                    evidence,
                    descriptor_version=descriptor["schema_version"],
                    stream_name=stream_name,
                    channel_id=channel_id,
                    page_number=page_number,
                    pointer=pointer,
                    pin_event=pin_event,
                )
                expected_rows.append(row)
                message_sha256 = _canonical_sha256(message)
                evidence_sha256 = _canonical_sha256(evidence)
                diagnostics = evidence.get("diagnostics")
                if not isinstance(diagnostics, (list, tuple)):
                    raise ValueError(
                        "Discord reference audit diagnostic collection is invalid"
                    )
                for diagnostic in diagnostics:
                    severity = (
                        diagnostic.get("severity")
                        if isinstance(diagnostic, Mapping)
                        else None
                    )
                    code = (
                        diagnostic.get("code")
                        if isinstance(diagnostic, Mapping)
                        else None
                    )
                    if severity not in raw_diagnostics_by_severity or not isinstance(
                        code, str
                    ) or not code:
                        raise ValueError(
                            "Discord reference audit diagnostic is invalid"
                        )
                    raw_diagnostics_by_severity[severity] += 1
                    codes = raw_diagnostic_codes_by_severity[severity]
                    codes[code] = codes.get(code, 0) + 1
                error_count = sum(
                    isinstance(item, Mapping) and item.get("severity") == "error"
                    for item in diagnostics
                )
                reference_error_count = sum(
                    isinstance(item, Mapping)
                    and item.get("severity") == "error"
                    and item.get("code") == "referenced_message_unknown"
                    and "/message_snapshots/" not in str(item.get("json_pointer"))
                    for item in diagnostics
                )
                root_message_count += 1
                raw_error_diagnostics += error_count
                raw_reference_errors += reference_error_count
                raw_partial_messages += evidence.get("status") == "partial"
                if reference_error_count or _is_default_reply_message(message):
                    page_records.append(
                        {
                            "message": message,
                            "evidence": evidence,
                            "message_sha256": message_sha256,
                            "evidence_sha256": evidence_sha256,
                        }
                    )
                if reference_error_count:
                    reference_row_states.append(
                        {
                            "message_sha256": message_sha256,
                            "evidence_sha256": evidence_sha256,
                            "status": evidence.get("status"),
                            "error_count": error_count,
                            "reference_error_count": reference_error_count,
                        }
                    )
                elif evidence.get("status") == "partial":
                    effective_partial_messages += 1
            expected_content = b"".join(
                _canonical_json_bytes(row) for row in expected_rows
            )
            evidence_content = anchor.read_regular(
                evidence_relative,
                "message evidence",
            )
            if _sha256(evidence_content) != descriptor.get("sha256"):
                raise ValueError("Discord reference audit evidence hash mismatch")
            if evidence_content != expected_content:
                raise ValueError(
                    "Discord reference audit evidence differs from raw extraction"
                )
            _validate_descriptor_counts(descriptor, expected_rows)
            records.extend(page_records)
            page_commitments.append(
                {
                    "stream": stream_name,
                    "page_number": page_number,
                    "raw_page_sha256": raw_sha256,
                    "message_evidence_sha256": descriptor["sha256"],
                }
            )
        _validate_item_validation_state(
            streams.get(f"{stream_name}_item_validation"),
            valid_items=stream_valid_messages,
            invalid_items=stream_invalid_items,
        )

    core = resolve_local_references(records, max_depth=max_depth).to_mapping()
    core_counts = core.get("counts")
    edges = core.get("edges")
    if not isinstance(core_counts, dict) or not isinstance(edges, list):
        raise ValueError("Discord reference audit resolution output is invalid")
    resolved_by_row: dict[tuple[str, str], list[str]] = {}
    for edge in edges:
        if not isinstance(edge, Mapping):
            raise ValueError("Discord reference audit resolution edge is invalid")
        outcome = edge.get("outcome")
        occurrences = edge.get("occurrences")
        if outcome not in {"local_resolved", "deleted", "unresolved"} or not isinstance(
            occurrences, list
        ):
            raise ValueError("Discord reference audit resolution edge is invalid")
        for occurrence in occurrences:
            if not isinstance(occurrence, Mapping):
                raise ValueError(
                    "Discord reference audit resolution occurrence is invalid"
                )
            key = (
                str(occurrence.get("root_message_sha256")),
                str(occurrence.get("evidence_sha256")),
            )
            resolved_by_row.setdefault(key, []).append(str(outcome))

    if raw_reference_errors != core_counts.get("raw_errors"):
        raise ValueError(
            "Discord reference audit raw diagnostics do not match resolution edges"
        )
    for state in reference_row_states:
        key = (str(state["message_sha256"]), str(state["evidence_sha256"]))
        outcomes = resolved_by_row.get(key, [])
        if len(outcomes) != state["reference_error_count"]:
            raise ValueError(
                "Discord reference audit row diagnostics do not match occurrences"
            )
        effective_row_errors = (
            int(state["error_count"])
            - int(state["reference_error_count"])
            + sum(outcome == "unresolved" for outcome in outcomes)
        )
        if effective_row_errors < 0:
            raise ValueError("Discord reference audit effective row count is invalid")
        if state["status"] == "partial" and (
            effective_row_errors > 0 or int(state["error_count"]) == 0
        ):
            effective_partial_messages += 1

    unresolved = core_counts.get("unresolved")
    if isinstance(unresolved, bool) or not isinstance(unresolved, int):
        raise ValueError("Discord reference audit unresolved count is invalid")
    non_reference_errors = raw_error_diagnostics - raw_reference_errors
    if non_reference_errors < 0:
        raise ValueError("Discord reference audit diagnostic counts are invalid")
    counts = {
        **deepcopy(core_counts),
        "raw_error_diagnostics": raw_error_diagnostics,
        "non_reference_error_diagnostics": non_reference_errors,
        "effective_error_diagnostics": non_reference_errors + unresolved,
        "raw_partial_messages": raw_partial_messages,
        "effective_partial_messages": effective_partial_messages,
        "raw_diagnostics_by_severity": deepcopy(raw_diagnostics_by_severity),
        "effective_diagnostics_by_severity": {
            **deepcopy(raw_diagnostics_by_severity),
            "error": non_reference_errors + unresolved,
        },
        "raw_diagnostic_codes_by_severity": deepcopy(
            raw_diagnostic_codes_by_severity
        ),
        "effective_diagnostic_codes_by_severity": _effective_diagnostic_codes(
            raw_diagnostic_codes_by_severity,
            resolved_reference_errors=raw_reference_errors - unresolved,
        ),
    }
    anchor.verify_root_binding()
    return {
        "schema_version": MESSAGE_REFERENCE_RESOLUTION_AUDIT_VERSION,
        "kind": MESSAGE_REFERENCE_RESOLUTION_AUDIT_KIND,
        "run_id": run_id,
        "request_sha256": request_sha256,
        "max_depth": max_depth,
        "source": {
            "page_count": len(page_commitments),
            "root_messages": root_message_count,
            "resolution_input_messages": len(records),
            "page_commitments_sha256": _canonical_sha256(page_commitments),
        },
        "counts": counts,
        "edges": deepcopy(edges),
    }


def canonical_message_reference_resolution_audit_bytes(
    audit: Mapping[str, Any],
) -> bytes:
    if not isinstance(audit, Mapping):
        raise TypeError("Discord message reference resolution audit is required")
    return _canonical_json_bytes(_strict_json_copy(audit))


def publish_message_reference_resolution_audit(
    *,
    run_root: str | os.PathLike[str],
    audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Atomically publish one content-addressed, no-clobber sidecar."""

    content = canonical_message_reference_resolution_audit_bytes(audit)
    digest = _sha256(content)
    relative = Path(
        MESSAGE_REFERENCE_RESOLUTION_AUDIT_DIRECTORY,
        f"{digest}.json",
    )
    anchor = _RootAnchor.open(run_root)
    try:
        anchor.write_exclusive_or_same(relative, content)
    finally:
        anchor.close()
    counts = audit.get("counts")
    if not isinstance(counts, Mapping):
        raise ValueError("Discord reference audit counts are invalid")
    return {
        "version": MESSAGE_REFERENCE_RESOLUTION_AUDIT_VERSION,
        "path": relative.as_posix(),
        "sha256": digest,
        "counts": _strict_json_copy(counts),
    }


def verify_published_message_reference_resolution_audit(
    *,
    run_root: str | os.PathLike[str],
    checkpoint: Mapping[str, Any],
    run_id: str,
    request_sha256: str,
    descriptor: object,
) -> dict[str, Any]:
    """Independently rebuild and bind a published sidecar."""

    if not isinstance(descriptor, Mapping) or set(descriptor) != _DESCRIPTOR_FIELDS:
        raise ValueError("Discord reference audit descriptor is invalid")
    path_value = descriptor.get("path")
    match = _SIDECAR_PATH.fullmatch(path_value) if isinstance(path_value, str) else None
    if (
        descriptor.get("version") != MESSAGE_REFERENCE_RESOLUTION_AUDIT_VERSION
        or match is None
        or not _valid_sha(descriptor.get("sha256"))
        or match.group(1) != descriptor.get("sha256")
        or not isinstance(descriptor.get("counts"), Mapping)
    ):
        raise ValueError("Discord reference audit descriptor identity is invalid")
    anchor = _RootAnchor.open(run_root)
    try:
        content = anchor.read_regular(Path(path_value), "reference audit")
        if _sha256(content) != descriptor["sha256"]:
            raise ValueError("Discord reference audit artifact hash mismatch")
        try:
            published = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Discord reference audit artifact JSON is invalid") from exc
        if content != canonical_message_reference_resolution_audit_bytes(published):
            raise ValueError("Discord reference audit artifact is not canonical")
        if (
            not isinstance(published, dict)
            or published.get("max_depth")
            != MESSAGE_REFERENCE_RESOLUTION_MAX_DEPTH
        ):
            raise ValueError("Discord reference audit max_depth is invalid")
        rebuilt = build_message_reference_resolution_audit(
            run_root=run_root,
            checkpoint=checkpoint,
            run_id=run_id,
            request_sha256=request_sha256,
            _anchor=anchor,
        )
        if published != rebuilt:
            raise ValueError("Discord reference audit differs from verified evidence")
        if descriptor["counts"] != rebuilt["counts"]:
            raise ValueError("Discord reference audit descriptor counts differ")
        anchor.verify_root_binding()
        return {
            "verified": True,
            "sha256": descriptor["sha256"],
            "counts": deepcopy(rebuilt["counts"]),
        }
    finally:
        anchor.close()


def _validate_descriptor(
    descriptor: Mapping[str, Any],
    *,
    stream_name: str,
    channel_id: str,
    page_number: int,
    raw_relative: Path,
    raw_sha256: str,
    evidence_relative: Path,
) -> None:
    version = descriptor.get("schema_version")
    if version not in {1, 2}:
        raise ValueError("Discord reference audit evidence schema is unsupported")
    expected_fields = (
        _EVIDENCE_DESCRIPTOR_V2_FIELDS
        if version == 2
        else _EVIDENCE_DESCRIPTOR_V1_FIELDS
    )
    if set(descriptor) != expected_fields:
        raise ValueError("Discord reference audit evidence descriptor fields differ")
    if (
        descriptor.get("path") != evidence_relative.as_posix()
        or not _valid_sha(descriptor.get("sha256"))
        or descriptor.get("raw_page_path") != raw_relative.as_posix()
        or descriptor.get("raw_page_sha256") != raw_sha256
    ):
        raise ValueError("Discord reference audit evidence descriptor identity differs")
    if version == 2 and (
        descriptor.get("stream") != stream_name
        or descriptor.get("channel_id") != channel_id
        or descriptor.get("page_number") != page_number
    ):
        raise ValueError("Discord reference audit evidence descriptor scope differs")


def _validate_descriptor_acquisition(
    descriptor: Mapping[str, Any],
    raw_document: object,
) -> None:
    if descriptor.get("schema_version") != 2:
        return
    acquisition = raw_document.get("acquisition") if isinstance(raw_document, dict) else None
    if (
        not isinstance(acquisition, Mapping)
        or set(acquisition) != {"fetched_at", "source"}
        or acquisition.get("source") != "collector_local_clock_after_response"
        or descriptor.get("fetched_at") != acquisition.get("fetched_at")
        or not isinstance(acquisition.get("fetched_at"), str)
    ):
        raise ValueError("Discord reference audit evidence acquisition differs")
    try:
        parsed = datetime.fromisoformat(
            str(acquisition["fetched_at"]).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("Discord reference audit evidence acquisition is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Discord reference audit evidence acquisition is invalid")


def _validate_descriptor_counts(
    descriptor: Mapping[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    counts = {
        "root_messages": len(rows),
        "partial_messages": sum(row.get("status") == "partial" for row in rows),
        "nodes": sum(len(row.get("nodes", [])) for row in rows),
        "media_occurrences": sum(len(row.get("media", [])) for row in rows),
        "references": sum(len(row.get("references", [])) for row in rows),
        "diagnostics": sum(len(row.get("diagnostics", [])) for row in rows),
    }
    for key, value in counts.items():
        if descriptor.get(key) != value:
            raise ValueError("Discord reference audit evidence descriptor counts differ")
    if descriptor.get("schema_version") == 2:
        severity = {"error": 0, "warning": 0, "info": 0}
        pin_events = 0
        for row in rows:
            pin_events += "pin_event" in row
            for diagnostic in row.get("diagnostics", []):
                level = diagnostic.get("severity") if isinstance(diagnostic, Mapping) else None
                if level not in severity:
                    raise ValueError("Discord reference audit diagnostic severity is invalid")
                severity[level] += 1
        if (
            descriptor.get("diagnostics_by_severity") != severity
            or descriptor.get("pin_events") != pin_events
        ):
            raise ValueError("Discord reference audit evidence descriptor totals differ")


def _page_messages(
    document: object,
    *,
    stream_name: str,
    channel_id: str,
) -> tuple[list[tuple[dict[str, Any], str, dict[str, Any] | None]], int]:
    if not isinstance(document, dict):
        raise ValueError("Discord reference audit raw page envelope is invalid")
    payload = document.get("payload")
    result: list[tuple[dict[str, Any], str, dict[str, Any] | None]] = []
    invalid_items = 0
    if stream_name.startswith("messages_"):
        if not isinstance(payload, list):
            raise ValueError("Discord reference audit message page payload is invalid")
        for index, message in enumerate(payload):
            if not _valid_message(message, channel_id):
                invalid_items += 1
                continue
            assert isinstance(message, dict)
            result.append((message, f"/payload/{index}", None))
        return result, invalid_items
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("Discord reference audit pin page payload is invalid")
    for index, item in enumerate(items):
        message = item.get("message") if isinstance(item, dict) else None
        pinned_at = item.get("pinned_at") if isinstance(item, dict) else None
        if not _valid_message(message, channel_id) or not isinstance(
            pinned_at,
            str,
        ):
            invalid_items += 1
            continue
        try:
            parsed = datetime.fromisoformat(pinned_at.replace("Z", "+00:00"))
        except ValueError:
            invalid_items += 1
            continue
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            invalid_items += 1
            continue
        assert isinstance(message, dict)
        pinned_at_utc = parsed.astimezone(UTC).isoformat()
        result.append(
            (
                message,
                f"/payload/items/{index}/message",
                {
                    "event_key": (
                        f"pin_event:{channel_id}:{message['id']}:{pinned_at_utc}"
                    ),
                    "channel_id": channel_id,
                    "message_id": message["id"],
                    "pinned_at": pinned_at,
                    "pinned_at_utc": pinned_at_utc,
                    "json_pointer": f"/payload/items/{index}",
                },
            )
        )
    return result, invalid_items


def _evidence_row(
    evidence: dict[str, Any],
    *,
    descriptor_version: int,
    stream_name: str,
    channel_id: str,
    page_number: int,
    pointer: str,
    pin_event: dict[str, Any] | None,
) -> dict[str, Any]:
    if descriptor_version == 1:
        return {"schema_version": 1, **deepcopy(evidence)}
    row = {
        "schema_version": 2,
        "stream": stream_name,
        "channel_id": channel_id,
        "page_number": page_number,
        "message_json_pointer": pointer,
        **deepcopy(evidence),
    }
    if pin_event is not None:
        row["pin_event"] = deepcopy(pin_event)
    return row


def _valid_message(message: object, channel_id: str) -> bool:
    return bool(
        isinstance(message, dict)
        and _valid_snowflake(message.get("id"))
        and message.get("channel_id") == channel_id
    )


def _is_default_reply_message(message: Mapping[str, Any]) -> bool:
    reference = message.get("message_reference")
    if not isinstance(reference, Mapping):
        return False
    message_type = message.get("type")
    reference_type = reference.get("type", 0)
    return type(message_type) is int and message_type == 19 and (
        type(reference_type) is int and reference_type == 0
    )


def _validate_item_validation_state(
    value: object,
    *,
    valid_items: int,
    invalid_items: int,
) -> None:
    if value is None and invalid_items == 0:
        return
    if not isinstance(value, Mapping):
        raise ValueError("Discord reference audit item validation state is missing")
    expected_status = "failed" if invalid_items else "complete"
    if (
        value.get("status") != expected_status
        or value.get("valid_items") != valid_items
        or value.get("invalid_items") != invalid_items
    ):
        raise ValueError("Discord reference audit item validation counts differ")


def _effective_diagnostic_codes(
    raw: Mapping[str, Mapping[str, int]],
    *,
    resolved_reference_errors: int,
) -> dict[str, dict[str, int]]:
    effective = deepcopy(dict(raw))
    errors = effective.get("error")
    if not isinstance(errors, dict):
        raise ValueError("Discord reference audit diagnostic code counts are invalid")
    unknown = errors.get("referenced_message_unknown", 0)
    if (
        isinstance(unknown, bool)
        or not isinstance(unknown, int)
        or unknown < resolved_reference_errors
    ):
        raise ValueError("Discord reference audit diagnostic code counts are invalid")
    remaining = unknown - resolved_reference_errors
    if remaining:
        errors["referenced_message_unknown"] = remaining
    else:
        errors.pop("referenced_message_unknown", None)
    return effective


class _DirectoryChain:
    def __init__(
        self,
        anchor: "_RootAnchor",
        fds: list[int],
        links: list[tuple[int, str, tuple[int, int]]],
    ) -> None:
        self._anchor = anchor
        self._fds = fds
        self._links = links

    @property
    def fd(self) -> int:
        return self._fds[-1] if self._fds else self._anchor.fd

    def verify(self) -> None:
        self._anchor.verify_root_binding()
        for parent_fd, name, identity in self._links:
            try:
                current = os.stat(
                    name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise ValueError(
                    "Discord immutable reference audit publication changed"
                ) from exc
            if not stat.S_ISDIR(current.st_mode) or _identity(current) != identity:
                raise ValueError(
                    "Discord immutable reference audit publication changed"
                )

    def close(self) -> None:
        first_error: OSError | None = None
        while self._fds:
            descriptor = self._fds.pop()
            try:
                os.close(descriptor)
            except OSError as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def __enter__(self) -> "_DirectoryChain":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class _RootAnchor:
    def __init__(self, path: Path, descriptor: int, identity: tuple[int, int]) -> None:
        self.path = path
        self.fd = descriptor
        self.identity = identity
        self._closed = False

    @classmethod
    def open(cls, value: str | os.PathLike[str]) -> "_RootAnchor":
        path = Path(value).absolute()
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise ValueError("Discord reference audit run root is unavailable") from exc
        try:
            opened = os.fstat(descriptor)
            named = os.stat(path, follow_symlinks=False)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or not stat.S_ISDIR(named.st_mode)
                or _identity(opened) != _identity(named)
            ):
                raise ValueError("Discord reference audit run root is unsafe")
            return cls(path, descriptor, _identity(opened))
        except BaseException:
            os.close(descriptor)
            raise

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            os.close(self.fd)

    def verify_root_binding(self) -> None:
        if self._closed:
            raise ValueError("Discord reference audit run root is unavailable")
        try:
            opened = os.fstat(self.fd)
            named = os.stat(self.path, follow_symlinks=False)
        except OSError as exc:
            raise ValueError("Discord reference audit run root binding changed") from exc
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(named.st_mode)
            or _identity(opened) != self.identity
            or _identity(named) != self.identity
        ):
            raise ValueError("Discord reference audit run root binding changed")

    def directory(self, relative: Path, *, create: bool) -> _DirectoryChain:
        _validate_relative_path(relative, allow_empty=True)
        fds: list[int] = []
        links: list[tuple[int, str, tuple[int, int]]] = []
        parent_fd = self.fd
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            for part in relative.parts:
                try:
                    child_fd = os.open(part, flags, dir_fd=parent_fd)
                except FileNotFoundError:
                    if not create:
                        raise ValueError(
                            "Discord reference audit directory is missing"
                        ) from None
                    try:
                        os.mkdir(part, 0o777, dir_fd=parent_fd)
                    except FileExistsError:
                        pass
                    else:
                        os.fsync(parent_fd)
                    try:
                        child_fd = os.open(part, flags, dir_fd=parent_fd)
                    except OSError as exc:
                        raise ValueError(
                            "Discord reference audit directory cannot be opened"
                        ) from exc
                except OSError as exc:
                    raise ValueError(
                        "Discord reference audit path contains an unsafe directory"
                    ) from exc
                fds.append(child_fd)
                child_stat = os.fstat(child_fd)
                if not stat.S_ISDIR(child_stat.st_mode):
                    raise ValueError(
                        "Discord reference audit path contains an unsafe directory"
                    )
                identity = _identity(child_stat)
                links.append((parent_fd, part, identity))
                parent_fd = child_fd
            chain = _DirectoryChain(self, fds, links)
            chain.verify()
            return chain
        except BaseException:
            for descriptor in reversed(fds):
                os.close(descriptor)
            raise

    def read_regular(self, relative: Path, label: str) -> bytes:
        _validate_relative_path(relative)
        with self.directory(relative.parent, create=False) as chain:
            chain.verify()
            content = _read_regular_at(chain.fd, relative.name, label)
            chain.verify()
            return content

    def write_exclusive_or_same(self, relative: Path, content: bytes) -> None:
        _validate_relative_path(relative)
        with self.directory(relative.parent, create=True) as chain:
            chain.verify()
            existing = _read_regular_at(
                chain.fd,
                relative.name,
                "reference audit",
                missing_ok=True,
            )
            if existing is not None:
                if existing != content:
                    raise ValueError(
                        "Discord immutable reference audit content differs"
                    )
                os.fsync(chain.fd)
                chain.verify()
                final_content = _read_regular_at(
                    chain.fd,
                    relative.name,
                    "reference audit",
                )
                if final_content != content:
                    raise ValueError(
                        "Discord immutable reference audit content differs"
                    )
                chain.verify()
                return

            temporary_name = f".{relative.name}.{secrets.token_hex(12)}"
            temporary_fd: int | None = None
            temporary_created = False
            temporary_identity: tuple[int, int] | None = None
            destination_created = False
            primary_error: BaseException | None = None
            try:
                flags = (
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                temporary_fd = os.open(
                    temporary_name,
                    flags,
                    0o600,
                    dir_fd=chain.fd,
                )
                temporary_created = True
                temporary_stat = os.fstat(temporary_fd)
                if not stat.S_ISREG(temporary_stat.st_mode):
                    raise ValueError(
                        "Discord immutable reference audit temporary is unsafe"
                    )
                temporary_identity = _identity(temporary_stat)
                _write_all(temporary_fd, content)
                os.fsync(temporary_fd)
                chain.verify()
                try:
                    os.link(
                        temporary_name,
                        relative.name,
                        src_dir_fd=chain.fd,
                        dst_dir_fd=chain.fd,
                        follow_symlinks=False,
                    )
                    destination_created = True
                    existing = _read_regular_at(
                        chain.fd,
                        relative.name,
                        "reference audit",
                        expected_identity=temporary_identity,
                    )
                    if existing != content:
                        raise ValueError(
                            "Discord immutable reference audit content differs"
                        )
                except FileExistsError:
                    existing = _read_regular_at(
                        chain.fd,
                        relative.name,
                        "reference audit",
                        missing_ok=True,
                    )
                    if existing is None:
                        raise ValueError(
                            "Discord immutable reference audit publication changed"
                        ) from None
                    if existing != content:
                        raise ValueError(
                            "Discord immutable reference audit content differs"
                        ) from None
                os.fsync(chain.fd)
                chain.verify()
            except BaseException as exc:
                primary_error = exc
                raise
            finally:
                close_error: OSError | None = None
                if temporary_fd is not None:
                    if temporary_created and temporary_identity is None:
                        try:
                            temporary_stat = os.fstat(temporary_fd)
                        except OSError:
                            pass
                        else:
                            if stat.S_ISREG(temporary_stat.st_mode):
                                temporary_identity = _identity(temporary_stat)
                    try:
                        os.close(temporary_fd)
                    except OSError as exc:
                        close_error = exc
                if temporary_created and temporary_identity is not None:
                    try:
                        named_temporary = os.stat(
                            temporary_name,
                            dir_fd=chain.fd,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        pass
                    else:
                        if _identity(named_temporary) == temporary_identity:
                            os.unlink(temporary_name, dir_fd=chain.fd)
                            os.fsync(chain.fd)
                if close_error is not None:
                    if primary_error is None:
                        raise close_error
                    primary_error.add_note(
                        "Discord reference audit temporary close also failed"
                    )
            chain.verify()
            final_content = _read_regular_at(
                chain.fd,
                relative.name,
                "reference audit",
                expected_identity=(
                    temporary_identity if destination_created else None
                ),
            )
            if final_content != content:
                raise ValueError(
                    "Discord immutable reference audit content differs"
                )
            chain.verify()


def _read_regular_at(
    parent_fd: int,
    name: str,
    label: str,
    *,
    missing_ok: bool = False,
    expected_identity: tuple[int, int] | None = None,
) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ValueError(f"Discord reference audit {label} is missing") from None
    except OSError as exc:
        raise ValueError(f"Discord reference audit {label} cannot be opened") from exc
    try:
        opened_before = os.fstat(descriptor)
        if not stat.S_ISREG(opened_before.st_mode):
            raise ValueError(f"Discord reference audit {label} is unsafe")
        if (
            expected_identity is not None
            and _identity(opened_before) != expected_identity
        ):
            raise ValueError(
                f"Discord reference audit {label} publication changed"
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        opened_after = os.fstat(descriptor)
        try:
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise ValueError(
                f"Discord reference audit {label} changed while reading"
            ) from exc
        if (
            not stat.S_ISREG(opened_after.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or _regular_file_signature(opened_before)
            != _regular_file_signature(opened_after)
            or _regular_file_signature(opened_after)
            != _regular_file_signature(named)
            or (
                expected_identity is not None
                and _identity(named) != expected_identity
            )
        ):
            raise ValueError(
                f"Discord reference audit {label} changed while reading"
            )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("Discord reference audit temporary write stopped")
        remaining = remaining[written:]


def _validate_relative_path(relative: Path, *, allow_empty: bool = False) -> None:
    if relative.is_absolute() or (not relative.parts and not allow_empty):
        raise ValueError("Discord reference audit path is not contained")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Discord reference audit path is not contained")


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _regular_file_signature(
    value: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        stat.S_IFMT(value.st_mode),
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _strict_json_copy(value: object) -> Any:
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise ValueError("Discord reference audit value is not safe JSON") from exc


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return _sha256(_canonical_json_bytes(value))


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _valid_snowflake(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.isascii()
        and value.isdigit()
        and value != "0"
        and str(int(value)) == value
    )
