"""Pure request-bound policy for Discord media resolution recovery."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any
from urllib.parse import unquote, urlsplit

from .connectors.discord import (
    RFC2544_FAKE_IP_MEDIA_HOSTS,
    RFC2544_FAKE_IP_MEDIA_PORT,
    rfc2544_fake_ip_media_policy_descriptor,
)


LEGACY_RETRY_TRIGGER = "legacy_resolver_security_conflation_v1"
RESOLUTION_RETRY_TRIGGER = "media_resolution_retry_v1"
LEGACY_ZERO_BYTE_RECLASSIFICATION_KIND = (
    "legacy_zero_byte_download_reclassification_v1"
)
EMBED_ICON_PRODUCER_MIGRATION_KIND = "embed_icon_url_fields_v1"
MEDIA_RECORD_SCHEMA_V4 = 4
MAX_RESOLUTION_RETRY_SEQUENCES = 3
HTTP_COMPENSATION_TERMINAL_REASONS = frozenset(
    {"download_http_400", "download_http_404", "download_http_415"}
)
FRESH_SECURITY_REJECTION_PROVENANCE = MappingProxyType(
    {
        "version": 1,
        "reason_code": "media_security_policy_rejected",
        "legacy_eligible": False,
    }
)
TRANSIENT_RESOLUTION_DETAILS = frozenset(
    {"resolver_eai_again", "resolver_timeout"}
)
UNRESOLVED_RESOLUTION_DETAILS = frozenset(
    {
        "resolver_name_not_found",
        "resolver_no_data",
        "resolver_empty_answer",
        "resolver_os_error_unclassified",
    }
)

_INVALID_ANSWER_DETAIL = "resolver_invalid_answer"
_STABLE_RESOLUTION_DETAILS = (
    TRANSIENT_RESOLUTION_DETAILS
    | UNRESOLVED_RESOLUTION_DETAILS
    | {_INVALID_ANSWER_DETAIL}
)
_RESOLUTION_TERMINAL_REASONS = frozenset(
    {
        "media_resolution_failed_transient",
        "media_resolution_retry_exhausted",
        "media_resolution_unresolved",
        "media_resolution_invalid_answer",
    }
)
_TYPED_SEQUENCE_RETRYABLE_REASONS = frozenset(
    {
        "content_length_mismatch",
        "download_failed",
        "download_failed_transient",
    }
)
_TYPED_FIELD_NAMES = frozenset(
    {
        "retry_trigger",
        "retry_of_attempt_number",
        "policy_inputs_sha256",
        "resolution_retry_sequence",
    }
)
_ZERO_METADATA_FIELDS = (
    "http_content_type",
    "http_content_length",
    "sha256",
    "blob_path",
)
_BINARY_STATUSES = frozenset({"complete", "captured_with_warning"})
_HARD_TERMINAL_REASONS = frozenset(
    {"logical_identity_conflict", "size_limit_exceeded"}
)
_COVERED_OUTCOME_TERMINAL_REASONS = MappingProxyType(
    {
        "complete": frozenset({"downloaded"}),
        "captured_with_warning": frozenset(
            {
                "declared_size_mismatch",
                "mime_mismatch",
                "media_type_unverified",
            }
        ),
        "reference_only": frozenset(
            {
                "media_reference_not_binary",
                "youtube_embed_player_reference",
            }
        ),
    }
)
_COVERED_STATUSES = frozenset(_COVERED_OUTCOME_TERMINAL_REASONS)
_OUTCOME_FIELDS = (
    "url",
    "status",
    "terminal_reason",
    "failure_detail",
    "http_content_type",
    "http_content_length",
    "actual_bytes",
    "sha256",
    "blob_path",
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DISCORD_EXTERNAL_PROXY_HOST = re.compile(
    r"^(?:media|images-ext-[0-9]+)\.discordapp\.net$"
)
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_ICON_FIELD_DESCRIPTORS = MappingProxyType(
    {
        ("embed", "author_icon"): ("icon_url", "proxy_icon_url", ("name",)),
        ("embed", "footer_icon"): ("icon_url", "proxy_icon_url", ("text",)),
    }
)
_DEFAULT_FIELD_DESCRIPTOR = ("url", "proxy_url", None)
_ICON_MIGRATION_FIELD = "producer_migration"
_ZERO_RECLASSIFICATION_FIELD = "evidence_reclassification"


@dataclass(frozen=True, slots=True)
class MediaResolutionContext:
    request_sha256: str
    allow_rfc2544_fake_ip: bool
    policy_inputs_sha256: str | None
    policy_descriptor: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class _AttemptFact:
    attempt_number: int
    attempt: Mapping[str, Any] | None
    url: object
    status: object
    terminal_reason: object
    failure_detail: object
    typed: bool
    sequence: object
    policy_inputs_sha256: object
    retry_trigger: object
    retry_trigger_present: bool
    retry_of_attempt_number: object
    retry_of_present: bool


def media_resolution_context(
    request_identity: Mapping[str, Any],
    request_sha256: str,
) -> MediaResolutionContext:
    """Bind the current RFC 2544 policy to an immutable request identity."""

    if (
        not isinstance(request_identity, Mapping)
        or not isinstance(request_sha256, str)
        or _SHA256_PATTERN.fullmatch(request_sha256) is None
    ):
        raise ValueError("Discord media recovery request identity is invalid")
    options = request_identity.get("options")
    if not isinstance(options, Mapping):
        raise ValueError("Discord media recovery request options are invalid")

    allow_rfc2544_fake_ip = options.get("allow_rfc2544_fake_ip", False)
    if not isinstance(allow_rfc2544_fake_ip, bool):
        raise ValueError("Discord RFC2544 fake-IP option must be a boolean")
    recorded_descriptor = options.get("rfc2544_fake_ip_policy")
    expected_descriptor = rfc2544_fake_ip_media_policy_descriptor()
    if allow_rfc2544_fake_ip:
        if recorded_descriptor != expected_descriptor:
            raise ValueError("Discord RFC2544 fake-IP policy identity mismatch")
        descriptor_copy = deepcopy(expected_descriptor)
        return MediaResolutionContext(
            request_sha256=request_sha256,
            allow_rfc2544_fake_ip=True,
            policy_inputs_sha256=str(expected_descriptor["inputs_sha256"]),
            policy_descriptor=MappingProxyType(descriptor_copy),
        )
    if recorded_descriptor is not None:
        raise ValueError("Discord RFC2544 fake-IP policy requires explicit opt-in")
    return MediaResolutionContext(
        request_sha256=request_sha256,
        allow_rfc2544_fake_ip=False,
        policy_inputs_sha256=None,
        policy_descriptor=None,
    )


def discord_media_field_descriptor(
    kind: object,
    field: object,
) -> tuple[str, str, tuple[str, ...] | None]:
    """Return the one exact producer URL/identity descriptor for a media field."""

    return _ICON_FIELD_DESCRIPTORS.get(
        (kind, field),
        _DEFAULT_FIELD_DESCRIPTOR,
    )


def migrate_legacy_media_record(
    record: Mapping[str, Any],
    *,
    source_record_sha256: str,
    verified_empty_blob: bool,
) -> tuple[dict[str, Any], bool]:
    """Apply narrow, deterministic, zero-network compatibility migrations."""

    if (
        not isinstance(record, Mapping)
        or not isinstance(source_record_sha256, str)
        or _SHA256_PATTERN.fullmatch(source_record_sha256) is None
        or not isinstance(verified_empty_blob, bool)
    ):
        raise ValueError("Discord legacy media migration input is invalid")
    migrated = deepcopy(dict(record))
    if _ICON_MIGRATION_FIELD in migrated:
        _validate_icon_migration_marker(migrated)
        _validate_zero_reclassification_markers(migrated)
        return migrated, False
    if _has_zero_reclassification_marker(migrated):
        _validate_zero_reclassification_markers(migrated)
        return migrated, False

    if _is_legacy_icon_record_candidate(migrated):
        if _canonical_sha256(migrated, newline=True) != source_record_sha256:
            raise ValueError("Discord icon migration source hash is invalid")
        return _migrate_legacy_icon_record(
            migrated,
            source_record_sha256=source_record_sha256,
        ), True

    if _is_legacy_zero_complete_candidate(migrated):
        if _canonical_sha256(migrated, newline=True) != source_record_sha256:
            raise ValueError("Discord zero-byte migration source hash is invalid")
        if not verified_empty_blob:
            raise ValueError("Discord zero-byte migration blob is unverified")
        return _migrate_legacy_zero_complete(
            migrated,
            source_record_sha256=source_record_sha256,
        ), True
    return migrated, False


def reclassified_zero_byte_attempt_numbers(
    record: Mapping[str, Any],
) -> frozenset[int]:
    """Return source attempts explicitly invalidated by a verified marker."""

    return frozenset(_validate_zero_reclassification_markers(record))


def is_legacy_zero_complete_candidate(record: Mapping[str, Any]) -> bool:
    """Expose the filesystem gate predicate without weakening exact migration."""

    return isinstance(record, Mapping) and _is_legacy_zero_complete_candidate(record)


def _migrate_legacy_icon_record(
    record: dict[str, Any],
    *,
    source_record_sha256: str,
) -> dict[str, Any]:
    source_record_snapshot = deepcopy(record)
    kind = record["kind"]
    field = record["field"]
    direct_field, proxy_field, identity_fields = discord_media_field_descriptor(
        kind,
        field,
    )
    assert identity_fields is not None
    observations = record["observations"]
    attempts = record["attempt_history"]
    assert isinstance(observations, list)
    assert isinstance(attempts, list)
    source_observation_hashes = [
        _canonical_sha256(observation) for observation in observations
    ]
    source_metadata_hashes = [
        _canonical_sha256(observation["metadata"])
        for observation in observations
    ]
    source_attempt_hashes = [
        _canonical_sha256(attempt) for attempt in attempts
    ]
    for observation in observations:
        assert isinstance(observation, dict)
        metadata = observation["metadata"]
        assert isinstance(metadata, Mapping)
        observation["proxy_url"] = _http_candidate(metadata.get(proxy_field))
        if observation.get("url") is None:
            observation["url"] = _http_candidate(metadata.get(direct_field)) or _http_candidate(
                metadata.get(proxy_field)
            )
    metadata = record["declared_metadata"]
    assert isinstance(metadata, Mapping)
    record["schema_version"] = MEDIA_RECORD_SCHEMA_V4
    record["identity_metadata"] = {
        identity_field: deepcopy(metadata.get(identity_field))
        for identity_field in identity_fields
    }
    record["identity_conflicts"] = []
    target_observation_hashes = [
        _canonical_sha256(observation) for observation in observations
    ]
    target_sha256 = _canonical_sha256(record, newline=True)
    record[_ICON_MIGRATION_FIELD] = {
        "version": 1,
        "kind": EMBED_ICON_PRODUCER_MIGRATION_KIND,
        "network_attempted": False,
        "source_schema_version": 3,
        "target_schema_version": MEDIA_RECORD_SCHEMA_V4,
        "source_record_sha256": source_record_sha256,
        "source_record_snapshot": source_record_snapshot,
        "target_record_sha256": target_sha256,
        "source_observation_count": len(observations),
        "source_observation_sha256": source_observation_hashes,
        "target_observation_sha256": target_observation_hashes,
        "source_observation_metadata_sha256": source_metadata_hashes,
        "source_attempt_count": len(attempts),
        "source_attempt_sha256": source_attempt_hashes,
    }
    _validate_icon_migration_marker(record)
    return record


def _migrate_legacy_zero_complete(
    record: dict[str, Any],
    *,
    source_record_sha256: str,
) -> dict[str, Any]:
    attempts = record["attempt_history"]
    assert isinstance(attempts, list) and attempts
    source_attempt = attempts[-1]
    assert isinstance(source_attempt, Mapping)
    source_observation = _source_observation_for_candidate(
        record,
        source_attempt["url"],
    )
    assert source_observation is not None
    marker = {
        "url": source_attempt["url"],
        "status": "failed",
        "terminal_reason": "download_failed_transient",
        "failure_detail": None,
        "http_content_type": None,
        "http_content_length": None,
        "actual_bytes": 0,
        "sha256": None,
        "blob_path": None,
        _ZERO_RECLASSIFICATION_FIELD: {
            "version": 1,
            "kind": LEGACY_ZERO_BYTE_RECLASSIFICATION_KIND,
            "network_attempted": False,
            "source_record_sha256": source_record_sha256,
            "source_attempt_number": len(attempts),
            "source_attempt_sha256": _canonical_sha256(source_attempt),
            "source_observation_sha256": _canonical_sha256(source_observation),
            "source_observation_metadata_sha256": _canonical_sha256(
                source_observation["metadata"]
            ),
        },
    }
    attempts.append(marker)
    for field in _OUTCOME_FIELDS:
        record[field] = deepcopy(marker.get(field))
    record["failure_detail"] = None
    _validate_zero_reclassification_markers(record)
    return record


def _is_legacy_icon_record_candidate(record: Mapping[str, Any]) -> bool:
    kind = record.get("kind")
    field = record.get("field")
    if (
        record.get("schema_version") != 3
        or (kind, field) not in _ICON_FIELD_DESCRIPTORS
        or _ICON_MIGRATION_FIELD in record
    ):
        return False
    metadata = record.get("declared_metadata")
    observations = record.get("observations")
    sources = record.get("sources")
    candidates = record.get("candidate_urls")
    attempts = record.get("attempt_history")
    identity_conflicts = record.get("identity_conflicts")
    direct_field, proxy_field, _ = discord_media_field_descriptor(kind, field)
    if (
        not isinstance(metadata, Mapping)
        or not isinstance(observations, list)
        or not observations
        or not isinstance(sources, list)
        or len(sources) != 1
        or not isinstance(candidates, list)
        or not 1 <= len(candidates) <= 2
        or len(set(candidates)) != len(candidates)
        or record.get("url") not in candidates
        or not isinstance(attempts, list)
        or identity_conflicts != []
        or record.get("terminal_reason") == "logical_identity_conflict"
    ):
        return False
    legacy_identity = discord_media_metadata_without_urls(metadata)
    if record.get("identity_metadata") != legacy_identity:
        return False
    authorized: set[str] = set()
    if len(observations) != 1:
        return False
    for observation in observations:
        if (
            not isinstance(observation, Mapping)
            or observation.get("source") not in sources
            or not isinstance(observation.get("metadata"), Mapping)
        ):
            return False
        observation_metadata = observation["metadata"]
        if discord_media_metadata_without_urls(observation_metadata) != legacy_identity:
            return False
        direct = _http_candidate(observation_metadata.get(direct_field))
        proxy = _http_candidate(observation_metadata.get(proxy_field))
        if direct is None and proxy is None:
            return False
        authorized.update(value for value in (direct, proxy) if value is not None)
        legacy_top_url = observation.get("url")
        if legacy_top_url not in {direct, proxy}:
            return False
        if observation.get("proxy_url") not in {None, proxy}:
            return False
    return set(candidates).issubset(authorized)


def _is_legacy_zero_complete_candidate(record: Mapping[str, Any]) -> bool:
    attempts = record.get("attempt_history")
    metadata = record.get("declared_metadata")
    candidates = record.get("candidate_urls")
    observations = record.get("observations")
    if (
        record.get("schema_version") != 3
        or record.get("kind") != "attachment"
        or record.get("field") != "attachment"
        or record.get("status") != "complete"
        or record.get("terminal_reason") != "downloaded"
        or record.get("failure_detail") is not None
        or not isinstance(metadata, Mapping)
        or isinstance(metadata.get("size"), bool)
        or metadata.get("size") != 0
        or not isinstance(candidates, list)
        or len(candidates) != 2
        or len(set(candidates)) != 2
        or record.get("url") not in candidates
        or not isinstance(observations, list)
        or not observations
        or not isinstance(attempts, list)
        or len(attempts) != 1
        or candidates[0] != record.get("url")
    ):
        return False
    source = attempts[-1]
    if not isinstance(source, Mapping) or not _legacy_zero_complete_outcome(source):
        return False
    if not _legacy_zero_complete_outcome(record):
        return False
    if not _current_record_mirrors_attempt(record, source):
        return False
    if _source_observation_for_candidate(record, source.get("url")) is None:
        return False
    try:
        validate_media_record_producer_metadata(record)
    except ValueError:
        return False
    return True


def _legacy_zero_complete_outcome(value: Mapping[str, Any]) -> bool:
    content_type = value.get("http_content_type")
    return (
        value.get("status") == "complete"
        and value.get("terminal_reason") == "downloaded"
        and value.get("failure_detail") is None
        and isinstance(content_type, str)
        and bool(content_type)
        and normalized_discord_media_mime(content_type) == content_type
        and value.get("http_content_length") == 0
        and value.get("actual_bytes") == 0
        and value.get("sha256") == _EMPTY_SHA256
        and isinstance(value.get("blob_path"), str)
        and bool(value.get("blob_path"))
    )


def _source_observation_for_candidate(
    record: Mapping[str, Any],
    candidate_url: object,
) -> Mapping[str, Any] | None:
    observations = record.get("observations")
    sources = record.get("sources")
    if not isinstance(observations, list) or not isinstance(sources, list):
        return None
    return next(
        (
            observation
            for observation in reversed(observations)
            if isinstance(observation, Mapping)
            and observation.get("source") in sources
            and candidate_url in _producer_observation_candidate_urls(
                record,
                observation,
            )
        ),
        None,
    )


def _has_zero_reclassification_marker(record: Mapping[str, Any]) -> bool:
    attempts = record.get("attempt_history")
    return isinstance(attempts, list) and any(
        isinstance(attempt, Mapping)
        and _ZERO_RECLASSIFICATION_FIELD in attempt
        for attempt in attempts
    )


def _validate_zero_reclassification_markers(
    record: Mapping[str, Any],
) -> set[int]:
    attempts = record.get("attempt_history", [])
    if not isinstance(attempts, list):
        raise ValueError("Discord zero-byte migration attempt ledger is invalid")
    sources: set[int] = set()
    for marker_number, attempt in enumerate(attempts, start=1):
        if not isinstance(attempt, Mapping) or _ZERO_RECLASSIFICATION_FIELD not in attempt:
            continue
        marker = attempt.get(_ZERO_RECLASSIFICATION_FIELD)
        if (
            not isinstance(marker, Mapping)
            or set(marker)
            != {
                "version",
                "kind",
                "network_attempted",
                "source_record_sha256",
                "source_attempt_number",
                "source_attempt_sha256",
                "source_observation_sha256",
                "source_observation_metadata_sha256",
            }
            or marker.get("version") != 1
            or marker.get("kind") != LEGACY_ZERO_BYTE_RECLASSIFICATION_KIND
            or marker.get("network_attempted") is not False
            or _SHA256_PATTERN.fullmatch(str(marker.get("source_record_sha256"))) is None
            or _SHA256_PATTERN.fullmatch(str(marker.get("source_attempt_sha256"))) is None
            or _SHA256_PATTERN.fullmatch(str(marker.get("source_observation_sha256"))) is None
            or _SHA256_PATTERN.fullmatch(
                str(marker.get("source_observation_metadata_sha256"))
            )
            is None
        ):
            raise ValueError("Discord zero-byte reclassification marker is invalid")
        source_number = marker.get("source_attempt_number")
        if (
            isinstance(source_number, bool)
            or not isinstance(source_number, int)
            or source_number != marker_number - 1
            or source_number in sources
            or source_number < 1
        ):
            raise ValueError("Discord zero-byte reclassification marker is not adjacent")
        source = attempts[source_number - 1]
        if (
            not isinstance(source, Mapping)
            or not _legacy_zero_complete_outcome(source)
            or _canonical_sha256(source) != marker["source_attempt_sha256"]
        ):
            raise ValueError("Discord zero-byte reclassification source is invalid")
        observations = record.get("observations")
        source_observation = next(
            (
                observation
                for observation in observations
                if isinstance(observation, Mapping)
                and _canonical_sha256(observation)
                == marker["source_observation_sha256"]
                and _canonical_sha256(observation.get("metadata"))
                == marker["source_observation_metadata_sha256"]
                and source.get("url")
                in _producer_observation_candidate_urls(record, observation)
            ),
            None,
        ) if isinstance(observations, list) else None
        if (
            source_observation is None
        ):
            raise ValueError("Discord zero-byte reclassification observation is invalid")
        expected_attempt = {
            "url": source.get("url"),
            "status": "failed",
            "terminal_reason": "download_failed_transient",
            "failure_detail": None,
            "http_content_type": None,
            "http_content_length": None,
            "actual_bytes": 0,
            "sha256": None,
            "blob_path": None,
            _ZERO_RECLASSIFICATION_FIELD: dict(marker),
        }
        if dict(attempt) != expected_attempt:
            raise ValueError("Discord zero-byte reclassification attempt is invalid")
        source_metadata = source_observation.get("metadata")
        if not isinstance(source_metadata, Mapping):
            raise ValueError("Discord zero-byte reclassification observation is invalid")
        direct_url = _http_candidate(source_metadata.get("url"))
        proxy_url = _http_candidate(source_metadata.get("proxy_url"))
        if direct_url != source.get("url") or proxy_url is None:
            raise ValueError("Discord zero-byte reclassification candidates are invalid")
        source_record = deepcopy(dict(record))
        source_record.update(
            {
                "schema_version": 3,
                "url": direct_url,
                "candidate_urls": [direct_url, proxy_url],
                "declared_metadata": deepcopy(dict(source_metadata)),
                "declared_content_type": normalized_discord_media_mime(
                    source_metadata.get("content_type")
                ),
                "identity_metadata": discord_media_identity_metadata(
                    "attachment",
                    source_metadata,
                ),
                "identity_conflicts": [],
                "sources": [deepcopy(source_observation.get("source"))],
                "observations": [deepcopy(dict(source_observation))],
                "observed_urls": [direct_url, proxy_url],
                "attempt_history": deepcopy(attempts[:source_number]),
            }
        )
        for field in _OUTCOME_FIELDS:
            source_record[field] = deepcopy(source.get(field))
        source_record.pop("failure_detail", None)
        if (
            _canonical_sha256(source_record, newline=True)
            != marker["source_record_sha256"]
        ):
            raise ValueError(
                "Discord zero-byte reclassification source hash is invalid"
            )
        sources.add(source_number)
    if len(sources) > 1:
        raise ValueError("Discord zero-byte reclassification marker is duplicated")
    return sources


def _validate_icon_migration_marker(record: Mapping[str, Any]) -> None:
    marker = record.get(_ICON_MIGRATION_FIELD)
    if marker is None:
        return
    expected_fields = {
        "version",
        "kind",
        "network_attempted",
        "source_schema_version",
        "target_schema_version",
        "source_record_sha256",
        "source_record_snapshot",
        "target_record_sha256",
        "source_observation_count",
        "source_observation_sha256",
        "target_observation_sha256",
        "source_observation_metadata_sha256",
        "source_attempt_count",
        "source_attempt_sha256",
    }
    if (
        not isinstance(marker, Mapping)
        or set(marker) != expected_fields
        or marker.get("version") != 1
        or marker.get("kind") != EMBED_ICON_PRODUCER_MIGRATION_KIND
        or marker.get("network_attempted") is not False
        or marker.get("source_schema_version") != 3
        or marker.get("target_schema_version") != MEDIA_RECORD_SCHEMA_V4
        or record.get("schema_version") != MEDIA_RECORD_SCHEMA_V4
        or (record.get("kind"), record.get("field")) not in _ICON_FIELD_DESCRIPTORS
        or _SHA256_PATTERN.fullmatch(str(marker.get("source_record_sha256"))) is None
        or _SHA256_PATTERN.fullmatch(str(marker.get("target_record_sha256"))) is None
    ):
        raise ValueError("Discord icon producer migration marker is invalid")
    observations = record.get("observations")
    attempts = record.get("attempt_history")
    source_record_snapshot = marker.get("source_record_snapshot")
    observation_count = marker.get("source_observation_count")
    attempt_count = marker.get("source_attempt_count")
    observation_hashes = marker.get("source_observation_sha256")
    target_observation_hashes = marker.get("target_observation_sha256")
    metadata_hashes = marker.get("source_observation_metadata_sha256")
    attempt_hashes = marker.get("source_attempt_sha256")
    if (
        not isinstance(source_record_snapshot, Mapping)
        or _ICON_MIGRATION_FIELD in source_record_snapshot
        or not _is_legacy_icon_record_candidate(source_record_snapshot)
        or _canonical_sha256(source_record_snapshot, newline=True)
        != marker["source_record_sha256"]
    ):
        raise ValueError("Discord icon producer migration source is invalid")
    if (
        not isinstance(observations, list)
        or not isinstance(attempts, list)
        or isinstance(observation_count, bool)
        or not isinstance(observation_count, int)
        or observation_count < 1
        or observation_count > len(observations)
        or isinstance(attempt_count, bool)
        or not isinstance(attempt_count, int)
        or attempt_count < 0
        or attempt_count > len(attempts)
        or not isinstance(observation_hashes, list)
        or not isinstance(target_observation_hashes, list)
        or not isinstance(metadata_hashes, list)
        or not isinstance(attempt_hashes, list)
        or len(observation_hashes) != observation_count
        or len(target_observation_hashes) != observation_count
        or len(metadata_hashes) != observation_count
        or len(attempt_hashes) != attempt_count
    ):
        raise ValueError("Discord icon producer migration ledger is invalid")
    for index in range(observation_count):
        observation = observations[index]
        source_observation = deepcopy(dict(observation)) if isinstance(observation, Mapping) else None
        if isinstance(source_observation, dict):
            source_metadata = source_observation.get("metadata")
            source_observation["proxy_url"] = (
                source_metadata.get("proxy_url")
                if isinstance(source_metadata, Mapping)
                else None
            )
        if (
            not isinstance(observation, Mapping)
            or _canonical_sha256(observation) != target_observation_hashes[index]
            or _canonical_sha256(source_observation) != observation_hashes[index]
            or _canonical_sha256(observation.get("metadata")) != metadata_hashes[index]
        ):
            raise ValueError("Discord icon producer migration observation changed")
    for index in range(attempt_count):
        if _canonical_sha256(attempts[index]) != attempt_hashes[index]:
            raise ValueError("Discord icon producer migration attempt changed")
    source_observations = source_record_snapshot.get("observations")
    source_attempts = source_record_snapshot.get("attempt_history")
    if (
        not isinstance(source_observations, list)
        or not isinstance(source_attempts, list)
        or len(source_observations) != observation_count
        or len(source_attempts) != attempt_count
        or [
            _canonical_sha256(observation)
            for observation in source_observations
        ]
        != observation_hashes
        or [
            _canonical_sha256(observation.get("metadata"))
            for observation in source_observations
            if isinstance(observation, Mapping)
        ]
        != metadata_hashes
        or [
            _canonical_sha256(attempt)
            for attempt in source_attempts
        ]
        != attempt_hashes
    ):
        raise ValueError("Discord icon producer migration source ledger is invalid")
    target = deepcopy(dict(source_record_snapshot))
    direct_field, proxy_field, identity_fields = discord_media_field_descriptor(
        target.get("kind"),
        target.get("field"),
    )
    assert identity_fields is not None
    for observation in target["observations"]:
        observation_metadata = observation["metadata"]
        observation["proxy_url"] = _http_candidate(
            observation_metadata.get(proxy_field)
        )
        if observation.get("url") is None:
            observation["url"] = _http_candidate(
                observation_metadata.get(direct_field)
            ) or _http_candidate(observation_metadata.get(proxy_field))
    target["schema_version"] = MEDIA_RECORD_SCHEMA_V4
    target["identity_metadata"] = {
        identity_field: deepcopy(target["declared_metadata"].get(identity_field))
        for identity_field in identity_fields
    }
    target["identity_conflicts"] = []
    if _canonical_sha256(target, newline=True) != marker["target_record_sha256"]:
        raise ValueError("Discord icon producer migration target hash is invalid")
    for field in ("logical_key", "kind", "field", "identity_metadata"):
        if record.get(field) != target.get(field):
            raise ValueError("Discord icon producer migration target changed")


def _http_candidate(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return value if urlsplit(value).scheme.lower() in {"http", "https"} else None
    except ValueError:
        return value if value.lower().startswith(("http://", "https://")) else None


def _canonical_sha256(value: object, *, newline: bool = False) -> str:
    content = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if newline:
        content += b"\n"
    return hashlib.sha256(content).hexdigest()


def validate_resolution_attempt_history(
    record: Mapping[str, Any],
    *,
    context: MediaResolutionContext,
) -> None:
    """Fail closed on corrupt typed attempt provenance without performing I/O."""

    _validate_context(context)
    if not isinstance(record, Mapping):
        raise ValueError("Discord media recovery record is invalid")
    attempt_history = record.get("attempt_history", [])
    if not isinstance(attempt_history, list):
        raise ValueError("Discord media recovery attempt history is invalid")
    validate_media_record_attempt_consistency(record)

    # Collect the complete ledger before validating cross-attempt relationships.
    facts = [
        _attempt_fact(attempt, attempt_number)
        for attempt_number, attempt in enumerate(attempt_history, start=1)
    ]

    typed_by_candidate: dict[str, list[_AttemptFact]] = defaultdict(list)
    legacy_markers: set[tuple[str, str, str | None]] = set()
    candidates_with_typed_history: set[str] = set()
    for fact in facts:
        if fact.attempt is None:
            raise ValueError("Discord media recovery attempt is invalid")
        if (
            isinstance(fact.url, str)
            and fact.url in candidates_with_typed_history
            and not fact.typed
        ):
            raise ValueError(
                "Discord media attempt after typed recovery lacks provenance"
            )
        if not fact.typed:
            continue
        if not isinstance(fact.url, str) or not fact.url:
            raise ValueError("Typed Discord media attempt has an invalid URL")
        candidates_with_typed_history.add(fact.url)
        if (
            isinstance(fact.sequence, bool)
            or not isinstance(fact.sequence, int)
            or not 1 <= fact.sequence <= MAX_RESOLUTION_RETRY_SEQUENCES
        ):
            raise ValueError("Discord media resolution sequence is invalid")
        if "policy_inputs_sha256" not in fact.attempt:
            raise ValueError("Typed Discord media attempt has no policy hash")
        if fact.policy_inputs_sha256 != context.policy_inputs_sha256:
            raise ValueError("Discord media resolution policy hash is invalid")
        _validate_failure_taxonomy(fact)

        if fact.retry_trigger_present:
            if fact.retry_trigger not in {
                LEGACY_RETRY_TRIGGER,
                RESOLUTION_RETRY_TRIGGER,
            }:
                raise ValueError("Discord media retry trigger is invalid")
            if not fact.retry_of_present:
                raise ValueError("Discord media retry provenance is incomplete")
            _validate_retry_of_value(fact)
        elif fact.retry_of_present:
            raise ValueError("Discord media retry provenance has no trigger")
        elif fact.sequence != 1:
            raise ValueError("Initial Discord media resolution sequence is invalid")

        if fact.retry_trigger == LEGACY_RETRY_TRIGGER:
            if (
                fact.sequence != 1
                or not _context_allows_legacy(context)
                or not _eligible_legacy_candidate_url(fact.url)
                or not _legacy_candidate_observation_allowed(record, fact.url)
            ):
                raise ValueError("Discord legacy media retry policy is invalid")
            marker_key = (
                fact.url,
                LEGACY_RETRY_TRIGGER,
                context.policy_inputs_sha256,
            )
            if marker_key in legacy_markers:
                raise ValueError("Discord legacy media retry marker is duplicated")
            legacy_markers.add(marker_key)
        elif fact.retry_trigger == RESOLUTION_RETRY_TRIGGER and fact.sequence == 1:
            raise ValueError("Discord media resolution retry sequence is invalid")
        typed_by_candidate[fact.url].append(fact)

    for candidate_url, candidate_facts in typed_by_candidate.items():
        sequences = [fact.sequence for fact in candidate_facts]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("Discord media resolution sequence ledger is invalid")

    for fact in facts:
        if not fact.typed or fact.retry_trigger is None:
            continue
        retry_of = fact.retry_of_attempt_number
        assert isinstance(retry_of, int) and not isinstance(retry_of, bool)
        referenced = facts[retry_of - 1]
        if referenced.attempt is None or referenced.url != fact.url:
            raise ValueError("Discord media retry crosses candidate URLs")
        prefix = facts[: fact.attempt_number - 1]
        latest_candidate = next(
            (
                prefix_fact
                for prefix_fact in reversed(prefix)
                if prefix_fact.attempt is not None
                and prefix_fact.url == fact.url
            ),
            None,
        )
        if fact.retry_trigger == LEGACY_RETRY_TRIGGER:
            if (
                latest_candidate is None
                or latest_candidate.attempt_number != retry_of
                or retry_of != fact.attempt_number - 1
                or any(
                    prefix_fact.attempt is not None
                    and prefix_fact.status in _BINARY_STATUSES
                    for prefix_fact in prefix
                )
            ):
                raise ValueError("Discord legacy media retry prefix is invalid")
            if not (
                referenced.status == "failed"
                and referenced.terminal_reason == "unsafe_media_url"
                and referenced.attempt is not None
                and _is_unmarked_legacy_security_attempt(referenced.attempt)
                and _zero_byte_no_metadata(referenced.attempt)
            ):
                raise ValueError("Discord legacy media retry reference is invalid")
            continue
        if not (
            latest_candidate is not None
            and latest_candidate.attempt_number == retry_of
            and referenced.typed
            and referenced.attempt is not None
            and _typed_failure_allows_next_sequence(referenced.attempt)
            and isinstance(referenced.sequence, int)
            and not isinstance(referenced.sequence, bool)
            and referenced.sequence + 1 == fact.sequence
        ):
            raise ValueError("Discord media resolution retry reference is invalid")


def validate_media_record_attempt_consistency(record: Mapping[str, Any]) -> None:
    """Bind the current media state to the append-only attempt ledger."""

    if not isinstance(record, Mapping):
        raise ValueError("Discord media record consistency input is invalid")
    validate_media_record_producer_metadata(record)
    reclassified_zero_attempts = _validate_zero_reclassification_markers(record)
    attempt_history = record.get("attempt_history", [])
    if not isinstance(attempt_history, list):
        raise ValueError("Discord media record attempt ledger is invalid")
    facts = [
        _attempt_fact(attempt, attempt_number)
        for attempt_number, attempt in enumerate(attempt_history, start=1)
    ]
    fresh_rejected_urls: set[str] = set()
    nonfinal_covered_urls: set[str] = set()
    for fact in facts:
        if fact.attempt is None:
            continue
        if isinstance(fact.url, str) and fact.url in nonfinal_covered_urls:
            raise ValueError(
                "Discord non-final covered media outcome is not candidate-terminal"
            )
        if isinstance(fact.url, str) and fact.url in fresh_rejected_urls:
            raise ValueError(
                "Discord fresh media security rejection is not terminal"
            )
        _validate_fresh_security_rejection(fact.attempt)
        if (
            isinstance(fact.url, str)
            and fact.attempt.get("security_rejection")
            == FRESH_SECURITY_REJECTION_PROVENANCE
        ):
            fresh_rejected_urls.add(fact.url)
        if (
            fact.typed
            and fact.sequence == 1
            and fact.retry_trigger != LEGACY_RETRY_TRIGGER
        ):
            latest_candidate_prefix = next(
                (
                    prefix_fact
                    for prefix_fact in reversed(facts[: fact.attempt_number - 1])
                    if prefix_fact.attempt is not None
                    and prefix_fact.url == fact.url
                ),
                None,
            )
            if (
                latest_candidate_prefix is not None
                and latest_candidate_prefix.status == "failed"
                and latest_candidate_prefix.terminal_reason == "unsafe_media_url"
            ):
                raise ValueError(
                    "Discord media legacy retry marker is missing"
                )
        if (
            fact.status in _COVERED_STATUSES
            and fact.attempt_number not in reclassified_zero_attempts
            and not _legal_covered_outcome(
            fact.attempt,
            producer_record=record,
            recompute_producer=True,
            allow_youtube_reference=False,
            )
        ):
            raise ValueError("Discord covered media attempt outcome is invalid")
        if (
            isinstance(fact.url, str)
            and fact.status in {"captured_with_warning", "reference_only"}
        ):
            nonfinal_covered_urls.add(fact.url)

    complete_facts = [
        fact
        for fact in facts
        if fact.status == "complete"
        and fact.attempt_number not in reclassified_zero_attempts
    ]
    if complete_facts:
        complete = complete_facts[0]
        current_is_conflict = (
            record.get("status") == "failed"
            and record.get("terminal_reason") == "logical_identity_conflict"
        )
        if (
            len(complete_facts) != 1
            or complete.attempt_number != len(facts)
            or complete.attempt is None
            or (
                not current_is_conflict
                and not _current_record_mirrors_attempt(record, complete.attempt)
            )
        ):
            raise ValueError("Discord complete media attempt is not absorbing")

    hard_facts = [
        fact for fact in facts if fact.terminal_reason in _HARD_TERMINAL_REASONS
    ]
    if hard_facts:
        hard = hard_facts[0]
        current_is_conflict = (
            record.get("status") == "failed"
            and record.get("terminal_reason") == "logical_identity_conflict"
        )
        if (
            len(hard_facts) != 1
            or hard.attempt_number != len(facts)
            or hard.attempt is None
            or (
                not current_is_conflict
                and not _current_record_mirrors_attempt(record, hard.attempt)
            )
        ):
            raise ValueError("Discord hard media failure is not absorbing")

    if record.get("status") in _COVERED_STATUSES and not _legal_covered_outcome(
        record,
        producer_record=record,
        recompute_producer=True,
        allow_youtube_reference=True,
    ):
        raise ValueError("Discord covered media record outcome is invalid")

    typed_pending = [
        fact
        for fact in facts
        if fact.typed and fact.status in {"in_progress", "interrupted"}
    ]
    for fact in typed_pending:
        if (
            fact.attempt is None
            or fact.attempt_number != len(facts)
            or not _legal_pending_attempt(fact.attempt)
            or not _current_record_mirrors_attempt(record, fact.attempt)
        ):
            raise ValueError(
                "Discord pending media recovery does not match current state"
            )

    current_status = record.get("status")
    current_reason = record.get("terminal_reason")
    if current_status == "failed" and current_reason not in {
        "logical_identity_conflict",
        "byte_transport_unavailable",
    }:
        latest = facts[-1].attempt if facts else None
        if (
            record.get("failure_detail") is not None
            or latest is None
            or not _current_record_mirrors_attempt(
                record,
                latest,
                compare_failure_detail=False,
            )
        ):
            raise ValueError(
                "Discord current media failure does not match attempt ledger"
            )
    if current_status == "in_progress" and (
        not facts or current_reason in {None, "candidate_urls_changed"}
    ):
        if record.get("failure_detail") is not None or not _zero_byte_no_metadata(
            record
        ):
            raise ValueError("Discord current media progress contains evidence")

    if (
        current_status == "in_progress"
        and current_reason in {None, "interrupted"}
        and facts
    ):
        latest = facts[-1].attempt
        if (
            latest is None
            or (
                current_reason is None
                and not _legal_pending_attempt(latest)
            )
            or not _current_record_mirrors_attempt(record, latest)
        ):
            raise ValueError(
                "Discord current media progress does not match attempt ledger"
            )

    if current_status == "complete":
        latest = facts[-1].attempt if facts else None
        if latest is None or not _current_record_mirrors_attempt(record, latest):
            raise ValueError(
                "Discord covered media record does not match attempt ledger"
            )
    elif (
        current_status == "captured_with_warning"
        or (
            current_status == "reference_only"
            and current_reason == "media_reference_not_binary"
        )
    ):
        latest_covered_number = next(
            (
                fact.attempt_number
                for fact in reversed(facts)
                if fact.url == record.get("url")
            ),
            None,
        )
        latest_covered = (
            facts[latest_covered_number - 1].attempt
            if latest_covered_number is not None
            else None
        )
        if (
            latest_covered is None
            or not _current_record_mirrors_attempt(record, latest_covered)
            or (
                facts
                and facts[-1].status in {"in_progress", "interrupted"}
            )
            or any(
                fact.status == "complete"
                for fact in facts[latest_covered_number:]
            )
        ):
            raise ValueError(
                "Discord non-final covered media record does not match attempt ledger"
            )


def has_resolution_attempt_history(
    record: Mapping[str, Any],
    candidate_url: str,
) -> bool:
    """Return whether a candidate has entered the typed recovery state machine."""

    if not isinstance(record, Mapping) or not isinstance(candidate_url, str):
        return False
    attempt_history = record.get("attempt_history")
    if not isinstance(attempt_history, list):
        return False
    return any(
        (fact := _attempt_fact(attempt, attempt_number)).url == candidate_url
        and fact.typed
        for attempt_number, attempt in enumerate(attempt_history, start=1)
    )


def legacy_recovery_retry_of(
    record: Mapping[str, Any],
    candidate_url: str,
    *,
    context: MediaResolutionContext,
) -> int | None:
    """Return the exact unsafe attempt eligible for the one-time legacy override."""

    if not _context_allows_legacy(context):
        return None
    validate_resolution_attempt_history(record, context=context)
    if (
        record.get("status") != "failed"
        or record.get("terminal_reason") != "unsafe_media_url"
        or record.get("url") != candidate_url
        or not _zero_byte_no_metadata(record)
        or not _eligible_legacy_candidate_url(candidate_url)
    ):
        return None

    attempt_history = record.get("attempt_history", [])
    assert isinstance(attempt_history, list)
    if any(
        isinstance(attempt, Mapping)
        and attempt.get("status") in _BINARY_STATUSES
        for attempt in attempt_history
    ):
        return None

    if not _legacy_candidate_observation_allowed(record, candidate_url):
        return None

    latest_number: int | None = None
    latest_attempt: Mapping[str, Any] | None = None
    for attempt_number, attempt in enumerate(attempt_history, start=1):
        if isinstance(attempt, Mapping) and attempt.get("url") == candidate_url:
            latest_number = attempt_number
            latest_attempt = attempt
    if latest_attempt is None or latest_number is None:
        return None
    if latest_number != len(attempt_history):
        return None
    if not (
        latest_attempt.get("status") == "failed"
        and latest_attempt.get("terminal_reason") == "unsafe_media_url"
        and _is_unmarked_legacy_security_attempt(latest_attempt)
        and _zero_byte_no_metadata(latest_attempt)
    ):
        return None
    if any(
        isinstance(attempt, Mapping)
        and attempt.get("url") == candidate_url
        and attempt.get("retry_trigger") == LEGACY_RETRY_TRIGGER
        and attempt.get("policy_inputs_sha256") == context.policy_inputs_sha256
        for attempt in attempt_history
    ):
        return None
    return latest_number


def reusable_resolution_attempt_number(
    record: Mapping[str, Any],
    candidate_url: str,
) -> int | None:
    """Return an already committed typed sequence that a crash may replay."""

    if not isinstance(record, Mapping):
        return None
    try:
        validate_media_record_attempt_consistency(record)
    except ValueError:
        return None
    attempt_history = record.get("attempt_history")
    if not isinstance(attempt_history, list):
        return None
    for attempt_number in range(len(attempt_history), 0, -1):
        attempt = attempt_history[attempt_number - 1]
        if not isinstance(attempt, Mapping) or attempt.get("url") != candidate_url:
            continue
        sequence = attempt.get("resolution_retry_sequence")
        if (
            _legal_pending_attempt(attempt)
            and "policy_inputs_sha256" in attempt
            and not isinstance(sequence, bool)
            and isinstance(sequence, int)
            and 1 <= sequence <= MAX_RESOLUTION_RETRY_SEQUENCES
            and _reusable_provenance_shape_is_valid(
                attempt,
                attempt_number=attempt_number,
                sequence=sequence,
            )
            and _reusable_reference_is_valid(
                attempt_history,
                attempt_number=attempt_number,
                attempt=attempt,
                sequence=sequence,
            )
        ):
            return attempt_number
        return None
    return None


def next_resolution_retry_metadata(
    record: Mapping[str, Any],
    candidate_url: str,
    *,
    context: MediaResolutionContext,
) -> dict[str, object] | None:
    """Select one bounded new logical sequence, or return no retry."""

    validate_resolution_attempt_history(record, context=context)
    current_status = record.get("status")
    current_reason = record.get("terminal_reason")
    if current_status == "complete" or current_reason in _HARD_TERMINAL_REASONS:
        return None
    if (
        current_status in {"captured_with_warning", "reference_only"}
        and candidate_url == record.get("url")
    ):
        return None
    if reusable_resolution_attempt_number(record, candidate_url) is not None:
        return None
    attempt_history = record.get("attempt_history", [])
    assert isinstance(attempt_history, list)
    global_tail = attempt_history[-1] if attempt_history else None
    if (
        isinstance(global_tail, Mapping)
        and any(field in global_tail for field in _TYPED_FIELD_NAMES)
        and global_tail.get("status") in {"in_progress", "interrupted"}
        and global_tail.get("url") != candidate_url
    ):
        return None
    latest_number: int | None = None
    latest_attempt: Mapping[str, Any] | None = None
    for attempt_number, attempt in enumerate(attempt_history, start=1):
        if isinstance(attempt, Mapping) and attempt.get("url") == candidate_url:
            latest_number = attempt_number
            latest_attempt = attempt

    if latest_attempt is not None and latest_number is not None:
        sequence = latest_attempt.get("resolution_retry_sequence")
        if (
            _typed_failure_allows_next_sequence(latest_attempt)
            and not isinstance(sequence, bool)
            and isinstance(sequence, int)
            and sequence < MAX_RESOLUTION_RETRY_SEQUENCES
        ):
            return {
                "retry_trigger": RESOLUTION_RETRY_TRIGGER,
                "retry_of_attempt_number": latest_number,
                "policy_inputs_sha256": context.policy_inputs_sha256,
                "resolution_retry_sequence": sequence + 1,
            }

    if any(
        isinstance(attempt, Mapping)
        and attempt.get("url") == candidate_url
        and "resolution_retry_sequence" in attempt
        for attempt in attempt_history
    ):
        return None
    retry_of = legacy_recovery_retry_of(
        record,
        candidate_url,
        context=context,
    )
    if retry_of is None:
        return None
    return {
        "retry_trigger": LEGACY_RETRY_TRIGGER,
        "retry_of_attempt_number": retry_of,
        "policy_inputs_sha256": context.policy_inputs_sha256,
        "resolution_retry_sequence": 1,
    }


def is_discord_external_proxy_url(value: object) -> bool:
    """Recognize the collector's existing credential-free Discord proxy shape."""

    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
        host = parsed.hostname
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and isinstance(host, str)
        and _DISCORD_EXTERNAL_PROXY_HOST.fullmatch(host) is not None
        and parsed.username is None
        and parsed.password is None
        and port is None
        and parsed.path.startswith("/external/")
    )


def _validate_context(context: MediaResolutionContext) -> None:
    if not isinstance(context, MediaResolutionContext):
        raise ValueError("Discord media resolution context is invalid")
    if (
        not isinstance(context.request_sha256, str)
        or _SHA256_PATTERN.fullmatch(context.request_sha256) is None
    ):
        raise ValueError("Discord media request hash is invalid")
    if not isinstance(context.allow_rfc2544_fake_ip, bool):
        raise ValueError("Discord media request policy flag is invalid")
    expected = rfc2544_fake_ip_media_policy_descriptor()
    if context.allow_rfc2544_fake_ip:
        if (
            context.policy_descriptor != expected
            or context.policy_inputs_sha256 != expected["inputs_sha256"]
        ):
            raise ValueError("Discord media request policy binding is invalid")
    elif (
        context.policy_descriptor is not None
        or context.policy_inputs_sha256 is not None
    ):
        raise ValueError("Discord non-opt-in request has a policy binding")


def _context_allows_legacy(context: object) -> bool:
    if not isinstance(context, MediaResolutionContext):
        return False
    expected = rfc2544_fake_ip_media_policy_descriptor()
    return (
        isinstance(context.request_sha256, str)
        and _SHA256_PATTERN.fullmatch(context.request_sha256) is not None
        and context.allow_rfc2544_fake_ip is True
        and context.policy_descriptor == expected
        and context.policy_inputs_sha256 == expected["inputs_sha256"]
    )


def _attempt_fact(attempt: object, attempt_number: int) -> _AttemptFact:
    if not isinstance(attempt, Mapping):
        return _AttemptFact(
            attempt_number,
            None,
            None,
            None,
            None,
            None,
            False,
            None,
            None,
            None,
            False,
            None,
            False,
        )
    terminal_reason = attempt.get("terminal_reason")
    failure_detail = attempt.get("failure_detail")
    typed = (
        bool(_TYPED_FIELD_NAMES.intersection(attempt))
        or failure_detail is not None
        or terminal_reason in _RESOLUTION_TERMINAL_REASONS
    )
    return _AttemptFact(
        attempt_number=attempt_number,
        attempt=attempt,
        url=attempt.get("url"),
        status=attempt.get("status"),
        terminal_reason=terminal_reason,
        failure_detail=failure_detail,
        typed=typed,
        sequence=attempt.get("resolution_retry_sequence"),
        policy_inputs_sha256=attempt.get("policy_inputs_sha256"),
        retry_trigger=attempt.get("retry_trigger"),
        retry_trigger_present="retry_trigger" in attempt,
        retry_of_attempt_number=attempt.get("retry_of_attempt_number"),
        retry_of_present="retry_of_attempt_number" in attempt,
    )


def _validate_retry_of_value(fact: _AttemptFact) -> None:
    retry_of = fact.retry_of_attempt_number
    if (
        isinstance(retry_of, bool)
        or not isinstance(retry_of, int)
        or retry_of < 1
        or retry_of >= fact.attempt_number
    ):
        raise ValueError("Discord media retry attempt number is invalid")


def _validate_failure_taxonomy(fact: _AttemptFact) -> None:
    detail = fact.failure_detail
    reason = fact.terminal_reason
    if fact.status in {"in_progress", "interrupted"}:
        if fact.attempt is None or not _legal_pending_attempt(fact.attempt):
            raise ValueError("Discord pending media resolution attempt is invalid")
        return
    if fact.status == "failed" and (
        not isinstance(reason, str) or not reason
    ):
        raise ValueError("Failed Discord media resolution attempt has no reason")
    if (
        (
            detail is not None
            or reason in _RESOLUTION_TERMINAL_REASONS
            or reason == "unsafe_media_url"
        )
        and (fact.attempt is None or not _zero_byte_no_metadata(fact.attempt))
    ):
        raise ValueError("Discord resolver outcome contains media evidence")
    if detail is None:
        if reason in _RESOLUTION_TERMINAL_REASONS:
            raise ValueError("Discord media resolution outcome has no failure detail")
        return
    if fact.status != "failed" or detail not in _STABLE_RESOLUTION_DETAILS:
        raise ValueError("Discord media resolution failure detail is invalid")
    if detail in TRANSIENT_RESOLUTION_DETAILS:
        expected_reason = (
            "media_resolution_retry_exhausted"
            if fact.sequence == MAX_RESOLUTION_RETRY_SEQUENCES
            else "media_resolution_failed_transient"
        )
        if reason != expected_reason:
            raise ValueError("Discord transient resolution outcome is invalid")
    elif detail in UNRESOLVED_RESOLUTION_DETAILS:
        if reason != "media_resolution_unresolved":
            raise ValueError("Discord unresolved media outcome is invalid")
    elif reason != "media_resolution_invalid_answer":
        raise ValueError("Discord invalid resolver answer outcome is invalid")


def _validate_fresh_security_rejection(attempt: Mapping[str, Any]) -> None:
    if "security_rejection" not in attempt:
        return
    if (
        attempt.get("security_rejection")
        != FRESH_SECURITY_REJECTION_PROVENANCE
        or attempt.get("status") != "failed"
        or attempt.get("terminal_reason") != "unsafe_media_url"
        or not _zero_byte_no_metadata(attempt)
    ):
        raise ValueError("Discord media security rejection provenance is invalid")


def _is_unmarked_legacy_security_attempt(attempt: Mapping[str, Any]) -> bool:
    return "security_rejection" not in attempt


def discord_declared_size_mismatch(
    record: Mapping[str, Any],
    actual_bytes: int,
) -> bool:
    """Return the collector's exact declared attachment-size warning decision."""

    if record.get("kind") != "attachment":
        return False
    metadata = record.get("declared_metadata")
    size = metadata.get("size") if isinstance(metadata, Mapping) else None
    return (
        isinstance(size, int)
        and not isinstance(size, bool)
        and size >= 0
        and size != actual_bytes
    )


def normalized_discord_media_mime(value: object) -> str | None:
    """Normalize Discord-declared/HTTP MIME exactly as the collector does."""

    if not isinstance(value, str) or not value.strip():
        return None
    return value.split(";", 1)[0].strip().lower()


def discord_media_metadata_without_urls(value: Any) -> Any:
    """Return the collector's exact recursive non-URL identity projection."""

    if isinstance(value, Mapping):
        return {
            key: discord_media_metadata_without_urls(item)
            for key, item in value.items()
            if key not in {"url", "proxy_url"}
        }
    if isinstance(value, list):
        return [discord_media_metadata_without_urls(item) for item in value]
    return deepcopy(value)


def discord_media_identity_metadata(
    kind: object,
    metadata: Mapping[str, Any],
    *,
    field: object = None,
    schema_version: object = None,
) -> dict[str, Any]:
    """Rebuild the collector's immutable identity projection."""

    if kind == "attachment":
        return {
            "id": metadata.get("id"),
            "size": metadata.get("size"),
            "content_type": normalized_discord_media_mime(
                metadata.get("content_type")
            ),
        }
    if kind == "sticker":
        return {
            "id": metadata.get("id"),
            "format_type": metadata.get("format_type"),
        }
    descriptor = discord_media_field_descriptor(kind, field)
    identity_fields = descriptor[2]
    if schema_version == MEDIA_RECORD_SCHEMA_V4 and identity_fields is not None:
        return {
            identity_field: deepcopy(metadata.get(identity_field))
            for identity_field in identity_fields
        }
    projected = discord_media_metadata_without_urls(metadata)
    assert isinstance(projected, dict)
    return projected


def validate_media_record_producer_metadata(record: Mapping[str, Any]) -> None:
    """Bind producer decision inputs to retained source observations."""

    _validate_icon_migration_marker(record)
    kind = record.get("kind")
    field = record.get("field")
    metadata = record.get("declared_metadata")
    observations = record.get("observations")
    current_url = record.get("url")
    baseline_identity = record.get("identity_metadata")
    identity_conflicts = record.get("identity_conflicts")
    sources = record.get("sources")
    if (
        not isinstance(metadata, Mapping)
        or not isinstance(observations, list)
        or not isinstance(current_url, str)
        or not current_url
        or not isinstance(baseline_identity, Mapping)
        or not isinstance(identity_conflicts, list)
        or not isinstance(sources, list)
        or any(not isinstance(source, Mapping) for source in sources)
    ):
        raise ValueError("Discord media producer metadata is invalid")
    expected_content_type = (
        None
        if kind == "sticker"
        else normalized_discord_media_mime(metadata.get("content_type"))
    )
    schema_version = record.get("schema_version")
    expected_identity = _producer_identity_metadata(
        kind,
        field,
        metadata,
        schema_version=schema_version,
    )
    expected_conflicts: list[dict[str, Any]] = []
    retained_observations: list[Mapping[str, Any]] = []
    authorized_candidate_urls: set[str] = set()
    current_observation_retained = False
    all_observation_sources_retained = True
    for observation_index, observation in enumerate(observations):
        if not isinstance(observation, Mapping):
            raise ValueError("Discord media producer observation is invalid")
        if any(observation == retained for retained in retained_observations):
            raise ValueError("Discord media producer observation is duplicated")
        retained_observations.append(observation)
        if not any(observation.get("source") == source for source in sources):
            all_observation_sources_retained = False
        observation_metadata = observation.get("metadata")
        if not isinstance(observation_metadata, Mapping):
            raise ValueError("Discord media producer observation is invalid")
        attachment_alias = _is_attachment_alias_observation(
            kind=kind,
            declared_metadata=metadata,
            observation=observation,
        )
        observed_identity = (
            baseline_identity
            if attachment_alias
            else _producer_identity_metadata(
                kind,
                field,
                observation_metadata,
                schema_version=schema_version,
            )
        )
        if (
            observed_identity == baseline_identity
            and not attachment_alias
            and observation.get("source") in sources
        ):
            observation_candidates = _producer_observation_candidate_urls(
                record,
                observation
            )
            authorized_candidate_urls.update(observation_candidates)
            if (
                observation_metadata == metadata
                and current_url in observation_candidates
            ):
                current_observation_retained = True
        if observed_identity != baseline_identity:
            conflict = {
                "observation_index": observation_index,
                "observed_identity": observed_identity,
            }
            if conflict not in expected_conflicts:
                expected_conflicts.append(conflict)
    has_conflict = bool(expected_conflicts)
    current_is_conflict = (
        record.get("status") == "failed"
        and record.get("terminal_reason") == "logical_identity_conflict"
    )
    candidate_urls = record.get("candidate_urls")
    candidate_ledger_valid = True
    if schema_version in {3, MEDIA_RECORD_SCHEMA_V4}:
        effective_candidate_urls = (
            [current_url]
            if "candidate_urls" not in record
            else candidate_urls
        )
        candidate_ledger_valid = (
            isinstance(effective_candidate_urls, list)
            and bool(effective_candidate_urls)
            and all(
                isinstance(value, str) and value
                for value in effective_candidate_urls
            )
            and len(set(effective_candidate_urls))
            == len(effective_candidate_urls)
            and current_url in effective_candidate_urls
            and set(effective_candidate_urls).issubset(
                authorized_candidate_urls
            )
        )
    if (
        record.get("declared_content_type") != expected_content_type
        or baseline_identity != expected_identity
        or not current_observation_retained
        or identity_conflicts != expected_conflicts
        or current_is_conflict != has_conflict
        or (not has_conflict and not all_observation_sources_retained)
        or not candidate_ledger_valid
    ):
        raise ValueError("Discord media producer metadata is inconsistent")


def discord_media_candidate_observation_metadata(
    record: Mapping[str, Any],
    candidate_url: str,
) -> dict[str, Any] | None:
    """Select the latest retained producer metadata that authorizes a candidate."""

    kind = record.get("kind")
    field = record.get("field")
    declared_metadata = record.get("declared_metadata")
    baseline_identity = record.get("identity_metadata")
    observations = record.get("observations")
    sources = record.get("sources")
    schema_version = record.get("schema_version")
    if (
        not isinstance(candidate_url, str)
        or not candidate_url
        or not isinstance(declared_metadata, Mapping)
        or not isinstance(baseline_identity, Mapping)
        or not isinstance(observations, list)
        or not isinstance(sources, list)
    ):
        return None
    for observation in reversed(observations):
        if (
            not isinstance(observation, Mapping)
            or observation.get("source") not in sources
        ):
            continue
        observation_metadata = observation.get("metadata")
        if not isinstance(observation_metadata, Mapping):
            continue
        if _is_attachment_alias_observation(
            kind=kind,
            declared_metadata=declared_metadata,
            observation=observation,
        ):
            continue
        if (
            _producer_identity_metadata(
                kind,
                field,
                observation_metadata,
                schema_version=schema_version,
            )
            != baseline_identity
            or candidate_url
            not in _producer_observation_candidate_urls(record, observation)
        ):
            continue
        return deepcopy(dict(observation_metadata))
    return None


def discord_media_reference_source_observation(
    record: Mapping[str, Any],
    source_url: object,
    *,
    expected_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Select the latest retained, identity-bound, unproxied source observation."""

    sources = record.get("sources")
    observations = record.get("observations")
    baseline_identity = record.get("identity_metadata")
    schema_version = record.get("schema_version")
    if (
        not isinstance(source_url, str)
        or not source_url
        or not isinstance(sources, list)
        or not sources
        or not isinstance(observations, list)
        or not isinstance(baseline_identity, Mapping)
    ):
        return None
    for observation in reversed(observations):
        if (
            not isinstance(observation, Mapping)
            or observation.get("source") not in sources
            or observation.get("url") != source_url
            or observation.get("proxy_url")
        ):
            continue
        metadata = observation.get("metadata")
        if (
            not isinstance(metadata, Mapping)
            or metadata.get("url") != source_url
            or metadata.get("proxy_url")
            or (
                expected_metadata is not None
                and metadata != expected_metadata
            )
            or _producer_identity_metadata(
                record.get("kind"),
                record.get("field"),
                metadata,
                schema_version=schema_version,
            )
            != baseline_identity
        ):
            continue
        return deepcopy(dict(observation))
    return None


def discord_media_reference_candidate_ledger_is_exact(
    record: Mapping[str, Any],
    *,
    source_url: object,
    failed_attempt_number: object,
) -> bool:
    """Bind a reference candidate ledger to retained observations and failures."""

    candidate_urls = record.get("candidate_urls")
    attempts = record.get("attempt_history")
    declared_metadata = record.get("declared_metadata")
    declared_source_observation = (
        discord_media_reference_source_observation(
            record,
            source_url,
            expected_metadata=declared_metadata,
        )
        if isinstance(declared_metadata, Mapping)
        else None
    )
    if (
        not isinstance(source_url, str)
        or not source_url
        or isinstance(failed_attempt_number, bool)
        or not isinstance(failed_attempt_number, int)
        or not isinstance(candidate_urls, list)
        or not candidate_urls
        or candidate_urls[0] != source_url
        or any(
            not isinstance(candidate_url, str) or not candidate_url
            for candidate_url in candidate_urls
        )
        or len(set(candidate_urls)) != len(candidate_urls)
        or not isinstance(attempts, list)
        or failed_attempt_number < 1
        or failed_attempt_number > len(attempts)
        or declared_source_observation is None
        or any(
            discord_media_candidate_observation_metadata(
                record,
                candidate_url,
            )
            is None
            for candidate_url in candidate_urls
        )
    ):
        return False
    later_urls: set[str] = set()
    for attempt in attempts[failed_attempt_number:]:
        if not isinstance(attempt, Mapping):
            return False
        attempt_url = attempt.get("url")
        terminal_reason = attempt.get("terminal_reason")
        if (
            not isinstance(attempt_url, str)
            or not attempt_url
            or attempt_url == source_url
            or attempt.get("status") != "failed"
            or not isinstance(terminal_reason, str)
            or not terminal_reason
            or discord_media_candidate_observation_metadata(
                record,
                attempt_url,
            )
            is None
        ):
            return False
        later_urls.add(attempt_url)
    return set(candidate_urls[1:]).issubset(later_urls)


def _producer_identity_metadata(
    kind: object,
    field: object,
    metadata: Mapping[str, Any],
    *,
    schema_version: object,
) -> dict[str, Any]:
    if schema_version == 2 and kind == "attachment":
        return {
            "id": metadata.get("id"),
            "filename": metadata.get("filename"),
            "size": metadata.get("size"),
            "content_type": normalized_discord_media_mime(
                metadata.get("content_type")
            ),
        }
    return discord_media_identity_metadata(
        kind,
        metadata,
        field=field,
        schema_version=schema_version,
    )


def _is_attachment_alias_observation(
    *,
    kind: object,
    declared_metadata: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> bool:
    if kind != "attachment":
        return False
    observation_metadata = observation.get("metadata")
    if not isinstance(observation_metadata, Mapping):
        return False
    declared_id = declared_metadata.get("id")
    alias_attachment_id = observation_metadata.get("attachment_id")
    if (
        isinstance(declared_id, str)
        and declared_id
        and isinstance(alias_attachment_id, str)
        and alias_attachment_id == declared_id
    ):
        return True
    declared_filename = declared_metadata.get("filename")
    metadata_url = observation_metadata.get("url")
    observation_url = observation.get("url")
    return (
        isinstance(declared_filename, str)
        and declared_filename
        and isinstance(metadata_url, str)
        and metadata_url == observation_url
        and metadata_url.startswith("attachment://")
        and unquote(metadata_url.removeprefix("attachment://"))
        == declared_filename
    )


def _producer_observation_candidate_urls(
    record: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> set[str]:
    metadata = observation.get("metadata")
    values = [observation.get("url"), observation.get("proxy_url")]
    if isinstance(metadata, Mapping):
        direct_field, proxy_field, _ = discord_media_field_descriptor(
            record.get("kind"),
            record.get("field"),
        )
        values.extend(
            (metadata.get(direct_field), metadata.get(proxy_field))
        )
    candidates: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            is_http = urlsplit(value).scheme.lower() in {"http", "https"}
        except ValueError:
            is_http = value.lower().startswith(("http://", "https://"))
        if is_http:
            candidates.add(value)
    return candidates


def discord_media_mime_outcome(
    record: Mapping[str, Any],
    declared_mime: object,
    actual_mime: object,
) -> tuple[str, str] | None:
    """Return the collector's exact status/reason decision for media MIME."""

    if (
        isinstance(declared_mime, str)
        and isinstance(actual_mime, str)
        and declared_mime != actual_mime
    ):
        declared_family = _mime_family(declared_mime)
        actual_family = _mime_family(actual_mime)
        if declared_family == actual_family:
            return "captured_with_warning", "mime_mismatch"
        if declared_family in {"image", "video", "audio"}:
            return "failed", "declared_media_type_mismatch"
        return "captured_with_warning", "mime_mismatch"

    if _requires_typed_media(record) and not _record_mime_matches(
        record,
        actual_mime,
    ):
        if actual_mime is None:
            return "captured_with_warning", "media_type_unverified"
        actual_family = _mime_family(actual_mime)
        if record.get("kind") == "embed" and actual_family not in {
            "image",
            "video",
            "audio",
        }:
            return "reference_only", "media_reference_not_binary"
        return "failed", "media_type_mismatch"
    return None


def _legal_covered_outcome(
    value: Mapping[str, Any],
    *,
    producer_record: Mapping[str, Any],
    recompute_producer: bool,
    allow_youtube_reference: bool,
) -> bool:
    status = value.get("status")
    allowed_reasons = _COVERED_OUTCOME_TERMINAL_REASONS.get(status)
    legal_status_reason = (
        allowed_reasons is not None
        and value.get("terminal_reason") in allowed_reasons
        and value.get("failure_detail") is None
    )
    if not legal_status_reason:
        return False
    if value.get("terminal_reason") == "youtube_embed_player_reference":
        return allow_youtube_reference and _zero_byte_no_metadata(value)

    actual_bytes = value.get("actual_bytes")
    content_length = value.get("http_content_length")
    content_type = value.get("http_content_type")
    if (
        isinstance(actual_bytes, bool)
        or not isinstance(actual_bytes, int)
        or actual_bytes < 0
    ):
        return False
    if actual_bytes == 0:
        return False
    digest = value.get("sha256")
    blob_path = value.get("blob_path")
    if (
        not isinstance(digest, str)
        or _SHA256_PATTERN.fullmatch(digest) is None
        or not isinstance(blob_path, str)
        or not blob_path
    ):
        return False
    if content_length is not None and (
        isinstance(content_length, bool)
        or not isinstance(content_length, int)
        or content_length < 0
        or content_length != actual_bytes
    ):
        return False
    if content_type is not None and not (
        isinstance(content_type, str)
        and bool(content_type)
        and content_type == content_type.split(";", 1)[0].strip().lower()
    ):
        return False
    if not recompute_producer:
        return True

    mime_outcome = discord_media_mime_outcome(
        producer_record,
        producer_record.get("declared_content_type"),
        content_type,
    )
    if mime_outcome is not None:
        expected = mime_outcome
    elif discord_declared_size_mismatch(producer_record, actual_bytes):
        expected = ("captured_with_warning", "declared_size_mismatch")
    else:
        expected = ("complete", "downloaded")
    return (status, value.get("terminal_reason")) == expected


def _current_record_mirrors_attempt(
    record: Mapping[str, Any],
    attempt: Mapping[str, Any],
    *,
    compare_failure_detail: bool = True,
) -> bool:
    attempt_status = attempt.get("status")
    expected_record_status = (
        "in_progress" if attempt_status == "interrupted" else attempt_status
    )
    if record.get("status") != expected_record_status:
        return False
    return all(
        record.get(field) == attempt.get(field)
        for field in _OUTCOME_FIELDS
        if field != "status"
        and (compare_failure_detail or field != "failure_detail")
    )


def _zero_byte_no_metadata(value: Mapping[str, Any]) -> bool:
    actual_bytes = value.get("actual_bytes")
    return (
        not isinstance(actual_bytes, bool)
        and isinstance(actual_bytes, int)
        and actual_bytes == 0
        and all(value.get(field) is None for field in _ZERO_METADATA_FIELDS)
    )


def _legal_pending_attempt(attempt: Mapping[str, Any]) -> bool:
    status = attempt.get("status")
    reason = attempt.get("terminal_reason")
    detail = attempt.get("failure_detail")
    return (
        (
            status == "in_progress"
            and reason is None
            and detail is None
        )
        or (
            status == "interrupted"
            and reason == "interrupted"
            and detail is None
        )
    ) and _zero_byte_no_metadata(attempt)


def _reusable_provenance_shape_is_valid(
    attempt: Mapping[str, Any],
    *,
    attempt_number: int,
    sequence: int,
) -> bool:
    policy_hash = attempt.get("policy_inputs_sha256")
    if policy_hash is not None and (
        not isinstance(policy_hash, str)
        or _SHA256_PATTERN.fullmatch(policy_hash) is None
    ):
        return False
    trigger_present = "retry_trigger" in attempt
    retry_of_present = "retry_of_attempt_number" in attempt
    if not trigger_present:
        return sequence == 1 and not retry_of_present
    trigger = attempt.get("retry_trigger")
    retry_of = attempt.get("retry_of_attempt_number")
    if (
        trigger not in {LEGACY_RETRY_TRIGGER, RESOLUTION_RETRY_TRIGGER}
        or not retry_of_present
        or isinstance(retry_of, bool)
        or not isinstance(retry_of, int)
        or not 1 <= retry_of < attempt_number
    ):
        return False
    if trigger == LEGACY_RETRY_TRIGGER:
        return sequence == 1
    return sequence >= 2


def _reusable_reference_is_valid(
    attempt_history: list[Any],
    *,
    attempt_number: int,
    attempt: Mapping[str, Any],
    sequence: int,
) -> bool:
    trigger = attempt.get("retry_trigger")
    if trigger is None:
        return True
    retry_of = attempt.get("retry_of_attempt_number")
    if (
        isinstance(retry_of, bool)
        or not isinstance(retry_of, int)
        or not 1 <= retry_of < attempt_number
    ):
        return False
    referenced = attempt_history[retry_of - 1]
    if not isinstance(referenced, Mapping) or referenced.get("url") != attempt.get("url"):
        return False
    if trigger == LEGACY_RETRY_TRIGGER:
        prefix = attempt_history[: attempt_number - 1]
        latest_candidate_number = next(
            (
                prefix_number
                for prefix_number in range(len(prefix), 0, -1)
                if isinstance(prefix[prefix_number - 1], Mapping)
                and prefix[prefix_number - 1].get("url") == attempt.get("url")
            ),
            None,
        )
        return (
            retry_of == attempt_number - 1
            and latest_candidate_number == retry_of
            and not any(
                isinstance(prefix_attempt, Mapping)
                and prefix_attempt.get("status") in _BINARY_STATUSES
                for prefix_attempt in prefix
            )
            and referenced.get("status") == "failed"
            and referenced.get("terminal_reason") == "unsafe_media_url"
            and _is_unmarked_legacy_security_attempt(referenced)
            and _zero_byte_no_metadata(referenced)
        )
    referenced_sequence = referenced.get("resolution_retry_sequence")
    return (
        not isinstance(referenced_sequence, bool)
        and isinstance(referenced_sequence, int)
        and referenced_sequence + 1 == sequence
        and "policy_inputs_sha256" in referenced
        and referenced.get("policy_inputs_sha256")
        == attempt.get("policy_inputs_sha256")
        and _typed_failure_allows_next_sequence(referenced)
    )


def _typed_failure_allows_next_sequence(attempt: Mapping[str, Any]) -> bool:
    if attempt.get("status") != "failed":
        return False
    reason = attempt.get("terminal_reason")
    detail = attempt.get("failure_detail")
    if reason == "media_resolution_failed_transient":
        return (
            detail in TRANSIENT_RESOLUTION_DETAILS
            and _zero_byte_no_metadata(attempt)
        )
    return detail is None and reason in _TYPED_SEQUENCE_RETRYABLE_REASONS


def _mime_family(value: str) -> str:
    return value.partition("/")[0]


def _embed_mime_matches(field: object, content_type: object) -> bool:
    if not isinstance(content_type, str):
        return False
    if field in {"image", "thumbnail", "media_gallery"}:
        return content_type.startswith("image/")
    if field == "video":
        return content_type.startswith("video/")
    if field in {
        "author_icon",
        "footer_icon",
        "poll_emoji",
        "sticker_items",
        "stickers",
    }:
        return content_type.startswith("image/")
    return False


def _requires_typed_media(record: Mapping[str, Any]) -> bool:
    if record.get("kind") == "embed":
        return record.get("field") in {
            "image",
            "thumbnail",
            "video",
            "author_icon",
            "footer_icon",
        }
    if record.get("kind") == "sticker":
        return True
    if record.get("kind") == "emoji":
        return True
    if record.get("kind") == "component":
        return record.get("field") in {"thumbnail", "media_gallery"}
    return False


def _record_mime_matches(
    record: Mapping[str, Any],
    content_type: object,
) -> bool:
    if record.get("kind") == "sticker":
        metadata = record.get("declared_metadata")
        format_type = (
            metadata.get("format_type") if isinstance(metadata, Mapping) else None
        )
        if format_type == 3:
            return content_type in {"application/json", "text/json"}
        return isinstance(content_type, str) and content_type.startswith("image/")
    if record.get("kind") == "emoji":
        return isinstance(content_type, str) and content_type.startswith("image/")
    if record.get("kind") == "component":
        return _embed_mime_matches(record.get("field"), content_type)
    return _embed_mime_matches(record.get("field"), content_type)


def _eligible_legacy_candidate_url(candidate_url: object) -> bool:
    if not isinstance(candidate_url, str):
        return False
    try:
        parsed = urlsplit(candidate_url)
        hostname = parsed.hostname
        explicit_port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme.lower() != "https"
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False
    canonical_host = _canonical_media_hostname(hostname)
    effective_port = (
        RFC2544_FAKE_IP_MEDIA_PORT
        if explicit_port is None
        else explicit_port
    )
    return (
        canonical_host in RFC2544_FAKE_IP_MEDIA_HOSTS
        and effective_port == RFC2544_FAKE_IP_MEDIA_PORT
    )


def _legacy_candidate_observation_allowed(
    record: Mapping[str, Any],
    candidate_url: object,
) -> bool:
    metadata = record.get("declared_metadata")
    proxy_url = metadata.get("proxy_url") if isinstance(metadata, Mapping) else None
    observations = record.get("observations")
    retained_observations = (
        observations if isinstance(observations, list) else []
    )
    if is_discord_external_proxy_url(candidate_url):
        return proxy_url == candidate_url and any(
            isinstance(observation, Mapping)
            and observation.get("proxy_url") == candidate_url
            for observation in retained_observations
        )
    if is_discord_external_proxy_url(proxy_url):
        return False
    return not any(
        isinstance(observation, Mapping)
        and observation.get("url") == candidate_url
        and is_discord_external_proxy_url(observation.get("proxy_url"))
        for observation in retained_observations
    )


def _canonical_media_hostname(host: str) -> str | None:
    if host.endswith(".."):
        return None
    normalized = host[:-1] if host.endswith(".") else host
    if not normalized:
        return None
    try:
        return normalized.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
