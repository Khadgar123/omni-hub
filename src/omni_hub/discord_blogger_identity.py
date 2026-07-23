"""Versioned, review-bound Discord target/author identity projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hmac
from typing import Iterable, Mapping, Sequence

from .discord_blogger_contract import canonical_json_sha256
from .discord_blogger_corpus import BloggerMessage


_VERIFIED_TYPES = frozenset({"owner", "team", "proxy", "community"})
_IDENTITY_TYPES = (*sorted(_VERIFIED_TYPES), "unknown", "conflict")
_TARGET_TYPES = (
    "single_author_analyst",
    "multi_author_team",
    "signal_delivery_channel",
    "community_chat",
    "news_or_aggregation",
    "unknown",
)
_AGGREGATION_SCOPES = frozenset(
    {"message_owner", "target_rollup_only", "no_performance"}
)
_DECISIONS = frozenset({"accepted", "rejected", "conflicting"})
_SHA_LENGTH = 64


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    target_id: str
    author_id: str | None
    identity_type: str
    target_type: str
    aggregation_scope: str
    verified: bool
    author_eligible: bool
    performance_owner_id: str | None
    aggregation_owner_id: str | None
    parent_rollup_target_ids: tuple[str, ...]
    review_candidate_ids: tuple[str, ...]


def build_target_identity_registry(
    *,
    messages: Iterable[BloggerMessage],
    inventory: Mapping[str, object],
    reviewed_overrides: Sequence[Mapping[str, object]],
    expected_reviewed_pack_sha256: str,
) -> dict[str, object]:
    """Build a redacted registry whose only verification source is frozen IDs."""

    targets = _inventory_targets(inventory)
    target_by_id = {str(row["target_id"]): row for row in targets}
    observed: dict[str, set[str]] = {target_id: set() for target_id in target_by_id}
    for message in messages:
        if not isinstance(message, BloggerMessage):
            raise TypeError("Discord identity messages must be BloggerMessage values")
        if message.channel_id not in target_by_id:
            raise ValueError("Discord identity message target is outside inventory")
        if message.author_id is not None:
            _require_id(message.author_id, "message author")
            observed[message.channel_id].add(message.author_id)
        if message.webhook_id is not None:
            _require_id(message.webhook_id, "message webhook")
        if message.application_id is not None:
            _require_id(message.application_id, "message application")
        _timestamp(message.timestamp, "message timestamp")

    _require_sha(
        expected_reviewed_pack_sha256,
        "expected reviewed pack",
    )
    actual_reviewed_pack_sha256 = canonical_json_sha256(
        list(reviewed_overrides)
    )
    if not hmac.compare_digest(
        expected_reviewed_pack_sha256,
        actual_reviewed_pack_sha256,
    ):
        raise ValueError(
            "Discord reviewed identity pack does not match detached SHA-256"
        )
    manifest, frozen_rows = _frozen_review_pack(
        reviewed_overrides, target_by_id
    )
    candidate_ids = [str(row["candidate_id"]) for row in frozen_rows]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("Discord reviewed identity candidate IDs are duplicated")

    output_targets: list[dict[str, object]] = []
    for target in sorted(targets, key=lambda row: int(str(row["target_id"]))):
        target_id = str(target["target_id"])
        parent_id = target.get("parent_id")
        rollup_only = target.get("count_semantics") == "family_rollup"
        bindings = (
            []
            if rollup_only
            else [
                row
                for row in frozen_rows
                if row["target_id"] == target_id
            ]
        )
        reviewed_target_types = {
            str(row["target_type"]) for row in bindings
        }
        target_type = (
            next(iter(reviewed_target_types))
            if len(reviewed_target_types) == 1
            else "unknown"
        )
        output_targets.append(
            {
                "target_id": target_id,
                "parent_id": parent_id,
                "identity_semantics": (
                    "rollup_only" if rollup_only else "exact_target_author"
                ),
                "default_identity_type": "unknown",
                "target_type": target_type,
                "author_eligible": True,
                "observed_author_ids": sorted(
                    observed[target_id], key=int
                ),
                "parent_rollup_target_ids": (
                    [parent_id]
                    if not rollup_only
                    and isinstance(parent_id, str)
                    and parent_id in target_by_id
                    and target_by_id[parent_id].get("count_semantics")
                    == "family_rollup"
                    else []
                ),
                "bindings": bindings,
            }
        )

    return {
        "artifact_kind": "discord-blogger-target-identity-registry-v1",
        "schema_version": 1,
        "identity_types": list(_IDENTITY_TYPES),
        "target_types": list(_TARGET_TYPES),
        "reviewed_pack_sha256": actual_reviewed_pack_sha256,
        "reviewed_row_count": manifest["reviewed_row_count"],
        "target_count": len(output_targets),
        "targets": output_targets,
    }


def resolve_message_owner(
    *, message: BloggerMessage, registry: Mapping[str, object]
) -> IdentityResolution:
    """Resolve one exact message author; parent targets remain rollups only."""

    if not isinstance(message, BloggerMessage):
        raise TypeError("Discord identity resolution requires a BloggerMessage")
    if (
        registry.get("artifact_kind")
        != "discord-blogger-target-identity-registry-v1"
        or registry.get("schema_version") != 1
    ):
        raise ValueError("Discord identity registry is invalid")
    targets = registry.get("targets")
    if not isinstance(targets, list):
        raise ValueError("Discord identity registry targets are invalid")
    matching_targets = [
        row
        for row in targets
        if isinstance(row, Mapping) and row.get("target_id") == message.channel_id
    ]
    if len(matching_targets) != 1:
        raise ValueError("Discord identity message target is missing or ambiguous")
    target = matching_targets[0]
    rollups = target.get("parent_rollup_target_ids")
    if (
        not isinstance(rollups, list)
        or any(not isinstance(value, str) for value in rollups)
    ):
        raise ValueError("Discord identity target rollups are invalid")
    instant = _timestamp(message.timestamp, "message timestamp")
    bindings = target.get("bindings")
    if not isinstance(bindings, list):
        raise ValueError("Discord identity target bindings are invalid")
    active_target_types = {
        str(row["target_type"])
        for row in bindings
        if isinstance(row, Mapping) and _active(row, instant)
    }
    resolved_target_type = (
        next(iter(active_target_types))
        if len(active_target_types) == 1
        else str(target["target_type"])
    )
    active: list[Mapping[str, object]] = []
    if message.author_id is not None:
        for row in bindings:
            if (
                isinstance(row, Mapping)
                and row.get("author_id") == message.author_id
                and row.get("webhook_id") == message.webhook_id
                and row.get("application_id") == message.application_id
                and _active(row, instant)
            ):
                active.append(row)

    if not active:
        identity_type = "unknown"
        target_type = resolved_target_type
        aggregation_scope = "no_performance"
        verified = False
        performance_owner_id = None
        aggregation_owner_id = None
    elif (
        len(active_target_types) > 1
        or len(active) > 1
        or any(row.get("decision") == "conflicting" for row in active)
    ):
        identity_type = "conflict"
        target_type = "unknown"
        aggregation_scope = "no_performance"
        verified = False
        performance_owner_id = None
        aggregation_owner_id = None
    else:
        row = active[0]
        identity_type = str(row["identity_type"])
        target_type = resolved_target_type
        aggregation_scope = str(row["aggregation_scope"])
        verified = row.get("decision") == "accepted"
        performance_owner_id = (
            str(row["performance_owner_id"])
            if verified
            and aggregation_scope == "message_owner"
            and row.get("performance_owner_id") is not None
            else None
        )
        aggregation_owner_id = (
            str(row["aggregation_owner_id"])
            if verified
            and aggregation_scope == "target_rollup_only"
            and row.get("aggregation_owner_id") is not None
            else None
        )
    return IdentityResolution(
        target_id=message.channel_id,
        author_id=message.author_id,
        identity_type=identity_type,
        target_type=target_type,
        aggregation_scope=aggregation_scope,
        verified=verified,
        author_eligible=identity_type != "community",
        performance_owner_id=performance_owner_id,
        aggregation_owner_id=aggregation_owner_id,
        parent_rollup_target_ids=tuple(rollups),
        review_candidate_ids=tuple(
            sorted(str(row["candidate_id"]) for row in active)
        ),
    )


def _inventory_targets(
    inventory: Mapping[str, object],
) -> list[Mapping[str, object]]:
    if (
        not isinstance(inventory, Mapping)
        or inventory.get("artifact_kind")
        != "discord-blogger-target-inventory-v1"
        or inventory.get("schema_version") != 1
    ):
        raise ValueError("Discord identity inventory is invalid")
    rows = inventory.get("targets")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Discord identity inventory targets are invalid")
    output: list[Mapping[str, object]] = []
    target_ids: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("Discord identity inventory target is invalid")
        target_id = _require_id(row.get("target_id"), "inventory target")
        parent_id = row.get("parent_id")
        if parent_id is not None:
            _require_id(parent_id, "inventory parent")
        if row.get("count_semantics") not in {
            "exact_channel",
            "exact_thread",
            "family_rollup",
        }:
            raise ValueError("Discord identity inventory target semantics are invalid")
        target_ids.append(target_id)
        output.append(row)
    if len(target_ids) != len(set(target_ids)):
        raise ValueError("Discord identity inventory target IDs are duplicated")
    return output


def _review_row(
    value: Mapping[str, object],
    target_by_id: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("Discord reviewed identity row is invalid")
    required = {
        "schema_version",
        "record_type",
        "candidate_id",
        "target_id",
        "author_id",
        "webhook_id",
        "application_id",
        "valid_from",
        "valid_to",
        "decision",
        "identity_type",
        "target_type",
        "aggregation_scope",
        "performance_owner_id",
        "aggregation_owner_id",
        "reviewer",
        "reviewed_at",
        "evidence_sha256",
        "candidate_pack_sha256",
        "reviewed_labels_sha256",
    }
    if (
        set(value) != required
        or value.get("schema_version") != 1
        or value.get("record_type") != "identity_review"
    ):
        raise ValueError("Discord reviewed identity row fields are invalid")
    row = dict(value)
    for field in (
        "candidate_id",
        "evidence_sha256",
        "candidate_pack_sha256",
        "reviewed_labels_sha256",
    ):
        _require_sha(row[field], field)
    target_id = _require_id(row["target_id"], "review target")
    if target_id not in target_by_id:
        raise ValueError("Discord reviewed identity target is outside inventory")
    _require_id(row["author_id"], "review author")
    for field in (
        "webhook_id",
        "application_id",
        "performance_owner_id",
        "aggregation_owner_id",
    ):
        if row[field] is not None:
            _require_id(row[field], field)
    valid_from = _timestamp(row["valid_from"], "valid_from")
    valid_to = (
        None
        if row["valid_to"] is None
        else _timestamp(row["valid_to"], "valid_to")
    )
    if valid_to is not None and valid_from >= valid_to:
        raise ValueError("Discord reviewed identity validity window is invalid")
    _timestamp(row["reviewed_at"], "reviewed_at")
    if (
        not isinstance(row["reviewer"], str)
        or not row["reviewer"].strip()
        or row["decision"] not in _DECISIONS
        or row["identity_type"] not in _IDENTITY_TYPES
        or row["target_type"] not in _TARGET_TYPES
        or row["aggregation_scope"] not in _AGGREGATION_SCOPES
    ):
        raise ValueError("Discord reviewed identity decision is invalid")
    decision = str(row["decision"])
    identity_type = str(row["identity_type"])
    owner_id = row["performance_owner_id"]
    aggregation_owner_id = row["aggregation_owner_id"]
    target_type = str(row["target_type"])
    aggregation_scope = str(row["aggregation_scope"])
    if decision == "accepted":
        allowed = {
            ("single_author_analyst", "owner", "message_owner"),
            ("multi_author_team", "team", "target_rollup_only"),
            ("signal_delivery_channel", "proxy", "target_rollup_only"),
            ("community_chat", "community", "no_performance"),
            ("news_or_aggregation", "community", "no_performance"),
        }
        if (target_type, identity_type, aggregation_scope) not in allowed:
            raise ValueError("accepted target/identity aggregation is invalid")
        if aggregation_scope == "message_owner":
            if owner_id is None or aggregation_owner_id is not None:
                raise ValueError("message owner aggregation is invalid")
        elif aggregation_scope == "target_rollup_only":
            if owner_id is not None or aggregation_owner_id is None:
                raise ValueError("target rollup aggregation is invalid")
        elif owner_id is not None or aggregation_owner_id is not None:
            raise ValueError("no-performance aggregation is invalid")
    elif decision == "rejected":
        if (
            identity_type != "unknown"
            or target_type != "unknown"
            or aggregation_scope != "no_performance"
            or owner_id is not None
            or aggregation_owner_id is not None
        ):
            raise ValueError("rejected identity must remain unknown")
    elif (
        identity_type != "conflict"
        or target_type != "unknown"
        or aggregation_scope != "no_performance"
        or owner_id is not None
        or aggregation_owner_id is not None
    ):
        raise ValueError("conflicting identity must not name a performance owner")
    return row


def _frozen_review_pack(
    values: Sequence[Mapping[str, object]],
    target_by_id: Mapping[str, Mapping[str, object]],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    if isinstance(values, (str, bytes)) or not values:
        raise ValueError("Discord reviewed identity pack is invalid")
    manifest_value = values[0]
    required = {
        "schema_version",
        "record_type",
        "reviewed_row_count",
        "reviewed_rows_sha256",
        "candidate_pack_sha256",
        "reviewed_labels_sha256",
    }
    if (
        not isinstance(manifest_value, Mapping)
        or set(manifest_value) != required
        or manifest_value.get("schema_version") != 1
        or manifest_value.get("record_type") != "manifest"
    ):
        raise ValueError("Discord reviewed identity pack manifest is invalid")
    manifest = dict(manifest_value)
    for field in (
        "reviewed_rows_sha256",
        "candidate_pack_sha256",
        "reviewed_labels_sha256",
    ):
        _require_sha(manifest[field], field)
    raw_rows = list(values[1:])
    if (
        isinstance(manifest["reviewed_row_count"], bool)
        or not isinstance(manifest["reviewed_row_count"], int)
        or manifest["reviewed_row_count"] != len(raw_rows)
        or manifest["reviewed_rows_sha256"]
        != canonical_json_sha256([dict(row) for row in raw_rows])
    ):
        raise ValueError(
            "Discord identity registry requires the complete frozen review pack"
        )
    rows = [_review_row(row, target_by_id) for row in raw_rows]
    if any(
        row["candidate_pack_sha256"] != manifest["candidate_pack_sha256"]
        or row["reviewed_labels_sha256"]
        != manifest["reviewed_labels_sha256"]
        for row in rows
    ):
        raise ValueError("Discord reviewed identity pack binding is inconsistent")
    return manifest, rows


def _active(row: Mapping[str, object], instant: datetime) -> bool:
    start = _timestamp(row.get("valid_from"), "valid_from")
    raw_end = row.get("valid_to")
    end = None if raw_end is None else _timestamp(raw_end, "valid_to")
    return instant >= start and (end is None or instant < end)


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"Discord identity {label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Discord identity {label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Discord identity {label} must be timezone-aware")
    return parsed.astimezone(UTC)


def _require_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.isdecimal() or int(value) <= 0:
        raise ValueError(f"Discord identity {label} ID is invalid")
    return value


def _require_sha(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"Discord identity {label} SHA-256 is invalid")
    return value
