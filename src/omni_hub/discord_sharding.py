"""Auditable parent-family sharding and closure evidence for Discord.

The sharding paths plan and verify immutable collector artifacts.  The closure
capture path uses an injected Discord JSON transport to acquire a bounded,
hash-pinned delta and publishes it as one immutable directory.  All writes are
invoked through handlers registered with ``OperationRunner``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from contextlib import closing
from copy import deepcopy
import ctypes
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
from typing import Any
from urllib.parse import urlsplit

from .discord_message_evidence import extract_message_evidence
from .discord_media_audit import (
    MEDIA_RECOVERY_AUDIT_FILENAME,
    MEDIA_RECOVERY_AUDIT_VERSION,
    build_media_recovery_audit,
    canonical_media_recovery_audit_bytes,
)
from .discord_media_recovery import (
    MediaResolutionContext,
    discord_media_reference_candidate_ledger_is_exact,
    discord_media_reference_source_observation,
    media_resolution_context,
    validate_resolution_attempt_history,
)
from .discord_reference_sidecar import (
    verify_published_message_reference_resolution_audit,
)


SHARD_SCHEME = "parent-family-v1"
PLAN_KIND = "discord-parent-family-plan-v1"
MERGE_AUDIT_KIND = "discord-parent-family-merge-v1"
CLOSURE_AUDIT_KIND = "discord-parent-family-closure-v1"
CLOSURE_CENSUS_KIND = "discord-thread-census-v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SNOWFLAKE = re.compile(r"^[0-9]+$")
_THREAD_TYPES = frozenset({10, 11, 12})
_MESSAGE_BEARING_TYPES = frozenset({0, 2, 5, 10, 11, 12, 13})
_THREAD_PARENT_TYPES = frozenset({0, 5, 15, 16})
_STREAM_STATUSES = (
    "complete",
    "blocked",
    "failed",
    "truncated_by_limit",
    "not_found",
    "in_progress",
    "unknown",
)
_COVERED_ASSET_STATUSES = frozenset(
    {"complete", "captured_with_warning", "reference_only"}
)
_ASSET_STATUSES = _COVERED_ASSET_STATUSES | frozenset(
    {"failed", "in_progress", "not_requested"}
)
_ASSET_KINDS = frozenset(
    {"attachment", "embed", "component", "sticker", "emoji"}
)
_MESSAGE_EVIDENCE_DESCRIPTOR_V1_FIELDS = frozenset(
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
_MESSAGE_EVIDENCE_DESCRIPTOR_V2_FIELDS = (
    _MESSAGE_EVIDENCE_DESCRIPTOR_V1_FIELDS
    | frozenset(
        {
            "stream",
            "channel_id",
            "page_number",
            "fetched_at",
            "diagnostics_by_severity",
            "pin_events",
        }
    )
)
_MESSAGE_EVIDENCE_ROW_V1_FIELDS = frozenset(
    {"schema_version", "status", "nodes", "media", "references", "diagnostics"}
)
_MESSAGE_EVIDENCE_ROW_V2_FIELDS = _MESSAGE_EVIDENCE_ROW_V1_FIELDS | frozenset(
    {"stream", "channel_id", "page_number", "message_json_pointer"}
)
_ALLOWED_MESSAGE_WARNING_CODES = frozenset(
    {"snapshot_timestamp_reference_mismatch"}
)
_HEAD_CATCHUP_EVIDENCE_KIND = "discord-head-catchup-target-v1"
_HEAD_CATCHUP_RAW_PAGE_KIND = "discord-head-catchup-raw-page-v1"
_CENSUS_RAW_PAGE_KIND = "discord-thread-census-raw-page-v1"
_DISCORD_EPOCH_MS = 1_420_070_400_000
_LEGACY_MAX_ASSET_BYTES = 512 * 1024 * 1024
_MEDIA_RECOVERY_AUDIT_DESCRIPTOR_FIELDS = frozenset(
    {"version", "path", "sha256", "counts"}
)
_BINARY_ASSET_STATUSES = frozenset({"complete", "captured_with_warning"})
_YOUTUBE_EMBED_PLAYER_REFERENCE_REASON = "youtube_embed_player_reference"
_YOUTUBE_EMBED_PLAYER_REFERENCE_RULE = (
    "youtube_embed_player_url_rejected_by_media_policy_v1"
)
_YOUTUBE_EMBED_PLAYER_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }
)
_YOUTUBE_EMBED_PLAYER_PATH = re.compile(r"^/embed/[A-Za-z0-9_-]+$")


def canonical_json_bytes(value: object) -> bytes:
    """Return the repository's canonical, newline-terminated JSON encoding."""

    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def canonical_json_sha256(value: object) -> str:
    """Hash the full canonical JSON value, including audit metadata."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def target_set_sha256(target_ids: Iterable[str]) -> str:
    """Hash a numeric-sorted Discord target ID set using the snapshot contract."""

    values = list(target_ids)
    if any(not _valid_snowflake(value) for value in values):
        raise ValueError("Discord target IDs must be snowflake strings")
    if len(values) != len(set(values)):
        raise ValueError("Discord target IDs must be unique")
    return hashlib.sha256("\n".join(sorted(values, key=int)).encode("utf-8")).hexdigest()


def build_parent_family_plan(
    snapshot: object,
    *,
    shard_count: int = 4,
    family_weights: Mapping[str, object] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build deterministic LPT shards while keeping every thread with its parent."""

    parent = _validate_parent_snapshot(snapshot)
    if isinstance(shard_count, bool) or not isinstance(shard_count, int) or shard_count <= 0:
        raise ValueError("Discord shard_count must be a positive integer")

    targets = parent["targets"]
    target_by_id = {target["id"]: target for target in targets}
    thread_ids = {
        target_id
        for target_id, target in target_by_id.items()
        if _is_explicit_thread(target)
    }
    families: dict[str, list[str]] = {
        target_id: [target_id]
        for target_id in target_by_id
        if target_id not in thread_ids
    }
    for thread_id in sorted(thread_ids, key=int):
        thread = target_by_id[thread_id]
        parent_id = thread.get("parent_id")
        if not _valid_snowflake(parent_id) or parent_id not in target_by_id:
            raise ValueError(f"Discord explicit thread parent is absent: {thread_id}")
        if parent_id in thread_ids:
            raise ValueError(f"Discord explicit thread parent cannot be a thread: {thread_id}")
        families[parent_id].append(thread_id)

    if shard_count > len(families):
        raise ValueError("Discord shard_count cannot exceed the parent-family count")
    weights, weight_details = _validate_family_weights(families, family_weights)

    assignments: list[list[str]] = [[] for _ in range(shard_count)]
    loads: list[int | float] = [0 for _ in range(shard_count)]
    for root_id in sorted(families, key=lambda item: (-weights[item], int(item))):
        destination = min(
            range(shard_count),
            key=lambda index: (loads[index], len(assignments[index]), index),
        )
        assignments[destination].append(root_id)
        loads[destination] += weights[root_id]

    parent_snapshot_sha = canonical_json_sha256(parent)
    parent_target_sha = target_set_sha256(target_by_id)
    parent_metadata = {
        key: deepcopy(value)
        for key, value in parent.items()
        if key not in {"targets", "target_count", "target_set_sha256"}
    }
    manifests: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for zero_index, family_roots in enumerate(assignments):
        index = zero_index + 1
        selected_ids = {
            target_id
            for root_id in family_roots
            for target_id in families[root_id]
        }
        selected_targets = [
            deepcopy(target) for target in targets if target["id"] in selected_ids
        ]
        selected_sha = target_set_sha256(item["id"] for item in selected_targets)
        family_estimated_weights = {
            root_id: weights[root_id] for root_id in sorted(family_roots, key=int)
        }
        family_weight_details = {
            root_id: deepcopy(weight_details[root_id])
            for root_id in sorted(family_roots, key=int)
        }
        manifest = {
            "schema_version": 1,
            "guild_id": parent["guild_id"],
            "shard_scheme": SHARD_SCHEME,
            "index": index,
            "count": shard_count,
            "parent_snapshot_sha256": parent_snapshot_sha,
            "parent_target_set_sha256": parent_target_sha,
            "parent_snapshot_metadata": deepcopy(parent_metadata),
            "family_count": len(family_roots),
            "family_root_ids": sorted(family_roots, key=int),
            "family_estimated_weights": family_estimated_weights,
            "family_weight_details": family_weight_details,
            "estimated_weight": sum(family_estimated_weights.values()),
            "target_count": len(selected_targets),
            "target_set_sha256": selected_sha,
            "targets": selected_targets,
        }
        manifest_file = f"shard-{index:02d}-of-{shard_count:02d}.json"
        manifest_sha = canonical_json_sha256(manifest)
        manifests.append(manifest)
        entries.append(
            {
                "index": index,
                "manifest_file": manifest_file,
                "manifest_sha256": manifest_sha,
                "target_count": len(selected_targets),
                "target_set_sha256": selected_sha,
                "target_ids": sorted(selected_ids, key=int),
                "family_count": len(family_roots),
                "family_root_ids": sorted(family_roots, key=int),
                "family_estimated_weights": family_estimated_weights,
                "family_weight_details": family_weight_details,
                "estimated_weight": manifest["estimated_weight"],
            }
        )

    _assert_exact_static_partition(target_by_id, manifests)
    plan = {
        "schema_version": 1,
        "plan_kind": PLAN_KIND,
        "shard_scheme": SHARD_SCHEME,
        "count": shard_count,
        "guild_id": parent["guild_id"],
        "parent_snapshot_sha256": parent_snapshot_sha,
        "parent_target_set_sha256": parent_target_sha,
        "parent_target_count": len(targets),
        "parent_family_count": len(families),
        "weight_method": "lpt",
        "weight_interface": "family-root-weight-v1",
        "shards": entries,
    }
    return plan, manifests


def write_parent_family_plan(
    *,
    workspace: str | os.PathLike[str],
    targets_path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    shard_count: int = 4,
    weights_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Safely write shard manifests followed by their immutable plan index."""

    root = _workspace_root(workspace)
    snapshot, _, _ = _read_json_relative(root, targets_path, "target snapshot")
    family_weights: Mapping[str, object] | None = None
    if weights_path is not None:
        weights, _, _ = _read_json_relative(root, weights_path, "family weights")
        if not isinstance(weights, dict) or not isinstance(weights.get("family_weights"), dict):
            raise ValueError("Discord family weights must contain a family_weights object")
        family_weights = weights["family_weights"]

    plan, manifests = build_parent_family_plan(
        snapshot,
        shard_count=shard_count,
        family_weights=family_weights,
    )
    relative_dir = _relative_path(output_dir, "shard output directory")
    destination = _safe_directory(root, relative_dir, create=True)
    for entry, manifest in zip(plan["shards"], manifests, strict=True):
        _write_exclusive_or_same(destination / entry["manifest_file"], manifest, root)
    plan_path = destination / "plan.json"
    _write_exclusive_or_same(plan_path, plan, root)
    return {
        "status": "planned",
        "plan_path": plan_path.relative_to(root).as_posix(),
        "plan_sha256": _sha256_file(plan_path),
        "parent_snapshot_sha256": plan["parent_snapshot_sha256"],
        "parent_target_set_sha256": plan["parent_target_set_sha256"],
        "parent_target_count": plan["parent_target_count"],
        "parent_family_count": plan["parent_family_count"],
        "shard_count": plan["count"],
        "shards": deepcopy(plan["shards"]),
    }


def audit_merged_shards(
    parent_snapshot: object,
    plan: object,
    shard_manifests: list[dict[str, Any]],
    merge_request: object,
    run_artifacts: list[dict[str, Any]],
    *,
    actual_plan_sha256: str,
    actual_shard_manifest_sha256s: Mapping[int, str] | None = None,
) -> dict[str, Any]:
    """Validate a logical merge without copying raw pages or media blobs."""

    parent = _validate_parent_snapshot(parent_snapshot)
    if not isinstance(plan, dict) or plan.get("plan_kind") != PLAN_KIND:
        raise ValueError("Discord shard plan kind is invalid")
    if not isinstance(merge_request, dict):
        raise ValueError("Discord merge request must be a JSON object")
    if not _valid_sha(actual_plan_sha256):
        raise ValueError("Discord actual plan hash is invalid")

    errors: list[str] = []
    parent_sha = canonical_json_sha256(parent)
    parent_target_sha = target_set_sha256(item["id"] for item in parent["targets"])
    count = plan.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("Discord shard plan count is invalid")
    expected_indices = set(range(1, count + 1))
    if plan.get("shard_scheme") != SHARD_SCHEME:
        errors.append("plan shard_scheme does not match parent-family-v1")
    if plan.get("parent_snapshot_sha256") != parent_sha:
        errors.append("plan parent_snapshot_sha256 does not match the canonical snapshot")
    if plan.get("parent_target_set_sha256") != parent_target_sha:
        errors.append("plan parent_target_set_sha256 does not match the parent target set")
    if plan.get("parent_target_count") != len(parent["targets"]):
        errors.append("plan parent_target_count does not match the parent snapshot")
    expected_parent_family_count = sum(
        not _is_explicit_thread(target) for target in parent["targets"]
    )
    if plan.get("parent_family_count") != expected_parent_family_count:
        errors.append("plan parent_family_count does not match the parent snapshot")

    plan_entries = _indexed_objects(plan.get("shards"), "plan shards")
    manifest_entries = _indexed_objects(shard_manifests, "shard manifests")
    request_entries = _indexed_objects(merge_request.get("shards"), "merge request shards")
    artifact_entries = _indexed_objects(run_artifacts, "run artifacts")
    for label, entries in (
        ("plan", plan_entries),
        ("manifest", manifest_entries),
        ("merge request", request_entries),
        ("run artifact", artifact_entries),
    ):
        if set(entries) != expected_indices:
            errors.append(f"{label} shard indices do not exactly cover 1..{count}")

    if merge_request.get("shard_scheme") != SHARD_SCHEME:
        errors.append("merge request shard_scheme does not match parent-family-v1")
    if merge_request.get("parent_snapshot_sha256") != parent_sha:
        errors.append("merge request parent_snapshot_sha256 does not match")
    if merge_request.get("plan_sha256") != actual_plan_sha256:
        errors.append("merge request plan_sha256 does not match")

    parent_by_id = {item["id"]: item for item in parent["targets"]}
    parent_ids = set(parent_by_id)
    expected_parent_metadata = {
        key: deepcopy(value)
        for key, value in parent.items()
        if key not in {"targets", "target_count", "target_set_sha256"}
    }
    target_owner: dict[str, int] = {}
    intersections: set[str] = set()
    manifest_target_sets: dict[int, set[str]] = {}
    shard_manifest_hashes: dict[str, dict[str, object]] = {}
    for index in sorted(expected_indices & set(manifest_entries) & set(plan_entries)):
        manifest = manifest_entries[index]
        entry = plan_entries[index]
        targets = manifest.get("targets")
        if not isinstance(targets, list):
            errors.append(f"shard {index} targets are invalid")
            targets = []
        ids = _target_ids_or_error(targets, f"shard {index}", errors)
        manifest_target_sets[index] = ids
        repeated = ids & set(target_owner)
        intersections.update(repeated)
        for target_id in ids:
            target_owner.setdefault(target_id, index)

        expected_manifest_sha = entry.get("manifest_sha256")
        if canonical_json_sha256(manifest) != expected_manifest_sha:
            errors.append(f"shard {index} manifest_sha256 does not match plan")
        actual_manifest_file_sha = (
            actual_shard_manifest_sha256s.get(index)
            if actual_shard_manifest_sha256s is not None
            else canonical_json_sha256(manifest)
        )
        shard_manifest_hashes[str(index)] = {
            "expected": expected_manifest_sha,
            "actual": actual_manifest_file_sha,
            "verified": (
                _valid_sha(expected_manifest_sha)
                and expected_manifest_sha == actual_manifest_file_sha
            ),
        }
        if not shard_manifest_hashes[str(index)]["verified"]:
            errors.append(f"shard {index} manifest file hash does not match plan")
        if manifest.get("shard_scheme") != SHARD_SCHEME:
            errors.append(f"shard {index} shard_scheme is invalid")
        if manifest.get("index") != index or manifest.get("count") != count:
            errors.append(f"shard {index} index/count metadata is invalid")
        if manifest.get("parent_snapshot_sha256") != parent_sha:
            errors.append(f"shard {index} parent_snapshot_sha256 does not match")
        if manifest.get("parent_target_set_sha256") != parent_target_sha:
            errors.append(f"shard {index} parent_target_set_sha256 does not match")
        if manifest.get("parent_snapshot_metadata") != expected_parent_metadata:
            errors.append(f"shard {index} parent snapshot metadata does not match")
        if manifest.get("target_count") != len(targets):
            errors.append(f"shard {index} target_count does not match")
        try:
            own_sha = target_set_sha256(ids)
        except ValueError:
            own_sha = ""
        if manifest.get("target_set_sha256") != own_sha:
            errors.append(f"shard {index} target_set_sha256 does not match")
        if entry.get("target_ids") != sorted(ids, key=int):
            errors.append(f"shard {index} target_ids do not match plan")
        if entry.get("target_set_sha256") != own_sha:
            errors.append(f"shard {index} target set hash does not match plan")
        family_roots = manifest.get("family_root_ids")
        family_estimated_weights = manifest.get("family_estimated_weights")
        family_weight_details = manifest.get("family_weight_details")
        expected_family_roots = {
            target_id
            for target_id in ids & set(parent_by_id)
            if not _is_explicit_thread(parent_by_id[target_id])
        }
        if (
            not isinstance(family_roots, list)
            or any(not _valid_snowflake(root_id) for root_id in family_roots)
            or len(family_roots) != len(set(family_roots))
            or set(family_roots) != expected_family_roots
        ):
            errors.append(f"shard {index} family_root_ids are invalid")
            family_roots = []
        if manifest.get("family_count") != len(family_roots):
            errors.append(f"shard {index} family_count is invalid")
        if (
            not isinstance(family_estimated_weights, dict)
            or set(family_estimated_weights) != set(family_roots)
            or any(
                not _valid_nonnegative_number(weight)
                for weight in family_estimated_weights.values()
            )
        ):
            errors.append(f"shard {index} family estimated weights are invalid")
            family_estimated_weights = {}
        if (
            not isinstance(family_weight_details, dict)
            or set(family_weight_details) != set(family_roots)
            or any(
                not isinstance(detail, dict)
                or detail.get("weight") != family_estimated_weights.get(root_id)
                or not isinstance(detail.get("source"), str)
                or not detail["source"].strip()
                or not isinstance(detail.get("metrics"), dict)
                for root_id, detail in family_weight_details.items()
            )
        ):
            errors.append(f"shard {index} family weight details are invalid")
        if manifest.get("estimated_weight") != sum(family_estimated_weights.values()):
            errors.append(f"shard {index} estimated shard weight is invalid")
        for field in (
            "family_root_ids",
            "family_count",
            "family_estimated_weights",
            "family_weight_details",
            "estimated_weight",
        ):
            if entry.get(field) != manifest.get(field):
                errors.append(f"shard {index} {field} does not match plan")
        for target in targets:
            if not isinstance(target, dict) or not _valid_snowflake(target.get("id")):
                continue
            if target.get("id") in parent_by_id and target != parent_by_id[target["id"]]:
                errors.append(f"shard {index} target {target['id']} metadata drifted from parent")

    static_union = set(target_owner)
    missing_static = parent_ids - static_union
    unexpected_static = static_union - parent_ids
    pairwise_disjoint = not intersections
    exact_union = not missing_static and not unexpected_static and static_union == parent_ids
    static_thread_wrong_owner = {
        target_id
        for target_id, target in parent_by_id.items()
        if _is_explicit_thread(target)
        and target_owner.get(target_id) != target_owner.get(target.get("parent_id"))
    }
    if not pairwise_disjoint:
        errors.append("shard target sets are not pairwise disjoint")
    if not exact_union:
        errors.append("shard target sets do not exactly cover the parent snapshot")
    if static_thread_wrong_owner:
        errors.append("explicit thread targets are not assigned with their parent owner")

    hash_verification: dict[str, dict[str, bool]] = {}
    artifact_hashes: dict[str, dict[str, dict[str, object]]] = {}
    stream_counts: Counter[str] = Counter()
    blocked_streams: list[dict[str, object]] = []
    failed_streams: list[dict[str, object]] = []
    truncated_streams: list[dict[str, object]] = []
    private_incomplete: list[dict[str, object]] = []
    private_blocked: list[dict[str, object]] = []
    non_private_incomplete: list[dict[str, object]] = []
    media_incomplete_shards: list[dict[str, object]] = []
    message_reference_incomplete_shards: list[dict[str, object]] = []
    discovered_by_id: dict[str, dict[str, object]] = {}
    duplicate_threads: set[str] = set()
    wrong_parent_owner: set[str] = set()
    wrong_thread_owner: set[str] = set()
    seen_run_ids: set[str] = set()

    shared_indices = (
        expected_indices
        & set(plan_entries)
        & set(manifest_entries)
        & set(request_entries)
        & set(artifact_entries)
    )
    for index in sorted(shared_indices):
        expected = request_entries[index]
        artifact = artifact_entries[index]
        request = artifact.get("request")
        run_manifest = artifact.get("manifest")
        checkpoint = artifact.get("checkpoint")
        inventory = artifact.get("targets_inventory")
        if not all(isinstance(value, dict) for value in (request, run_manifest, checkpoint, inventory)):
            errors.append(f"shard {index} run artifacts must all be JSON objects")
            continue
        assert isinstance(request, dict)
        assert isinstance(run_manifest, dict)
        assert isinstance(checkpoint, dict)
        assert isinstance(inventory, dict)

        flags: dict[str, bool] = {}
        hashes: dict[str, dict[str, object]] = {}
        for field, short_name in (
            ("request_sha256", "request"),
            ("manifest_sha256", "manifest"),
            ("checkpoint_sha256", "checkpoint"),
            ("targets_inventory_sha256", "targets_inventory"),
            ("asset_ledger_sha256", "asset_ledger"),
        ):
            expected_hash = expected.get(field)
            actual_hash = artifact.get(field)
            flags[short_name] = _valid_sha(expected_hash) and expected_hash == actual_hash
            hashes[short_name] = {
                "expected": expected_hash,
                "actual": actual_hash,
                "verified": flags[short_name],
            }
            if not flags[short_name]:
                errors.append(f"shard {index} {field} does not match")
        hash_verification[str(index)] = flags
        artifact_hashes[str(index)] = hashes

        request_run_id = request.get("run_id")
        if (
            not isinstance(request_run_id, str)
            or not request_run_id
            or run_manifest.get("run_id") != request_run_id
            or checkpoint.get("run_id") != request_run_id
        ):
            errors.append(f"shard {index} request/manifest/checkpoint run_id values differ")
        elif request_run_id in seen_run_ids:
            errors.append(f"shard {index} reuses a run_id from another shard")
        else:
            seen_run_ids.add(request_run_id)

        shard_snapshot = request.get("target_snapshot")
        planned_manifest = manifest_entries[index]
        if not isinstance(shard_snapshot, dict):
            errors.append(f"shard {index} request target_snapshot is invalid")
        else:
            request_target_sha = canonical_json_sha256(shard_snapshot)
            if request.get("target_sha256") != request_target_sha:
                errors.append(f"shard {index} request target_sha256 does not match")
            if request_target_sha != canonical_json_sha256(planned_manifest):
                errors.append(f"shard {index} request does not pin its planned shard manifest")

        streams = run_manifest.get("streams")
        checkpoint_streams = checkpoint.get("streams")
        if not isinstance(streams, dict) or not isinstance(checkpoint_streams, dict):
            errors.append(f"shard {index} stream state is invalid")
            streams = {}
            checkpoint_streams = {}
        if streams != checkpoint_streams:
            errors.append(f"shard {index} manifest streams do not match checkpoint streams")
        for stream_name, state in streams.items():
            if not isinstance(stream_name, str) or not isinstance(state, dict):
                stream_counts["unknown"] += 1
                errors.append(f"shard {index} contains an invalid stream state")
                continue
            status_value = state.get("status")
            status_name = status_value if status_value in _STREAM_STATUSES[:-1] else "unknown"
            stream_counts[status_name] += 1
            item = {
                "index": index,
                "stream": stream_name,
                "status": status_value,
                "terminal_reason": state.get("terminal_reason"),
            }
            if status_name == "blocked":
                blocked_streams.append(item)
            elif status_name == "failed":
                failed_streams.append(item)
            elif status_name == "truncated_by_limit":
                truncated_streams.append(item)
            if status_name != "complete":
                if _is_private_only_stream(stream_name):
                    private_incomplete.append(item)
                    if status_name == "blocked":
                        private_blocked.append(item)
                else:
                    non_private_incomplete.append(item)

        transitive = artifact.get("transitive_evidence")
        asset_evidence = (
            transitive.get("asset_evidence")
            if isinstance(transitive, dict)
            else None
        )
        media_recovery_audit = (
            transitive.get("media_recovery_audit")
            if isinstance(transitive, dict)
            else None
        )
        message_reference_audit = (
            transitive.get("message_reference_resolution_audit")
            if isinstance(transitive, dict)
            else None
        )
        media = run_manifest.get("media")
        media_valid, media_reason = _validate_media_state(
            media,
            asset_evidence,
            media_recovery_audit,
            checkpoint,
            request.get("options"),
        )
        if not media_valid:
            errors.append(f"shard {index} media state is invalid: {media_reason}")
        media_status = media.get("status") if isinstance(media, dict) else None
        if (
            not media_valid
            or media_status not in {"complete", "complete_with_warnings", "not_requested"}
        ):
            media_incomplete_shards.append(
                {"index": index, "status": media_status, "reason": media_reason}
            )

        reference_valid, reference_complete, reference_reason = (
            _validate_message_reference_state(
                run_manifest.get("message_evidence"),
                message_reference_audit,
            )
        )
        if not reference_valid:
            errors.append(
                f"shard {index} message reference state is invalid: "
                f"{reference_reason}"
            )
        if not reference_valid or not reference_complete:
            message_reference_incomplete_shards.append(
                {
                    "index": index,
                    "status": (
                        run_manifest.get("message_evidence", {}).get(
                            "effective_status"
                        )
                        if isinstance(run_manifest.get("message_evidence"), dict)
                        else None
                    ),
                    "reason": reference_reason,
                }
            )

        streams_complete = bool(streams) and all(
            isinstance(state, dict) and state.get("status") == "complete"
            for state in streams.values()
        )
        derived_complete = (
            streams_complete
            and media_valid
            and media_status in {
                "complete",
                "complete_with_warnings",
                "not_requested",
            }
            and reference_valid
            and reference_complete
        )
        manifest_status = run_manifest.get("status")
        message_evidence = run_manifest.get("message_evidence")
        message_effective_status = (
            message_evidence.get("effective_status")
            if isinstance(message_evidence, dict)
            else None
        )
        expected_manifest_status = (
            "partial"
            if not derived_complete
            else "complete_with_warnings"
            if (
                media_status == "complete_with_warnings"
                or (
                    reference_valid
                    and message_effective_status == "complete_with_warnings"
                )
            )
            else "complete"
        )
        if manifest_status not in {
            "complete",
            "complete_with_warnings",
            "partial",
        }:
            errors.append(f"shard {index} manifest status is invalid")
        elif manifest_status != expected_manifest_status:
            errors.append(
                f"shard {index} manifest status does not match derived state: "
                f"expected {expected_manifest_status}, got {manifest_status}"
            )
        checkpoint_errors = checkpoint.get("errors")
        if (
            not isinstance(checkpoint_errors, list)
            or run_manifest.get("errors") != len(checkpoint_errors)
        ):
            errors.append(f"shard {index} manifest error count does not match checkpoint")

        if not isinstance(transitive, dict):
            errors.append(f"shard {index} transitive evidence audit is missing")
        else:
            transitive_errors = transitive.get("validation_errors")
            if not isinstance(transitive_errors, list):
                errors.append(f"shard {index} transitive evidence errors are invalid")
            else:
                errors.extend(
                    f"shard {index} {error}"
                    for error in transitive_errors
                    if isinstance(error, str)
                )
            media_logical_keys = transitive.get("downloadable_media_logical_keys")
            ledger_logical_keys = (
                asset_evidence.get("logical_keys")
                if isinstance(asset_evidence, dict)
                else None
            )
            if not isinstance(media_logical_keys, list) or not isinstance(
                ledger_logical_keys,
                list,
            ):
                errors.append(
                    f"shard {index} media-to-asset logical-key coverage is unavailable"
                )
            elif any(
                not isinstance(logical_key, str) or not logical_key
                for logical_key in (*media_logical_keys, *ledger_logical_keys)
            ):
                errors.append(
                    f"shard {index} media-to-asset logical-key coverage is invalid"
                )
            else:
                missing_asset_keys = set(media_logical_keys) - set(ledger_logical_keys)
                if missing_asset_keys:
                    errors.append(
                        f"shard {index} downloadable media logical keys are absent from "
                        "the asset ledger: "
                        + ",".join(sorted(missing_asset_keys))
                    )

        inventory_targets = inventory.get("targets")
        if not isinstance(inventory_targets, list):
            errors.append(f"shard {index} target inventory targets are invalid")
            inventory_targets = []
        requested_by_id: dict[str, dict[str, Any]] = {}
        for record in inventory_targets:
            requested = record.get("requested") if isinstance(record, dict) else None
            target_id = requested.get("id") if isinstance(requested, dict) else None
            if not _valid_snowflake(target_id) or target_id in requested_by_id:
                errors.append(f"shard {index} target inventory requested identities are invalid")
                continue
            requested_by_id[target_id] = requested
        if set(requested_by_id) != manifest_target_sets.get(index, set()):
            errors.append(f"shard {index} target inventory does not match static targets")
        planned_by_id = {
            target["id"]: target
            for target in manifest_entries[index].get("targets", [])
            if isinstance(target, dict) and _valid_snowflake(target.get("id"))
        }
        for target_id in set(requested_by_id) & set(planned_by_id):
            if requested_by_id[target_id] != planned_by_id[target_id]:
                errors.append(f"shard {index} target {target_id} metadata drifted")
        for target_id, requested in requested_by_id.items():
            for required_stream in _required_collection_streams(target_id, requested):
                if required_stream not in streams:
                    errors.append(
                        f"shard {index} target {target_id} required stream is missing: "
                        f"{required_stream}"
                    )

        threads = inventory.get("threads")
        if not isinstance(threads, list):
            errors.append(f"shard {index} target inventory threads are invalid")
            threads = []
        local_threads: set[str] = set()
        for thread in threads:
            if not isinstance(thread, dict):
                errors.append(f"shard {index} contains invalid thread metadata")
                continue
            thread_id = thread.get("id")
            parent_id = thread.get("parent_id")
            if not _valid_snowflake(thread_id) or not _valid_snowflake(parent_id):
                errors.append(f"shard {index} contains invalid thread identity/parent metadata")
                continue
            for required_stream in (f"messages_{thread_id}", f"pins_{thread_id}"):
                if required_stream not in streams:
                    errors.append(
                        f"shard {index} thread {thread_id} required stream is missing: "
                        f"{required_stream}"
                    )
            if thread_id in local_threads or thread_id in discovered_by_id:
                duplicate_threads.add(thread_id)
            local_threads.add(thread_id)
            planned_thread = parent_by_id.get(thread_id)
            if planned_thread is not None:
                if not _is_explicit_thread(planned_thread):
                    errors.append(
                        f"shard {index} inventory thread {thread_id} collides with a "
                        "non-thread static target"
                    )
                else:
                    planned_parent_id = planned_thread.get("parent_id")
                    if parent_id != planned_parent_id:
                        errors.append(
                            f"shard {index} explicit thread {thread_id} parent metadata "
                            f"drifted from {planned_parent_id} to {parent_id}"
                        )
            if target_owner.get(parent_id) != index:
                wrong_parent_owner.add(thread_id)
            if thread_id in target_owner and target_owner[thread_id] != index:
                wrong_thread_owner.add(thread_id)
            discovered_by_id.setdefault(
                thread_id,
                {"id": thread_id, "parent_id": parent_id, "owner_index": index},
            )

    if duplicate_threads:
        errors.append("thread IDs are duplicated across or within shard inventories")
    if wrong_parent_owner:
        errors.append("thread parent ownership does not match the shard owner")
    if wrong_thread_owner:
        errors.append("explicit thread ownership does not match its inventory shard")

    for status_name in _STREAM_STATUSES:
        stream_counts.setdefault(status_name, 0)
    validation_errors = sorted(set(errors))
    incomplete = (
        any(stream_counts[name] for name in _STREAM_STATUSES if name != "complete")
        or bool(media_incomplete_shards)
        or bool(message_reference_incomplete_shards)
        or bool(non_private_incomplete)
    )
    status = "failed" if validation_errors else "partial" if incomplete else "complete"
    discovered_threads = [
        discovered_by_id[thread_id] for thread_id in sorted(discovered_by_id, key=int)
    ]
    message_bearing_static_ids = {
        target_id
        for target_id, target in parent_by_id.items()
        if _target_channel_type(target) in _MESSAGE_BEARING_TYPES
    }
    thread_parent_static_ids = {
        target_id
        for target_id, target in parent_by_id.items()
        if _target_channel_type(target) in _THREAD_PARENT_TYPES
        and not _is_explicit_thread(target)
    }
    required_target_ids = sorted(
        message_bearing_static_ids | set(discovered_by_id),
        key=int,
    )
    return {
        "schema_version": 1,
        "audit_kind": MERGE_AUDIT_KIND,
        "status": status,
        "merge_semantics": "logical-index-only-no-raw-or-blob-copy",
        "guild_id": parent["guild_id"],
        "shard_scheme": SHARD_SCHEME,
        "shard_count": count,
        "parent_snapshot_sha256": parent_sha,
        "parent_target_set_sha256": parent_target_sha,
        "plan_sha256": actual_plan_sha256,
        "static_scope": {
            "target_count": len(parent_ids),
            "pairwise_disjoint": pairwise_disjoint,
            "exact_union": exact_union,
            "intersection_target_ids": sorted(intersections, key=int),
            "missing_target_ids": sorted(missing_static, key=int),
            "unexpected_target_ids": sorted(unexpected_static, key=int),
            "wrong_owner_explicit_thread_ids": sorted(
                static_thread_wrong_owner,
                key=int,
            ),
        },
        "static_target_ids": sorted(parent_ids, key=int),
        "thread_scope": {
            "thread_count": len(discovered_threads),
            "duplicate_thread_ids": sorted(duplicate_threads, key=int),
            "wrong_parent_owner_thread_ids": sorted(wrong_parent_owner, key=int),
            "wrong_thread_owner_ids": sorted(wrong_thread_owner, key=int),
        },
        "discovered_threads": discovered_threads,
        "message_bearing_static_target_ids": sorted(
            message_bearing_static_ids,
            key=int,
        ),
        "thread_parent_static_target_ids": sorted(
            thread_parent_static_ids,
            key=int,
        ),
        "required_head_catchup_target_ids": required_target_ids,
        "artifact_hash_verification": hash_verification,
        "artifact_hashes": artifact_hashes,
        "shard_manifest_hashes": shard_manifest_hashes,
        "transitive_evidence": {
            str(index): deepcopy(artifact_entries[index].get("transitive_evidence"))
            for index in sorted(shared_indices)
        },
        "stream_status_counts": dict(stream_counts),
        "blocked_streams": blocked_streams,
        "failed_streams": failed_streams,
        "truncated_streams": truncated_streams,
        "private_archived_incomplete_streams": private_incomplete,
        "private_archived_blocked_streams": private_blocked,
        "non_private_incomplete_streams": non_private_incomplete,
        "media_incomplete_shards": media_incomplete_shards,
        "message_reference_incomplete_shards": (
            message_reference_incomplete_shards
        ),
        "validation_errors": validation_errors,
    }


def write_merged_shard_audit(
    *,
    workspace: str | os.PathLike[str],
    targets_path: str | os.PathLike[str],
    plan_path: str | os.PathLike[str],
    merge_request_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Read pinned shard artifacts and atomically write a logical merge audit."""

    root = _workspace_root(workspace)
    parent, parent_file_sha, _ = _read_json_relative(root, targets_path, "target snapshot")
    plan, plan_sha, plan_file = _read_json_relative(root, plan_path, "shard plan")
    merge_request, merge_request_sha, _ = _read_json_relative(
        root,
        merge_request_path,
        "merge request",
    )
    if not isinstance(plan, dict) or not isinstance(plan.get("shards"), list):
        raise ValueError("Discord shard plan must contain a shards list")
    if not isinstance(merge_request, dict) or not isinstance(merge_request.get("shards"), list):
        raise ValueError("Discord merge request must contain a shards list")

    plan_relative = plan_file.relative_to(root)
    shard_manifests: list[dict[str, Any]] = []
    shard_manifest_file_hashes: dict[int, str] = {}
    for entry in plan["shards"]:
        if not isinstance(entry, dict):
            raise ValueError("Discord shard plan entry must be an object")
        manifest_file = entry.get("manifest_file")
        if (
            not isinstance(manifest_file, str)
            or Path(manifest_file).name != manifest_file
            or not manifest_file.endswith(".json")
        ):
            raise ValueError("Discord shard manifest filename is invalid")
        manifest_relative = plan_relative.parent / manifest_file
        manifest, manifest_file_sha, _ = _read_json_relative(
            root,
            manifest_relative,
            "shard manifest",
        )
        if not isinstance(manifest, dict):
            raise ValueError("Discord shard manifest must be a JSON object")
        shard_manifests.append(manifest)
        index = manifest.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("Discord shard manifest index is invalid")
        shard_manifest_file_hashes[index] = manifest_file_sha

    run_artifacts: list[dict[str, Any]] = []
    seen_run_roots: set[Path] = set()
    for entry in merge_request["shards"]:
        if not isinstance(entry, dict):
            raise ValueError("Discord merge request shard entry must be an object")
        run_root_value = entry.get("run_root")
        run_relative = _relative_path(run_root_value, "run root")
        if run_relative in seen_run_roots:
            raise ValueError("Discord merge request run roots must be distinct")
        seen_run_roots.add(run_relative)
        _safe_directory(root, run_relative, create=False)
        artifact: dict[str, Any] = {"index": entry.get("index")}
        for filename, key in (
            ("request.json", "request"),
            ("manifest.json", "manifest"),
            ("checkpoint.json", "checkpoint"),
            ("inventory/targets.json", "targets_inventory"),
        ):
            payload, digest, _ = _read_json_relative(
                root,
                run_relative / filename,
                f"run {entry.get('index')} {key}",
            )
            artifact[key] = payload
            artifact[f"{key}_sha256"] = digest
        artifact["transitive_evidence"] = _audit_transitive_run_evidence(
            root,
            run_relative,
            artifact["checkpoint"],
            artifact["manifest"],
            artifact["request"],
            artifact["request_sha256"],
        )
        asset_evidence = artifact["transitive_evidence"].get("asset_evidence")
        artifact["asset_ledger_sha256"] = (
            asset_evidence.get("asset_ledger_sha256")
            if isinstance(asset_evidence, dict)
            else None
        )
        run_artifacts.append(artifact)

    audit = audit_merged_shards(
        parent,
        plan,
        shard_manifests,
        merge_request,
        run_artifacts,
        actual_plan_sha256=plan_sha,
        actual_shard_manifest_sha256s=shard_manifest_file_hashes,
    )
    audit["parent_snapshot_file_sha256"] = parent_file_sha
    audit["merge_request_sha256"] = merge_request_sha
    output_relative = _relative_path(output_path, "merge audit output")
    destination = root / output_relative
    _write_exclusive_or_same(destination, audit, root)
    return {
        "status": audit["status"],
        "output_path": output_relative.as_posix(),
        "output_sha256": _sha256_file(destination),
        "validation_error_count": len(audit["validation_errors"]),
        "blocked_stream_count": audit["stream_status_counts"]["blocked"],
        "failed_stream_count": audit["stream_status_counts"]["failed"],
        "truncated_stream_count": audit["stream_status_counts"]["truncated_by_limit"],
    }


def capture_closure_evidence(
    *,
    workspace: str | os.PathLike[str],
    targets_path: str | os.PathLike[str],
    merge_audit_path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    t_close: str,
    t_close_source_sha256: str,
    transport: Any,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Capture one immutable census plus the bounded ``(T_close, H)`` delta."""

    root = _workspace_root(workspace)
    output_relative = _relative_path(output_dir, "closure capture output directory")
    output_parent = _safe_directory(
        root,
        Path(*output_relative.parts[:-1]),
        create=True,
    )
    destination = output_parent / output_relative.name
    try:
        destination_mode = destination.lstat().st_mode
    except FileNotFoundError:
        destination_mode = None
    if destination_mode is not None:
        if stat.S_ISLNK(destination_mode):
            raise ValueError("Discord closure immutable output path is a symbolic link")
        raise ValueError("Discord closure immutable output directory already exists")

    if not _valid_sha(t_close_source_sha256):
        raise ValueError("Discord t_close_source_sha256 is invalid")
    close_time = _parse_timestamp(t_close, "t_close")
    caught_time = _ceil_millisecond((clock or _utc_now)())
    if caught_time <= close_time:
        raise ValueError("Discord closure capture H must be after t_close")
    caught_through = _format_millisecond_timestamp(caught_time)
    high_exclusive = _discord_snowflake_lower_bound(caught_time)

    snapshot_value, _, _ = _read_json_relative(root, targets_path, "target snapshot")
    snapshot = _validate_parent_snapshot(snapshot_value)
    merge, merge_file_sha, _ = _read_json_relative(
        root,
        merge_audit_path,
        "merge audit",
    )
    if not isinstance(merge, dict) or merge.get("audit_kind") != MERGE_AUDIT_KIND:
        raise ValueError("Discord merge audit kind is invalid")
    if merge.get("guild_id") != snapshot["guild_id"]:
        raise ValueError("Discord merge audit guild_id does not match target snapshot")
    if merge.get("parent_snapshot_sha256") != canonical_json_sha256(snapshot):
        raise ValueError("Discord merge audit does not bind the target snapshot")
    snapshot_ids = {target["id"] for target in snapshot["targets"]}
    if _snowflake_set(merge.get("static_target_ids"), "merge static targets") != snapshot_ids:
        raise ValueError("Discord merge static targets do not match target snapshot")

    merge_status = merge.get("status")
    merge_errors = merge.get("validation_errors")
    static_scope = merge.get("static_scope")
    if merge_status not in {"complete", "partial"}:
        raise ValueError("Discord merge audit is not usable for closure capture")
    if not isinstance(merge_errors, list) or merge_errors:
        raise ValueError("Discord merge audit has unresolved validation errors")
    if (
        not isinstance(static_scope, dict)
        or static_scope.get("exact_union") is not True
        or static_scope.get("pairwise_disjoint") is not True
    ):
        raise ValueError("Discord merge static scope is not an exact partition")

    family_parent_ids = {
        target["id"]
        for target in snapshot["targets"]
        if not _is_explicit_thread(target)
        and _target_channel_type(target) in _THREAD_PARENT_TYPES
    }
    declared_family_parents = merge.get("thread_parent_static_target_ids")
    if declared_family_parents is None:
        raise ValueError("Discord merge thread-parent scope is missing")
    if _snowflake_set(
        declared_family_parents,
        "merge thread-parent static targets",
    ) != family_parent_ids:
        raise ValueError("Discord merge thread-parent scope does not match target snapshot")

    expected_message_bearing_ids = {
        target["id"]
        for target in snapshot["targets"]
        if _target_channel_type(target) in _MESSAGE_BEARING_TYPES
    }
    if _snowflake_set(
        merge.get("message_bearing_static_target_ids"),
        "merge message-bearing static targets",
    ) != expected_message_bearing_ids:
        raise ValueError(
            "Discord merge message-bearing scope does not match target snapshot"
        )

    discovered = merge.get("discovered_threads")
    if not isinstance(discovered, list):
        raise ValueError("Discord merge discovered_threads is invalid")
    snapshot_by_id = {target["id"]: target for target in snapshot["targets"]}
    discovered_ids: set[str] = set()
    for item in discovered:
        if not isinstance(item, dict):
            raise ValueError("Discord merge discovered thread record is invalid")
        thread_id = item.get("id")
        parent_id = item.get("parent_id")
        if not _valid_snowflake(thread_id) or not _valid_snowflake(parent_id):
            raise ValueError("Discord merge discovered thread identity/parent is invalid")
        if thread_id in discovered_ids:
            raise ValueError("Discord merge discovered thread IDs are duplicated")
        discovered_ids.add(thread_id)
        if parent_id not in family_parent_ids:
            raise ValueError("Discord merge discovered thread parent is outside family scope")
        static_target = snapshot_by_id.get(thread_id)
        if static_target is not None and (
            not _is_explicit_thread(static_target)
            or static_target.get("parent_id") != parent_id
        ):
            raise ValueError("Discord merge discovered thread conflicts with static scope")

    base_required = expected_message_bearing_ids | discovered_ids
    if _snowflake_set(
        merge.get("required_head_catchup_target_ids"),
        "merge required head catch-up targets",
    ) != base_required:
        raise ValueError("Discord merge required head catch-up scope is invalid")

    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{output_relative.name}.capture-",
            dir=output_parent,
        )
    )
    published = False
    try:
        census, census_artifacts = _capture_thread_census(
            transport,
            guild_id=snapshot["guild_id"],
            family_parent_ids=family_parent_ids,
            t_close=t_close,
            t_close_source_sha256=t_close_source_sha256,
            merge_audit_file_sha256=merge_file_sha,
            caught_through=caught_through,
            high_exclusive=high_exclusive,
            output_prefix=output_relative,
        )
        required_ids = base_required | {item["id"] for item in census["threads"]}
        head, head_artifacts = _capture_head_catchup(
            transport,
            guild_id=snapshot["guild_id"],
            target_ids=required_ids,
            t_close=t_close,
            t_close_source_sha256=t_close_source_sha256,
            merge_audit_file_sha256=merge_file_sha,
            caught_through=caught_through,
            high_exclusive=high_exclusive,
            output_prefix=output_relative,
        )
        census_path = output_relative / "census.json"
        head_path = output_relative / "head-catchup.json"
        artifacts = {
            **census_artifacts,
            **head_artifacts,
            census_path: census,
            head_path: head,
        }
        for final_path, value in artifacts.items():
            try:
                staged_relative = final_path.relative_to(output_relative)
            except ValueError:
                raise AssertionError("closure capture artifact escaped output prefix") from None
            _write_exclusive_or_same(staging / staged_relative, value, staging)

        try:
            destination.lstat()
        except FileNotFoundError:
            pass
        else:
            raise ValueError("Discord closure immutable output directory already exists")
        _rename_directory_noreplace(staging, destination)
        published = True
        return {
            "status": "captured",
            "output_dir": output_relative.as_posix(),
            "census_path": census_path.as_posix(),
            "census_sha256": _sha256_file(root / census_path),
            "head_catchup_path": head_path.as_posix(),
            "head_catchup_sha256": _sha256_file(root / head_path),
            "caught_through": caught_through,
            "high_exclusive": high_exclusive,
            "census_thread_count": len(census["threads"]),
            "head_target_count": len(head["targets"]),
            "merge_audit_file_sha256": merge_file_sha,
        }
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)


def _capture_thread_census(
    transport: Any,
    *,
    guild_id: str,
    family_parent_ids: set[str],
    t_close: str,
    t_close_source_sha256: str,
    merge_audit_file_sha256: str,
    caught_through: str,
    high_exclusive: str,
    output_prefix: Path,
) -> tuple[dict[str, Any], dict[Path, object]]:
    from .connectors.discord import DiscordAPIError

    raw_descriptors: list[dict[str, Any]] = []
    artifacts: dict[Path, object] = {}
    thread_parents: dict[str, str] = {}
    thread_sources: dict[str, set[str]] = {}
    excluded_after_high: set[str] = set()
    private_blocked: set[str] = set()

    def retain_thread(
        item: object,
        *,
        source: str,
        expected_parent_id: str | None,
    ) -> None:
        if not isinstance(item, dict):
            raise ValueError(f"Discord census {source} thread is not an object")
        thread_id = item.get("id")
        parent_id = item.get("parent_id")
        thread_type = item.get("type")
        if (
            not _valid_snowflake(thread_id)
            or not _valid_snowflake(parent_id)
            or thread_type not in _THREAD_TYPES
            or item.get("guild_id", guild_id) != guild_id
        ):
            raise ValueError(f"Discord census {source} thread identity is invalid")
        if expected_parent_id is not None and parent_id != expected_parent_id:
            raise ValueError(f"Discord census {source} thread parent is invalid")
        if source == "public_archived" and thread_type not in {10, 11}:
            raise ValueError("Discord public archive returned a non-public thread")
        if source in {"private_archived", "joined_private_archived"} and thread_type != 12:
            raise ValueError("Discord private archive returned a non-private thread")
        if parent_id not in family_parent_ids:
            if source == "active":
                return
            raise ValueError("Discord archive thread parent is outside target families")
        if int(thread_id) >= int(high_exclusive):
            excluded_after_high.add(thread_id)
            return
        prior_parent = thread_parents.setdefault(thread_id, parent_id)
        if prior_parent != parent_id:
            raise ValueError("Discord census thread parent differs across endpoints")
        thread_sources.setdefault(thread_id, set()).add(source)

    active_path = f"/guilds/{guild_id}/threads/active"
    active_request = {"method": "GET", "path": active_path, "params": {}}
    active_payload = transport.get_json(active_path, {})
    if (
        not isinstance(active_payload, dict)
        or not isinstance(active_payload.get("threads"), list)
        or not isinstance(active_payload.get("members"), list)
    ):
        raise ValueError("Discord active thread census payload is invalid")
    if not _active_thread_census_order_is_valid(active_payload["threads"]):
        raise ValueError("Discord active thread census order is invalid")
    for thread in active_payload["threads"]:
        retain_thread(thread, source="active", expected_parent_id=None)
    active_response = {
        "status_code": 200,
        "payload": deepcopy(active_payload),
        "next_cursor": None,
        "terminal": True,
        "terminal_reason": "single_response",
    }
    _append_census_raw_page(
        raw_descriptors,
        artifacts,
        output_prefix=output_prefix,
        guild_id=guild_id,
        parent_id=None,
        source="active",
        page_number=1,
        t_close=t_close,
        t_close_source_sha256=t_close_source_sha256,
        caught_through=caught_through,
        request=active_request,
        response=active_response,
    )

    source_specs = (
        ("public_archived", "/channels/{parent}/threads/archived/public", "timestamp"),
        ("private_archived", "/channels/{parent}/threads/archived/private", "timestamp"),
        (
            "joined_private_archived",
            "/channels/{parent}/users/@me/threads/archived/private",
            "snowflake",
        ),
    )
    for parent_id in sorted(family_parent_ids, key=int):
        for source, path_template, cursor_kind in source_specs:
            path = path_template.format(parent=parent_id)
            before: str | None = None
            seen_cursors: set[str] = set()
            seen_thread_ids: set[str] = set()
            page_number = 0
            while True:
                page_number += 1
                params: dict[str, object] = {"limit": 100}
                if before is not None:
                    params["before"] = before
                request = {"method": "GET", "path": path, "params": params}
                try:
                    payload = transport.get_json(path, params)
                except DiscordAPIError as exc:
                    if source != "private_archived" or exc.status_code != 403:
                        raise
                    private_blocked.add(parent_id)
                    response = {
                        "status_code": 403,
                        "payload": None,
                        "next_cursor": None,
                        "terminal": True,
                        "terminal_reason": "authorized_scope_private_archive_403",
                    }
                    _append_census_raw_page(
                        raw_descriptors,
                        artifacts,
                        output_prefix=output_prefix,
                        guild_id=guild_id,
                        parent_id=parent_id,
                        source=source,
                        page_number=page_number,
                        t_close=t_close,
                        t_close_source_sha256=t_close_source_sha256,
                        caught_through=caught_through,
                        request=request,
                        response=response,
                    )
                    break
                if (
                    not isinstance(payload, dict)
                    or not isinstance(payload.get("threads"), list)
                    or not isinstance(payload.get("members"), list)
                    or not isinstance(payload.get("has_more"), bool)
                ):
                    raise ValueError(f"Discord census {source} payload is invalid")
                threads = payload["threads"]
                if len(threads) > 100:
                    raise ValueError(f"Discord census {source} page exceeds limit")
                page_ids: list[str] = []
                page_cursors: list[str] = []
                for thread in threads:
                    retain_thread(
                        thread,
                        source=source,
                        expected_parent_id=parent_id,
                    )
                    assert isinstance(thread, dict)
                    thread_id = thread["id"]
                    if thread_id in seen_thread_ids:
                        raise ValueError(f"Discord census {source} repeats a thread")
                    seen_thread_ids.add(thread_id)
                    page_ids.append(thread_id)
                    page_cursors.append(
                        _census_archive_cursor(thread, cursor_kind, source)
                    )
                if page_cursors != sorted(
                    page_cursors,
                    key=(lambda value: int(value)) if cursor_kind == "snowflake" else _timestamp_sort_key,
                    reverse=True,
                ) or len(page_cursors) != len(set(page_cursors)):
                    raise ValueError(f"Discord census {source} page order is invalid")
                has_more = payload["has_more"]
                if has_more and not threads:
                    raise ValueError(f"Discord census {source} cannot advance an empty page")
                next_cursor = page_cursors[-1] if has_more else None
                if next_cursor is not None:
                    if next_cursor in seen_cursors or (
                        before is not None
                        and not _census_cursor_decreases(before, next_cursor, cursor_kind)
                    ):
                        raise ValueError(f"Discord census {source} cursor did not decrease")
                    seen_cursors.add(next_cursor)
                response = {
                    "status_code": 200,
                    "payload": deepcopy(payload),
                    "next_cursor": next_cursor,
                    "terminal": not has_more,
                    "terminal_reason": "has_more_false" if not has_more else None,
                }
                _append_census_raw_page(
                    raw_descriptors,
                    artifacts,
                    output_prefix=output_prefix,
                    guild_id=guild_id,
                    parent_id=parent_id,
                    source=source,
                    page_number=page_number,
                    t_close=t_close,
                    t_close_source_sha256=t_close_source_sha256,
                    caught_through=caught_through,
                    request=request,
                    response=response,
                )
                if not has_more:
                    break
                before = next_cursor

    threads = [
        {"id": thread_id, "parent_id": thread_parents[thread_id]}
        for thread_id in sorted(thread_parents, key=int)
    ]
    census = {
        "schema_version": 1,
        "audit_kind": CLOSURE_CENSUS_KIND,
        "guild_id": guild_id,
        "t_close": t_close,
        "t_close_source_sha256": t_close_source_sha256,
        "merge_audit_file_sha256": merge_audit_file_sha256,
        "caught_through": caught_through,
        "high_exclusive": high_exclusive,
        "family_parent_ids": sorted(family_parent_ids, key=int),
        "threads": threads,
        "thread_sources": {
            thread_id: sorted(thread_sources[thread_id])
            for thread_id in sorted(thread_sources, key=int)
        },
        "raw_pages": raw_descriptors,
        "limitations": {
            "private_archived_403_parent_ids": sorted(private_blocked, key=int),
            "full_private_archive_scope_complete": not private_blocked,
            "thread_state_observed_after_h_not_as_of_h": True,
            "thread_id_before_h_only_constrains_creation_time": True,
            "archive_delete_permission_race_after_h": True,
            "excluded_thread_ids_at_or_after_h": sorted(excluded_after_high, key=int),
            "pins_current_snapshot_non_as_of": True,
            "pins_included_in_h_claim": False,
        },
    }
    return census, artifacts


def _append_census_raw_page(
    descriptors: list[dict[str, Any]],
    artifacts: dict[Path, object],
    *,
    output_prefix: Path,
    guild_id: str,
    parent_id: str | None,
    source: str,
    page_number: int,
    t_close: str,
    t_close_source_sha256: str,
    caught_through: str,
    request: dict[str, object],
    response: dict[str, object],
) -> None:
    family = parent_id if parent_id is not None else "guild"
    path = output_prefix / "raw" / "census" / family / source / f"{page_number:06d}.json"
    raw_page = {
        "schema_version": 1,
        "audit_kind": _CENSUS_RAW_PAGE_KIND,
        "guild_id": guild_id,
        "parent_id": parent_id,
        "source": source,
        "t_close": t_close,
        "t_close_source_sha256": t_close_source_sha256,
        "caught_through": caught_through,
        "request": request,
        "response": response,
    }
    descriptors.append(
        {
            "source": source,
            "parent_id": parent_id,
            "path": path.as_posix(),
            "sha256": canonical_json_sha256(raw_page),
            "request_sha256": canonical_json_sha256(request),
            "response_sha256": canonical_json_sha256(response),
        }
    )
    artifacts[path] = raw_page


def _capture_head_catchup(
    transport: Any,
    *,
    guild_id: str,
    target_ids: set[str],
    t_close: str,
    t_close_source_sha256: str,
    merge_audit_file_sha256: str,
    caught_through: str,
    high_exclusive: str,
    output_prefix: Path,
) -> tuple[dict[str, Any], dict[Path, object]]:
    close_time = _parse_timestamp(t_close, "t_close")
    lower_bound = _discord_snowflake_lower_bound(close_time)
    artifacts: dict[Path, object] = {}
    target_descriptors: list[dict[str, Any]] = []
    for target_id in sorted(target_ids, key=int):
        raw_descriptors: list[dict[str, Any]] = []
        new_message_ids: list[str] = []
        new_thread_ids: list[str] = []
        seen_message_ids: set[str] = set()
        seen_thread_ids: set[str] = set()
        before = high_exclusive
        page_number = 0
        while True:
            page_number += 1
            path = f"/channels/{target_id}/messages"
            params = {"before": before, "limit": 100}
            request = {"method": "GET", "path": path, "params": params}
            payload = transport.get_json(path, params)
            if not isinstance(payload, list):
                raise ValueError(f"Discord head catch-up {target_id} payload is not a list")
            if len(payload) > 100:
                raise ValueError(f"Discord head catch-up {target_id} exceeds page limit")
            page_ids: list[str] = []
            page_threads: list[dict[str, str]] = []
            for message in payload:
                if not isinstance(message, dict):
                    raise ValueError(f"Discord head catch-up {target_id} message is invalid")
                message_id = message.get("id")
                if not _valid_snowflake(message_id) or message.get("channel_id") != target_id:
                    raise ValueError(
                        f"Discord head catch-up {target_id} message identity/channel is invalid"
                    )
                if int(message_id) >= int(before) or message_id in seen_message_ids:
                    raise ValueError(
                        f"Discord head catch-up {target_id} message boundary/duplicate is invalid"
                    )
                seen_message_ids.add(message_id)
                page_ids.append(message_id)
                if int(lower_bound) < int(message_id) < int(high_exclusive):
                    new_message_ids.append(message_id)
                embedded = message.get("thread")
                if embedded is not None:
                    if (
                        not isinstance(embedded, dict)
                        or not _valid_snowflake(embedded.get("id"))
                        or embedded.get("parent_id") != target_id
                        or embedded.get("type") not in _THREAD_TYPES
                    ):
                        raise ValueError(
                            f"Discord head catch-up {target_id} embedded thread is invalid"
                        )
                    thread_id = embedded["id"]
                    if thread_id in seen_thread_ids:
                        raise ValueError(
                            f"Discord head catch-up {target_id} repeats an embedded thread"
                        )
                    seen_thread_ids.add(thread_id)
                    page_threads.append({"id": thread_id, "parent_id": target_id})
                    if int(lower_bound) < int(thread_id) < int(high_exclusive):
                        new_thread_ids.append(thread_id)
            if (
                page_ids != sorted(page_ids, key=int, reverse=True)
                or len(page_ids) != len(set(page_ids))
            ):
                raise ValueError(f"Discord head catch-up {target_id} messages are out of order")
            crossed_lower = bool(page_ids and int(min(page_ids, key=int)) <= int(lower_bound))
            empty_page = not page_ids
            short_page = len(page_ids) < 100
            terminal = crossed_lower or empty_page or short_page
            terminal_reason = (
                "crossed_lower_bound"
                if crossed_lower
                else "empty_page"
                if empty_page
                else "short_page"
                if short_page
                else None
            )
            next_cursor = None if terminal else min(page_ids, key=int)
            response = {
                "status_code": 200,
                "messages": deepcopy(payload),
                "threads": page_threads,
                "next_cursor": next_cursor,
                "terminal": terminal,
                "terminal_reason": terminal_reason,
            }
            raw_path = (
                output_prefix
                / "raw"
                / "head"
                / target_id
                / f"{page_number:06d}.json"
            )
            raw_page = {
                "schema_version": 1,
                "audit_kind": _HEAD_CATCHUP_RAW_PAGE_KIND,
                "guild_id": guild_id,
                "target_id": target_id,
                "t_close": t_close,
                "t_close_source_sha256": t_close_source_sha256,
                "caught_through": caught_through,
                "request": request,
                "response": response,
            }
            raw_descriptors.append(
                {
                    "path": raw_path.as_posix(),
                    "sha256": canonical_json_sha256(raw_page),
                    "request_sha256": canonical_json_sha256(request),
                    "response_sha256": canonical_json_sha256(response),
                }
            )
            artifacts[raw_path] = raw_page
            if terminal:
                break
            assert next_cursor is not None
            before = next_cursor

        evidence_path = output_prefix / "evidence" / "head" / f"{target_id}.json"
        evidence = {
            "schema_version": 1,
            "audit_kind": _HEAD_CATCHUP_EVIDENCE_KIND,
            "guild_id": guild_id,
            "target_id": target_id,
            "t_close": t_close,
            "t_close_source_sha256": t_close_source_sha256,
            "caught_through": caught_through,
            "high_exclusive": high_exclusive,
            "new_message_count": len(new_message_ids),
            "new_message_ids": new_message_ids,
            "new_thread_count": len(new_thread_ids),
            "new_thread_ids": new_thread_ids,
            "raw_pages": raw_descriptors,
        }
        artifacts[evidence_path] = evidence
        target_descriptors.append(
            {
                "id": target_id,
                "caught_through": caught_through,
                "evidence_path": evidence_path.as_posix(),
                "evidence_sha256": canonical_json_sha256(evidence),
                "new_message_count": len(new_message_ids),
                "new_message_ids": new_message_ids,
                "new_thread_count": len(new_thread_ids),
                "new_thread_ids": new_thread_ids,
            }
        )

    return (
        {
            "schema_version": 1,
            "guild_id": guild_id,
            "t_close": t_close,
            "t_close_source_sha256": t_close_source_sha256,
            "merge_audit_file_sha256": merge_audit_file_sha256,
            "caught_through": caught_through,
            "high_exclusive": high_exclusive,
            "required_target_ids": sorted(target_ids, key=int),
            "targets": target_descriptors,
            "limitations": {
                "pins_current_snapshot_non_as_of": True,
                "pins_included_in_h_claim": False,
                "message_empty_page_may_reflect_current_permission_scope": True,
            },
        },
        artifacts,
    )


def _census_archive_cursor(
    thread: Mapping[str, object],
    cursor_kind: str,
    source: str,
) -> str:
    if cursor_kind == "snowflake":
        return str(thread["id"])
    metadata = thread.get("thread_metadata")
    cursor = metadata.get("archive_timestamp") if isinstance(metadata, dict) else None
    _parse_timestamp(cursor, f"census {source} archive cursor")
    assert isinstance(cursor, str)
    return cursor


def _timestamp_sort_key(value: str) -> datetime:
    return _parse_timestamp(value, "census archive cursor")


def _census_cursor_decreases(previous: str, current: str, cursor_kind: str) -> bool:
    if cursor_kind == "snowflake":
        return int(current) < int(previous)
    return _timestamp_sort_key(current) < _timestamp_sort_key(previous)


def _ceil_millisecond(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Discord closure clock must return a timezone-aware datetime")
    utc = value.astimezone(timezone.utc)
    remainder = utc.microsecond % 1_000
    if remainder:
        utc += timedelta(microseconds=1_000 - remainder)
    return utc


def _format_millisecond_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish a staged directory without replacing any entry."""

    if os.name == "nt":
        os.rename(source, destination)
        return

    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename_exclusive = getattr(libc, "renamex_np", None)
        if rename_exclusive is None:
            raise OSError(errno.ENOTSUP, "atomic no-clobber rename is unavailable")
        rename_exclusive.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(source_bytes, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        rename_exclusive = getattr(libc, "renameat2", None)
        if rename_exclusive is None:
            raise OSError(errno.ENOTSUP, "atomic no-clobber rename is unavailable")
        rename_exclusive.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(
            -100,
            source_bytes,
            -100,
            destination_bytes,
            0x00000001,
        )
    else:
        raise OSError(errno.ENOTSUP, "atomic no-clobber rename is unavailable")

    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(
            error_number,
            os.strerror(error_number),
            os.fspath(destination),
        )
    raise OSError(
        error_number,
        os.strerror(error_number),
        os.fspath(destination),
    )


def _rename_directory_noreplace_at(
    source_name: str,
    destination_name: str,
    parent_fd: int,
) -> None:
    """Atomically publish a direct child of ``parent_fd`` without clobbering."""

    if not source_name or not destination_name or "/" in source_name or "/" in destination_name:
        raise ValueError("Discord atomic publication names must be direct children")
    if os.name == "nt":
        os.rename(source_name, destination_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        return

    source_bytes = os.fsencode(source_name)
    destination_bytes = os.fsencode(destination_name)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename_exclusive = getattr(libc, "renameatx_np", None)
        if rename_exclusive is None:
            raise OSError(errno.ENOTSUP, "atomic no-clobber rename is unavailable")
        rename_exclusive.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(
            parent_fd,
            source_bytes,
            parent_fd,
            destination_bytes,
            0x00000004,
        )
    elif sys.platform.startswith("linux"):
        rename_exclusive = getattr(libc, "renameat2", None)
        if rename_exclusive is None:
            raise OSError(errno.ENOTSUP, "atomic no-clobber rename is unavailable")
        rename_exclusive.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(
            parent_fd,
            source_bytes,
            parent_fd,
            destination_bytes,
            0x00000001,
        )
    else:
        raise OSError(errno.ENOTSUP, "atomic no-clobber rename is unavailable")

    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(error_number, os.strerror(error_number), destination_name)
    raise OSError(error_number, os.strerror(error_number), destination_name)


def _validate_census_evidence(
    census: dict[str, Any],
    verified_pages: Mapping[str, object] | None,
    *,
    guild_id: object,
    t_close: datetime,
    t_close_source_sha256: str,
    caught_through: datetime,
    high_exclusive: str,
    expected_family_parent_ids: set[str] | None,
    errors: list[str],
) -> tuple[dict[str, str], set[str]]:
    if census.get("audit_kind") != CLOSURE_CENSUS_KIND:
        errors.append("closure census audit_kind is invalid")
    family_value = census.get("family_parent_ids")
    try:
        family_parent_ids = _snowflake_set(
            family_value,
            "closure census family parents",
        )
    except ValueError as exc:
        errors.append(str(exc))
        family_parent_ids = set()
    if (
        expected_family_parent_ids is not None
        and family_parent_ids != expected_family_parent_ids
    ):
        errors.append("closure census family-parent scope does not match merge audit")
    limitations = census.get("limitations")
    if not isinstance(limitations, dict):
        errors.append("closure census limitations are invalid")
    elif (
        limitations.get("thread_state_observed_after_h_not_as_of_h") is not True
        or limitations.get("thread_id_before_h_only_constrains_creation_time") is not True
        or limitations.get("archive_delete_permission_race_after_h") is not True
        or limitations.get("pins_current_snapshot_non_as_of") is not True
        or limitations.get("pins_included_in_h_claim") is not False
    ):
        errors.append("closure census race/pins limitations are incomplete")

    descriptors = census.get("raw_pages")
    if not isinstance(descriptors, list) or not descriptors:
        errors.append("closure census raw_pages must be a non-empty list")
        return {}, set()
    if not isinstance(verified_pages, Mapping):
        errors.append("closure census raw pages were not read")
        return {}, set()

    declared_paths: set[str] = set()
    grouped: dict[tuple[str, str | None], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for page_number, descriptor in enumerate(descriptors, start=1):
        label = f"census raw page {page_number}"
        if not isinstance(descriptor, dict):
            errors.append(f"{label} descriptor is not an object")
            continue
        source = descriptor.get("source")
        parent_id = descriptor.get("parent_id")
        if source not in {
            "active",
            "public_archived",
            "private_archived",
            "joined_private_archived",
        }:
            errors.append(f"{label} source is invalid")
            continue
        if source == "active":
            if parent_id is not None:
                errors.append(f"{label} active parent_id must be null")
                continue
        elif not _valid_snowflake(parent_id) or parent_id not in family_parent_ids:
            errors.append(f"{label} parent_id is outside census families")
            continue
        path = descriptor.get("path")
        try:
            _relative_path(path, label)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        assert isinstance(path, str)
        if path in declared_paths:
            errors.append(f"{label} path is duplicated")
            continue
        declared_paths.add(path)
        expected_sha = descriptor.get("sha256")
        request_sha = descriptor.get("request_sha256")
        response_sha = descriptor.get("response_sha256")
        if not all(_valid_sha(value) for value in (expected_sha, request_sha, response_sha)):
            errors.append(f"{label} hashes are invalid")
            continue
        verified = verified_pages.get(path)
        if not isinstance(verified, dict):
            errors.append(f"{label} was not read")
            continue
        if isinstance(verified.get("read_error"), str):
            errors.append(f"{label} is missing or unsafe: {verified['read_error']}")
            continue
        raw_page = verified.get("payload")
        if verified.get("file_sha256") != expected_sha:
            errors.append(f"{label} hash mismatch")
        if not isinstance(raw_page, dict):
            errors.append(f"{label} is not a JSON object")
            continue
        if (
            raw_page.get("schema_version") != 1
            or raw_page.get("audit_kind") != _CENSUS_RAW_PAGE_KIND
            or raw_page.get("guild_id") != guild_id
            or raw_page.get("source") != source
            or raw_page.get("parent_id") != parent_id
            or raw_page.get("t_close_source_sha256") != t_close_source_sha256
        ):
            errors.append(f"{label} identity is invalid")
        try:
            page_close = _parse_timestamp(raw_page.get("t_close"), f"{label} t_close")
            page_caught = _parse_timestamp(
                raw_page.get("caught_through"),
                f"{label} caught_through",
            )
        except ValueError as exc:
            errors.append(str(exc))
        else:
            if page_close != t_close or page_caught != caught_through:
                errors.append(f"{label} time bounds mismatch")
        request = raw_page.get("request")
        response = raw_page.get("response")
        if not isinstance(request, dict) or canonical_json_sha256(request) != request_sha:
            errors.append(f"{label} request commitment mismatch")
            continue
        if not isinstance(response, dict) or canonical_json_sha256(response) != response_sha:
            errors.append(f"{label} response commitment mismatch")
            continue
        grouped.setdefault((source, parent_id), []).append((request, response))

    unexpected_paths = set(verified_pages) - declared_paths
    if unexpected_paths:
        errors.append("closure census raw pages contain unexpected paths")
    expected_streams = {("active", None)} | {
        (source, parent_id)
        for parent_id in family_parent_ids
        for source in (
            "public_archived",
            "private_archived",
            "joined_private_archived",
        )
    }
    if set(grouped) != expected_streams:
        errors.append("closure census raw streams do not exactly cover target families")

    reconstructed: dict[str, str] = {}
    reconstructed_sources: dict[str, set[str]] = {}
    blocked_private: set[str] = set()
    for stream in sorted(
        grouped,
        key=lambda item: (
            -1 if item[1] is None else int(item[1]),
            item[0],
        ),
    ):
        source, parent_id = stream
        pages = grouped[stream]
        expected_before: str | None = None
        terminal_seen = False
        stream_thread_ids: set[str] = set()
        for index, (request, response) in enumerate(pages, start=1):
            label = f"census raw page {source}/{parent_id or 'guild'}/{index}"
            if terminal_seen:
                errors.append(f"{label} appears after a terminal page")
            expected_path = (
                f"/guilds/{guild_id}/threads/active"
                if source == "active"
                else f"/channels/{parent_id}/threads/archived/public"
                if source == "public_archived"
                else f"/channels/{parent_id}/threads/archived/private"
                if source == "private_archived"
                else f"/channels/{parent_id}/users/@me/threads/archived/private"
            )
            expected_params: dict[str, object] = {} if source == "active" else {"limit": 100}
            if expected_before is not None:
                expected_params["before"] = expected_before
            if (
                request.get("method") != "GET"
                or request.get("path") != expected_path
                or request.get("params") != expected_params
            ):
                errors.append(f"{label} request is invalid")
            status_code = response.get("status_code")
            if status_code == 403:
                if (
                    source != "private_archived"
                    or len(pages) != 1
                    or response.get("payload") is not None
                    or response.get("terminal") is not True
                    or response.get("terminal_reason")
                    != "authorized_scope_private_archive_403"
                    or response.get("next_cursor") is not None
                ):
                    errors.append(f"{label} 403 limitation is invalid")
                else:
                    assert parent_id is not None
                    blocked_private.add(parent_id)
                terminal_seen = True
                continue
            payload = response.get("payload")
            if status_code != 200 or not isinstance(payload, dict):
                errors.append(f"{label} response status/payload is invalid")
                continue
            threads = payload.get("threads")
            members = payload.get("members")
            if not isinstance(threads, list) or not isinstance(members, list):
                errors.append(f"{label} response collections are invalid")
                continue
            if len(threads) > 100 and source != "active":
                errors.append(f"{label} response exceeds the requested limit")
            page_cursors: list[str] = []
            for thread in threads:
                if not isinstance(thread, dict):
                    errors.append(f"{label} thread is not an object")
                    continue
                thread_id = thread.get("id")
                actual_parent = thread.get("parent_id")
                thread_type = thread.get("type")
                if (
                    not _valid_snowflake(thread_id)
                    or not _valid_snowflake(actual_parent)
                    or thread_type not in _THREAD_TYPES
                    or thread.get("guild_id", guild_id) != guild_id
                ):
                    errors.append(f"{label} thread identity is invalid")
                    continue
                if source != "active" and actual_parent != parent_id:
                    errors.append(f"{label} thread parent is invalid")
                    continue
                if source == "public_archived" and thread_type not in {10, 11}:
                    errors.append(f"{label} returned a non-public thread")
                if source in {"private_archived", "joined_private_archived"} and thread_type != 12:
                    errors.append(f"{label} returned a non-private thread")
                if source != "active":
                    page_cursors.append(
                        _census_archive_cursor(
                            thread,
                            "snowflake" if source == "joined_private_archived" else "timestamp",
                            source,
                        )
                    )
                if actual_parent not in family_parent_ids or int(thread_id) >= int(high_exclusive):
                    continue
                if thread_id in stream_thread_ids:
                    errors.append(f"{label} repeats a thread in one stream")
                stream_thread_ids.add(thread_id)
                prior = reconstructed.setdefault(thread_id, actual_parent)
                if prior != actual_parent:
                    errors.append(f"{label} thread parent differs across streams")
                reconstructed_sources.setdefault(thread_id, set()).add(source)
            if source == "active" and not _active_thread_census_order_is_valid(threads):
                errors.append("active thread census order is invalid")
            if source != "active":
                cursor_kind = (
                    "snowflake" if source == "joined_private_archived" else "timestamp"
                )
                if (
                    page_cursors
                    != sorted(
                        page_cursors,
                        key=(lambda value: int(value))
                        if cursor_kind == "snowflake"
                        else _timestamp_sort_key,
                        reverse=True,
                    )
                    or len(page_cursors) != len(set(page_cursors))
                ):
                    errors.append(f"{label} cursor order is invalid")
                if (
                    expected_before is not None
                    and page_cursors
                    and not _census_cursor_decreases(
                        expected_before,
                        page_cursors[0],
                        cursor_kind,
                    )
                ):
                    errors.append(f"{label} content does not follow request.before")
            if source == "active":
                if (
                    len(pages) != 1
                    or response.get("terminal") is not True
                    or response.get("terminal_reason") != "single_response"
                    or response.get("next_cursor") is not None
                ):
                    errors.append(f"{label} active terminal evidence is invalid")
                terminal_seen = True
                continue
            has_more = payload.get("has_more")
            if not isinstance(has_more, bool):
                errors.append(f"{label} has_more is invalid")
                continue
            derived_next = page_cursors[-1] if has_more and page_cursors else None
            if has_more and not page_cursors:
                errors.append(f"{label} cannot advance an empty page")
            if (
                response.get("next_cursor") != derived_next
                or response.get("terminal") is not (not has_more)
                or response.get("terminal_reason")
                != ("has_more_false" if not has_more else None)
            ):
                errors.append(f"{label} pagination evidence is invalid")
            if not has_more:
                terminal_seen = True
            expected_before = derived_next
        if not terminal_seen:
            errors.append(f"census raw stream {source}/{parent_id or 'guild'} is not terminal")

    declared_threads = census.get("threads")
    declared_map: dict[str, str] = {}
    if isinstance(declared_threads, list):
        for item in declared_threads:
            if (
                isinstance(item, dict)
                and _valid_snowflake(item.get("id"))
                and _valid_snowflake(item.get("parent_id"))
            ):
                declared_map[item["id"]] = item["parent_id"]
    if reconstructed != declared_map:
        errors.append("closure census raw thread union does not match summary")
    declared_sources = census.get("thread_sources")
    expected_sources = {
        thread_id: sorted(sources)
        for thread_id, sources in sorted(
            reconstructed_sources.items(),
            key=lambda item: int(item[0]),
        )
    }
    if declared_sources != expected_sources:
        errors.append("closure census raw thread sources do not match summary")
    declared_blocked = (
        limitations.get("private_archived_403_parent_ids")
        if isinstance(limitations, dict)
        else None
    )
    try:
        declared_blocked_set = _snowflake_set(
            declared_blocked,
            "closure census private 403 parents",
        )
    except ValueError as exc:
        errors.append(str(exc))
        declared_blocked_set = set()
    if blocked_private != declared_blocked_set:
        errors.append("closure census private 403 summary does not match raw evidence")
    if isinstance(limitations, dict) and limitations.get(
        "full_private_archive_scope_complete"
    ) is not (not blocked_private):
        errors.append("closure census full-private summary does not match raw evidence")
    return reconstructed, blocked_private


def audit_closure(
    merge_audit: object,
    census: object,
    head_catchup: object,
    *,
    t_close: str,
    verified_head_evidence: Mapping[str, object] | None = None,
    verified_census_evidence: Mapping[str, object] | None = None,
    require_verified_census: bool = False,
    actual_merge_audit_file_sha256: str | None = None,
) -> dict[str, Any]:
    """Compare a T_close census and forward catch-up against a logical merge."""

    if not isinstance(merge_audit, dict) or merge_audit.get("audit_kind") != MERGE_AUDIT_KIND:
        raise ValueError("Discord merge audit kind is invalid")
    if not isinstance(census, dict) or not isinstance(head_catchup, dict):
        raise ValueError("Discord closure inputs must be JSON objects")
    close_time = _parse_timestamp(t_close, "t_close")
    census_time = _parse_timestamp(census.get("t_close"), "census t_close")
    catchup_time = _parse_timestamp(head_catchup.get("t_close"), "head-catchup t_close")
    if census_time != close_time or catchup_time != close_time:
        raise ValueError("Discord closure inputs must pin the requested t_close")
    guild_id = merge_audit.get("guild_id")
    if census.get("guild_id") != guild_id or head_catchup.get("guild_id") != guild_id:
        raise ValueError("Discord closure guild_id values do not match")
    census_t_close_source = census.get("t_close_source_sha256")
    head_t_close_source = head_catchup.get("t_close_source_sha256")
    if (
        not _valid_sha(census_t_close_source)
        or not _valid_sha(head_t_close_source)
        or census_t_close_source != head_t_close_source
    ):
        raise ValueError(
            "Discord closure inputs must share a valid t_close_source_sha256"
        )
    merge_t_close_source = merge_audit.get("t_close_source_sha256")
    if merge_t_close_source is not None and merge_t_close_source != census_t_close_source:
        raise ValueError("Discord merge t_close_source_sha256 does not match closure")
    census_caught_through = _parse_timestamp(
        census.get("caught_through"),
        "closure census caught_through",
    )
    head_caught_through = _parse_timestamp(
        head_catchup.get("caught_through"),
        "head-catchup caught_through",
    )
    if census_caught_through != head_caught_through:
        raise ValueError("Discord closure inputs must share one caught_through boundary")
    if (
        census_caught_through <= close_time
        or census_caught_through.microsecond % 1_000
    ):
        raise ValueError(
            "Discord closure caught_through must be a millisecond-aligned "
            "exclusive boundary after t_close"
        )
    common_high_exclusive = _discord_snowflake_lower_bound(census_caught_through)
    if (
        census.get("high_exclusive") != common_high_exclusive
        or head_catchup.get("high_exclusive") != common_high_exclusive
    ):
        raise ValueError("Discord closure high_exclusive commitment mismatch")

    static_ids = _snowflake_set(merge_audit.get("static_target_ids"), "merge static targets")
    discovered = merge_audit.get("discovered_threads")
    if not isinstance(discovered, list):
        raise ValueError("Discord merge discovered_threads is invalid")
    merged_threads: dict[str, str] = {}
    for item in discovered:
        if not isinstance(item, dict):
            raise ValueError("Discord merge thread record is invalid")
        thread_id = item.get("id")
        parent_id = item.get("parent_id")
        if not _valid_snowflake(thread_id) or not _valid_snowflake(parent_id):
            raise ValueError("Discord merge thread identity/parent is invalid")
        if thread_id in merged_threads:
            raise ValueError("Discord merge thread IDs are duplicated")
        merged_threads[thread_id] = parent_id

    census_threads_raw = census.get("threads")
    if not isinstance(census_threads_raw, list):
        raise ValueError("Discord closure census threads must be a list")
    census_threads: dict[str, str] = {}
    census_threads_at_or_after_high: set[str] = set()
    for item in census_threads_raw:
        if not isinstance(item, dict):
            raise ValueError("Discord closure census thread must be an object")
        thread_id = item.get("id")
        parent_id = item.get("parent_id")
        if not _valid_snowflake(thread_id) or not _valid_snowflake(parent_id):
            raise ValueError("Discord closure census thread identity/parent is invalid")
        if thread_id in census_threads:
            raise ValueError("Discord closure census thread IDs are duplicated")
        census_threads[thread_id] = parent_id
        if int(thread_id) >= int(common_high_exclusive):
            census_threads_at_or_after_high.add(thread_id)

    errors: list[str] = []
    if require_verified_census:
        captured_merge_sha = census.get("merge_audit_file_sha256")
        if (
            not _valid_sha(captured_merge_sha)
            or head_catchup.get("merge_audit_file_sha256") != captured_merge_sha
            or actual_merge_audit_file_sha256 != captured_merge_sha
        ):
            errors.append("closure evidence does not bind the merge audit file")
    census_evidence_errors: list[str] = []
    census_private_blocked: set[str] = set()
    if require_verified_census or verified_census_evidence is not None:
        expected_family_parent_ids: set[str] | None = None
        declared_merge_family_parents = merge_audit.get(
            "thread_parent_static_target_ids"
        )
        if declared_merge_family_parents is None:
            census_evidence_errors.append(
                "merge thread-parent static targets are missing"
            )
        else:
            expected_family_parent_ids = _snowflake_set(
                declared_merge_family_parents,
                "merge thread-parent static targets",
            )
        _, census_private_blocked = _validate_census_evidence(
            census,
            verified_census_evidence,
            guild_id=guild_id,
            t_close=close_time,
            t_close_source_sha256=census_t_close_source,
            caught_through=census_caught_through,
            high_exclusive=common_high_exclusive,
            expected_family_parent_ids=expected_family_parent_ids,
            errors=census_evidence_errors,
        )
        errors.extend(census_evidence_errors)
    for thread_id, parent_id in census_threads.items():
        if parent_id not in static_ids:
            errors.append(f"census thread {thread_id} parent is outside static scope")
        if parent_id in merged_threads:
            errors.append(f"census thread {thread_id} parent is itself a thread")
    if census_threads_at_or_after_high:
        errors.append("census contains threads at/after the common high boundary")
    for thread_id in set(census_threads) & set(merged_threads):
        if census_threads[thread_id] != merged_threads[thread_id]:
            errors.append(f"census thread {thread_id} parent differs from merge audit")
    missing_from_merge = set(census_threads) - set(merged_threads)
    missing_from_census = set(merged_threads) - set(census_threads)

    message_bearing_static_ids = _snowflake_set(
        merge_audit.get("message_bearing_static_target_ids"),
        "merge message-bearing static targets",
    )
    if not message_bearing_static_ids <= static_ids:
        errors.append("merge message-bearing static targets exceed static scope")
    base_required_ids = message_bearing_static_ids | set(merged_threads)
    declared_required = merge_audit.get("required_head_catchup_target_ids")
    merge_required_ids = _snowflake_set(
        declared_required,
        "required head catch-up targets",
    )
    if merge_required_ids != base_required_ids:
        errors.append(
            "merge required_head_catchup_target_ids does not match its "
            "message-bearing scope"
        )
    required_ids = base_required_ids | set(census_threads)
    declared_head_required_ids = _snowflake_set(
        head_catchup.get("required_target_ids"),
        "head catch-up required targets",
    )
    if declared_head_required_ids != required_ids:
        errors.append(
            "head-catchup required_target_ids does not match merge plus census scope"
        )
    catchup_raw = head_catchup.get("targets")
    if not isinstance(catchup_raw, list):
        raise ValueError("Discord head-catchup targets must be a list")
    caught: dict[str, datetime] = {}
    caught_items: dict[str, dict[str, Any]] = {}
    invalid_zero_delta: set[str] = set()
    unverified_evidence: set[str] = set()
    new_message_targets: set[str] = set()
    new_thread_targets: set[str] = set()
    all_new_message_ids: list[str] = []
    all_new_thread_ids: list[str] = []
    reported_thread_parents: dict[str, str] = {}
    evidence_hashes: dict[str, str] = {}
    evidence_map = verified_head_evidence or {}
    for item in catchup_raw:
        if not isinstance(item, dict) or not _valid_snowflake(item.get("id")):
            raise ValueError("Discord head-catchup target identity is invalid")
        target_id = item["id"]
        if target_id in caught:
            raise ValueError("Discord head-catchup target IDs are duplicated")
        caught[target_id] = _parse_timestamp(
            item.get("caught_through"),
            f"head-catchup target {target_id} caught_through",
        )
        caught_items[target_id] = item
        delta_errors: list[str] = []
        delta_valid, message_ids, thread_ids = _validate_head_delta_fields(
            item,
            f"head-catchup target {target_id}",
            delta_errors,
        )
        if not delta_valid:
            invalid_zero_delta.add(target_id)
            errors.extend(delta_errors)
        else:
            if message_ids:
                new_message_targets.add(target_id)
                all_new_message_ids.extend(message_ids)
            if thread_ids:
                new_thread_targets.add(target_id)
                all_new_thread_ids.extend(thread_ids)
                for thread_id in thread_ids:
                    reported_thread_parents.setdefault(thread_id, target_id)
        evidence_errors: list[str] = []
        verified_hash = _validate_head_evidence(
            item,
            evidence_map.get(target_id),
            guild_id=guild_id,
            t_close=close_time,
            t_close_source_sha256=census_t_close_source,
            caught_through=caught[target_id],
            errors=evidence_errors,
        )
        if verified_hash is None:
            unverified_evidence.add(target_id)
            errors.extend(evidence_errors)
        else:
            evidence_hashes[target_id] = verified_hash
    unexpected_verified = {
        key for key in evidence_map if not isinstance(key, str) or key not in caught_items
    }
    if unexpected_verified:
        errors.append("verified head-catchup evidence contains unexpected targets")
    if len(all_new_message_ids) != len(set(all_new_message_ids)):
        errors.append("head-catchup new message IDs overlap across targets")
    if len(all_new_thread_ids) != len(set(all_new_thread_ids)):
        errors.append("head-catchup new thread IDs overlap across targets")
    missing_catchup = required_ids - set(caught)
    unexpected_catchup = set(caught) - required_ids
    behind = {
        target_id
        for target_id in required_ids & set(caught)
        if caught[target_id] < close_time
    }
    common_boundary_mismatch = {
        target_id
        for target_id in required_ids & set(caught)
        if caught[target_id] != census_caught_through
    }
    lower_bound = _discord_snowflake_lower_bound(close_time)
    late_census_threads = {
        thread_id
        for thread_id in missing_from_merge
        if int(lower_bound) < int(thread_id) < int(common_high_exclusive)
    }
    historical_threads_missing_from_merge = {
        thread_id
        for thread_id in missing_from_merge
        if int(thread_id) <= int(lower_bound)
    }
    reported_threads_missing_from_census = set(all_new_thread_ids) - set(
        census_threads
    )
    reported_threads_with_wrong_parent = {
        thread_id
        for thread_id, parent_id in reported_thread_parents.items()
        if census_threads.get(thread_id) not in {None, parent_id}
    }
    if reported_threads_with_wrong_parent:
        errors.append("head-catchup reported thread parent differs from census")

    static_scope = merge_audit.get("static_scope")
    if not isinstance(static_scope, dict):
        raise ValueError("Discord merge static_scope is invalid")
    merge_validation_errors = merge_audit.get("validation_errors")
    if not isinstance(merge_validation_errors, list):
        raise ValueError("Discord merge validation_errors is invalid")
    non_private_incomplete = merge_audit.get("non_private_incomplete_streams", [])
    media_incomplete = merge_audit.get("media_incomplete_shards", [])
    message_reference_incomplete = merge_audit.get(
        "message_reference_incomplete_shards",
        [],
    )
    private_incomplete = merge_audit.get("private_archived_incomplete_streams", [])
    private_blocked = merge_audit.get("private_archived_blocked_streams", [])
    for value, label in (
        (non_private_incomplete, "non_private_incomplete_streams"),
        (media_incomplete, "media_incomplete_shards"),
        (
            message_reference_incomplete,
            "message_reference_incomplete_shards",
        ),
        (private_incomplete, "private_archived_incomplete_streams"),
        (private_blocked, "private_archived_blocked_streams"),
    ):
        if not isinstance(value, list):
            raise ValueError(f"Discord merge {label} is invalid")

    unresolved_target_ids = (
        missing_catchup
        | unexpected_catchup
        | common_boundary_mismatch
        | invalid_zero_delta
        | unverified_evidence
    )
    unresolved_empty = not (
        unresolved_target_ids
        or historical_threads_missing_from_merge
        or reported_threads_missing_from_census
    )
    authorized_complete = bool(
        not errors
        and not merge_validation_errors
        and merge_audit.get("status") in {"complete", "partial"}
        and static_scope.get("exact_union") is True
        and static_scope.get("pairwise_disjoint") is True
        and not non_private_incomplete
        and not media_incomplete
        and not message_reference_incomplete
        and unresolved_empty
    )
    full_private_complete = bool(
        authorized_complete
        and not private_incomplete
        and not private_blocked
        and not census_private_blocked
    )
    if full_private_complete:
        closure_status = "authorized-and-full-private-scope-complete"
    elif authorized_complete:
        closure_status = "authorized-scope-complete-private-scope-incomplete"
    else:
        closure_status = "incomplete"
    return {
        "schema_version": 1,
        "audit_kind": CLOSURE_AUDIT_KIND,
        "status": closure_status,
        "guild_id": guild_id,
        "t_close": t_close,
        "t_close_source_sha256": census_t_close_source,
        "caught_through": head_catchup.get("caught_through"),
        "high_exclusive": common_high_exclusive,
        "closure_method": "t_close-census-plus-common-H-bounded-before-union",
        "shards_share_single_point_in_time": False,
        "scope_definitions": {
            "authorized": "targets and threads visible to the authorized bot principal",
            "full_private": "authorized scope plus complete private-archived enumeration",
        },
        "input_canonical_sha256": {
            "merge_audit": canonical_json_sha256(merge_audit),
            "census": canonical_json_sha256(census),
            "head_catchup": canonical_json_sha256(head_catchup),
        },
        "authorized_scope_point_in_time_complete": authorized_complete,
        "full_private_scope_point_in_time_complete": full_private_complete,
        "census_evidence_verified": bool(
            require_verified_census and not census_evidence_errors
        ),
        "census_delta": {
            "missing_from_merge": sorted(missing_from_merge, key=int),
            "missing_from_census": sorted(missing_from_census, key=int),
        },
        "captured_delta": {
            "message_target_ids": sorted(new_message_targets, key=int),
            "message_ids": sorted(set(all_new_message_ids), key=int),
            "thread_parent_target_ids": sorted(new_thread_targets, key=int),
            "reported_thread_ids": sorted(set(all_new_thread_ids), key=int),
            "census_thread_ids": sorted(late_census_threads, key=int),
            "thread_ids": sorted(
                late_census_threads | set(all_new_thread_ids),
                key=int,
            ),
        },
        "unresolved": {
            "target_ids": sorted(unresolved_target_ids, key=int),
            "missing_target_ids": sorted(missing_catchup, key=int),
            "unexpected_target_ids": sorted(unexpected_catchup, key=int),
            "common_boundary_mismatch_target_ids": sorted(
                common_boundary_mismatch,
                key=int,
            ),
            "invalid_delta_target_ids": sorted(invalid_zero_delta, key=int),
            "unverified_evidence_target_ids": sorted(
                unverified_evidence,
                key=int,
            ),
            "historical_thread_ids_missing_from_merge": sorted(
                historical_threads_missing_from_merge,
                key=int,
            ),
            "reported_thread_ids_missing_from_census": sorted(
                reported_threads_missing_from_census,
                key=int,
            ),
            "reported_thread_ids_with_wrong_parent": sorted(
                reported_threads_with_wrong_parent,
                key=int,
            ),
            "census_thread_ids_at_or_after_high": sorted(
                census_threads_at_or_after_high,
                key=int,
            ),
            "non_private_incomplete_streams": deepcopy(non_private_incomplete),
            "media_incomplete_shards": deepcopy(media_incomplete),
            "message_reference_incomplete_shards": deepcopy(
                message_reference_incomplete
            ),
            "census_evidence_errors": sorted(set(census_evidence_errors)),
        },
        "limitations": {
            "current_census_absent_thread_ids": sorted(
                missing_from_census,
                key=int,
            ),
            "current_census_absence_does_not_remove_merged_history": True,
            "possible_current_census_absence_causes": [
                "thread_deleted_after_merge",
                "visibility_or_permission_changed",
            ],
            "historical_missing_thread_requires_full_history_delta_evidence": True,
            "census_private_archived_403_parent_ids": sorted(
                census_private_blocked,
                key=int,
            ),
            "thread_state_observed_after_h_not_as_of_h": True,
            "thread_id_before_h_only_constrains_creation_time": True,
            "archive_delete_permission_race_after_h": True,
            "pins_current_snapshot_non_as_of": True,
            "pins_included_in_h_claim": False,
        },
        "head_catchup_delta": {
            "missing_target_ids": sorted(missing_catchup, key=int),
            "behind_t_close_target_ids": sorted(behind, key=int),
            "common_boundary_mismatch_target_ids": sorted(
                common_boundary_mismatch,
                key=int,
            ),
            "unexpected_target_ids": sorted(unexpected_catchup, key=int),
            "invalid_zero_delta_target_ids": sorted(invalid_zero_delta, key=int),
            "unverified_evidence_target_ids": sorted(unverified_evidence, key=int),
            "new_message_target_ids": sorted(new_message_targets, key=int),
            "new_message_ids": sorted(set(all_new_message_ids), key=int),
            "new_thread_target_ids": sorted(new_thread_targets, key=int),
            "new_thread_ids": sorted(set(all_new_thread_ids), key=int),
        },
        "verified_head_catchup_evidence_sha256": {
            target_id: evidence_hashes[target_id]
            for target_id in sorted(evidence_hashes, key=int)
        },
        "required_head_catchup_target_count": len(required_ids),
        "private_archived_incomplete_count": len(private_incomplete),
        "private_archived_blocked_count": len(private_blocked)
        + len(census_private_blocked),
        "validation_errors": sorted(set(errors)),
    }


def _validate_head_delta_fields(
    value: object,
    label: str,
    errors: list[str],
) -> tuple[bool, list[str], list[str]]:
    if not isinstance(value, dict):
        errors.append(f"{label} zero-delta evidence is not an object")
        return False, [], []
    valid = True
    results: dict[str, list[str]] = {}
    for noun in ("message", "thread"):
        count_key = f"new_{noun}_count"
        ids_key = f"new_{noun}_ids"
        count = value.get(count_key)
        ids = value.get(ids_key)
        if not _valid_nonnegative_int(count):
            errors.append(f"{label} {count_key} must be an explicit nonnegative integer")
            valid = False
        if (
            not isinstance(ids, list)
            or any(not _valid_snowflake(item) for item in ids)
            or len(ids) != len(set(ids))
        ):
            errors.append(f"{label} {ids_key} must contain unique snowflake strings")
            valid = False
            results[noun] = []
            continue
        results[noun] = ids
        if _valid_nonnegative_int(count) and count != len(ids):
            errors.append(f"{label} {count_key} does not match {ids_key}")
            valid = False
    return valid, results.get("message", []), results.get("thread", [])


def _validate_head_evidence(
    descriptor: dict[str, Any],
    verified: object,
    *,
    guild_id: object,
    t_close: datetime,
    t_close_source_sha256: str,
    caught_through: datetime,
    errors: list[str],
) -> str | None:
    target_id = descriptor["id"]
    evidence_path = descriptor.get("evidence_path")
    evidence_sha = descriptor.get("evidence_sha256")
    try:
        _relative_path(evidence_path, f"head catch-up evidence {target_id}")
    except ValueError as exc:
        errors.append(str(exc))
    if not _valid_sha(evidence_sha):
        errors.append(f"head-catchup target {target_id} evidence hash is invalid")
    if not isinstance(verified, dict):
        errors.append(f"head-catchup target {target_id} evidence was not read")
        return None
    if isinstance(verified.get("read_error"), str):
        errors.append(
            f"head-catchup target {target_id} evidence is missing or unsafe: "
            f"{verified['read_error']}"
        )
        return None
    actual_sha = verified.get("file_sha256")
    payload = verified.get("payload")
    if not _valid_sha(actual_sha) or actual_sha != evidence_sha:
        errors.append(f"head-catchup target {target_id} evidence hash mismatch")
    if not isinstance(payload, dict):
        errors.append(f"head-catchup target {target_id} evidence is not a JSON object")
        return None
    if (
        payload.get("schema_version") != 1
        or payload.get("audit_kind") != _HEAD_CATCHUP_EVIDENCE_KIND
        or payload.get("guild_id") != guild_id
        or payload.get("target_id") != target_id
        or payload.get("t_close_source_sha256") != t_close_source_sha256
    ):
        errors.append(f"head-catchup target {target_id} evidence identity is invalid")
    try:
        payload_close = _parse_timestamp(
            payload.get("t_close"),
            f"head-catchup evidence {target_id} t_close",
        )
        payload_caught = _parse_timestamp(
            payload.get("caught_through"),
            f"head-catchup evidence {target_id} caught_through",
        )
    except ValueError as exc:
        errors.append(str(exc))
    else:
        if payload_close != t_close or payload_caught != caught_through:
            errors.append(f"head-catchup target {target_id} evidence time bounds mismatch")
    payload_delta_errors: list[str] = []
    payload_valid, _, _ = _validate_head_delta_fields(
        payload,
        f"head-catchup evidence {target_id}",
        payload_delta_errors,
    )
    errors.extend(payload_delta_errors)
    if payload_valid and any(
        payload.get(key) != descriptor.get(key)
        for key in (
            "new_message_count",
            "new_message_ids",
            "new_thread_count",
            "new_thread_ids",
        )
    ):
        errors.append(f"head-catchup target {target_id} evidence delta mismatch")
    _validate_head_raw_pages(
        payload,
        verified.get("raw_pages"),
        target_id=target_id,
        guild_id=guild_id,
        t_close=t_close,
        t_close_source_sha256=t_close_source_sha256,
        caught_through=caught_through,
        errors=errors,
    )
    return actual_sha if not errors else None


def _validate_head_raw_pages(
    evidence: dict[str, Any],
    verified_pages: object,
    *,
    target_id: str,
    guild_id: object,
    t_close: datetime,
    t_close_source_sha256: str,
    caught_through: datetime,
    errors: list[str],
) -> None:
    """Validate the raw-response commitments behind one catch-up summary.

    Producer contract: ``raw_pages`` is a non-empty ordered list of descriptors.
    Before its first request, the producer commits a millisecond-aligned
    ``caught_through`` exclusive boundary and its derived Snowflake
    ``high_exclusive``. Pages walk newest-to-oldest with ``before``; every
    continuation uses the prior page's minimum message ID. A terminal page must
    cross the T_close lower bound, be empty, or be shorter than its requested
    limit. Only IDs in the open interval ``(T_close, high_exclusive)`` enter the
    summary. Each descriptor commits canonical page, request, and response JSON.
    These hashes prove local acquisition integrity, not a Discord signature or
    platform-authenticated response.
    """

    descriptors = evidence.get("raw_pages")
    if not isinstance(descriptors, list) or not descriptors:
        errors.append(
            f"head-catchup target {target_id} raw_pages must be a non-empty list"
        )
        return
    if not isinstance(verified_pages, dict):
        errors.append(f"head-catchup target {target_id} raw pages were not read")
        return

    declared_paths: set[str] = set()
    message_ids: list[str] = []
    thread_ids: list[str] = []
    all_raw_message_ids: list[str] = []
    all_raw_thread_ids: list[str] = []
    terminal_flags: list[bool] = []
    lower_bound = _discord_snowflake_lower_bound(t_close)
    high_exclusive = _discord_snowflake_lower_bound(caught_through)
    if caught_through.microsecond % 1_000:
        errors.append(
            f"head-catchup target {target_id} caught_through must be a "
            "millisecond-aligned exclusive boundary"
        )
    if int(high_exclusive) <= int(lower_bound):
        errors.append(
            f"head-catchup target {target_id} high_exclusive must be after t_close"
        )
    if evidence.get("high_exclusive") != high_exclusive:
        errors.append(
            f"head-catchup target {target_id} high_exclusive commitment mismatch"
        )
    expected_before = high_exclusive
    for page_number, descriptor in enumerate(descriptors, start=1):
        label = f"head-catchup target {target_id} raw page {page_number}"
        if not isinstance(descriptor, dict):
            errors.append(f"{label} descriptor is not an object")
            continue
        path = descriptor.get("path")
        try:
            _relative_path(path, label)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if path in declared_paths:
            errors.append(f"{label} path is duplicated")
            continue
        declared_paths.add(path)
        expected_sha = descriptor.get("sha256")
        request_sha = descriptor.get("request_sha256")
        response_sha = descriptor.get("response_sha256")
        if not all(_valid_sha(value) for value in (expected_sha, request_sha, response_sha)):
            errors.append(f"{label} hashes are invalid")
            continue

        verified = verified_pages.get(path)
        if not isinstance(verified, dict):
            errors.append(f"{label} was not read")
            continue
        if isinstance(verified.get("read_error"), str):
            errors.append(f"{label} is missing or unsafe: {verified['read_error']}")
            continue
        actual_sha = verified.get("file_sha256")
        raw_page = verified.get("payload")
        if not _valid_sha(actual_sha) or actual_sha != expected_sha:
            errors.append(f"{label} hash mismatch")
        if not isinstance(raw_page, dict):
            errors.append(f"{label} is not a JSON object")
            continue
        if (
            raw_page.get("schema_version") != 1
            or raw_page.get("audit_kind") != _HEAD_CATCHUP_RAW_PAGE_KIND
            or raw_page.get("guild_id") != guild_id
            or raw_page.get("target_id") != target_id
            or raw_page.get("t_close_source_sha256") != t_close_source_sha256
        ):
            errors.append(f"{label} identity is invalid")
        try:
            page_close = _parse_timestamp(raw_page.get("t_close"), f"{label} t_close")
            page_caught = _parse_timestamp(
                raw_page.get("caught_through"),
                f"{label} caught_through",
            )
        except ValueError as exc:
            errors.append(str(exc))
        else:
            if page_close != t_close or page_caught != caught_through:
                errors.append(f"{label} time bounds mismatch")

        request = raw_page.get("request")
        response = raw_page.get("response")
        request_before: str | None = None
        request_limit: int | None = None
        if not isinstance(request, dict) or canonical_json_sha256(request) != request_sha:
            errors.append(f"{label} request commitment mismatch")
        elif not _valid_head_raw_request(request, target_id, expected_before):
            errors.append(f"{label} request is invalid")
        else:
            request_before = request["params"]["before"]
            request_limit = request["params"]["limit"]
        if not isinstance(response, dict) or canonical_json_sha256(response) != response_sha:
            errors.append(f"{label} response commitment mismatch")
            continue
        page_messages = response.get("messages")
        page_threads = response.get("threads")
        if not isinstance(page_messages, list) or not isinstance(page_threads, list):
            errors.append(f"{label} response collections are invalid")
            continue
        if response.get("status_code") != 200:
            errors.append(f"{label} response status is not successful")
        page_message_ids: list[str] = []
        for message in page_messages:
            message_id = message.get("id") if isinstance(message, dict) else None
            channel_id = (
                message.get("channel_id") if isinstance(message, dict) else None
            )
            if not _valid_snowflake(message_id) or channel_id != target_id:
                errors.append(f"{label} contains an invalid message identity/channel")
                continue
            page_message_ids.append(message_id)
            all_raw_message_ids.append(message_id)
            if request_before is not None and int(message_id) >= int(request_before):
                errors.append(f"{label} contains a message at/after request.before")
            if int(lower_bound) < int(message_id) < int(high_exclusive):
                message_ids.append(message_id)
        if page_message_ids != sorted(page_message_ids, key=int, reverse=True):
            errors.append(f"{label} message IDs are out of order")
        if request_limit is not None and len(page_messages) > request_limit:
            errors.append(f"{label} response exceeds the requested limit")
        page_thread_ids: list[str] = []
        for thread in page_threads:
            thread_id = thread.get("id") if isinstance(thread, dict) else None
            parent_id = thread.get("parent_id") if isinstance(thread, dict) else None
            if not _valid_snowflake(thread_id) or parent_id != target_id:
                errors.append(f"{label} contains an invalid thread identity/parent")
                continue
            page_thread_ids.append(thread_id)
            all_raw_thread_ids.append(thread_id)
            if int(thread_id) >= int(high_exclusive):
                errors.append(f"{label} contains a thread at/after high_exclusive")
            if int(lower_bound) < int(thread_id) < int(high_exclusive):
                thread_ids.append(thread_id)
        terminal = response.get("terminal")
        terminal_flags.append(terminal if isinstance(terminal, bool) else False)
        if not isinstance(terminal, bool):
            errors.append(f"{label} terminal flag is invalid")
        next_cursor = response.get("next_cursor")
        terminal_reason = response.get("terminal_reason")
        crossed_lower = bool(
            page_message_ids
            and int(min(page_message_ids, key=int)) <= int(lower_bound)
        )
        empty_page = not page_messages
        short_page = (
            request_limit is not None and len(page_messages) < request_limit
        )
        derived_terminal_reason = (
            "crossed_lower_bound"
            if crossed_lower
            else "empty_page"
            if empty_page
            else "short_page"
            if short_page
            else None
        )
        if terminal is True:
            if derived_terminal_reason is None:
                errors.append(f"{label} terminates without exhaustion proof")
            if terminal_reason != derived_terminal_reason:
                errors.append(f"{label} terminal_reason does not match page content")
            if next_cursor is not None:
                errors.append(f"{label} terminal raw page has a cursor")
            expected_before = ""
        elif terminal is False:
            if derived_terminal_reason is not None:
                errors.append(f"{label} continues after reaching an exhaustion proof")
            if terminal_reason is not None:
                errors.append(f"{label} nonterminal terminal_reason must be null")
            if (
                not page_message_ids
                or not _valid_snowflake(next_cursor)
                or next_cursor != min(page_message_ids, key=int)
            ):
                errors.append(
                    f"{label} next_cursor is not the reverse page boundary"
                )
            expected_before = next_cursor if _valid_snowflake(next_cursor) else ""
        else:
            expected_before = ""

    unexpected_paths = set(verified_pages) - declared_paths
    if unexpected_paths:
        errors.append(
            f"head-catchup target {target_id} raw pages contain unexpected paths"
        )
    if len(all_raw_message_ids) != len(set(all_raw_message_ids)):
        errors.append(f"head-catchup target {target_id} raw message IDs are duplicated")
    if len(all_raw_thread_ids) != len(set(all_raw_thread_ids)):
        errors.append(f"head-catchup target {target_id} raw thread IDs are duplicated")
    if message_ids != evidence.get("new_message_ids"):
        errors.append(
            f"head-catchup target {target_id} raw message IDs do not match summary"
        )
    if thread_ids != evidence.get("new_thread_ids"):
        errors.append(
            f"head-catchup target {target_id} raw thread IDs do not match summary"
        )
    if len(terminal_flags) != len(descriptors) or any(terminal_flags[:-1]):
        errors.append(f"head-catchup target {target_id} raw pagination terminates early")
    if not terminal_flags or terminal_flags[-1] is not True:
        errors.append(f"head-catchup target {target_id} raw pagination is not terminal")


def _valid_head_raw_request(
    request: dict[str, Any],
    target_id: str,
    expected_before: str,
) -> bool:
    if request.get("method") != "GET":
        return False
    if request.get("path") != f"/channels/{target_id}/messages":
        return False
    params = request.get("params")
    if not isinstance(params, dict):
        return False
    limit = params.get("limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        return False
    before = params.get("before")
    return (
        set(params) == {"before", "limit"}
        and _valid_snowflake(before)
        and before == expected_before
    )


def write_closure_audit(
    *,
    workspace: str | os.PathLike[str],
    merge_audit_path: str | os.PathLike[str],
    census_path: str | os.PathLike[str],
    head_catchup_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    t_close: str,
) -> dict[str, Any]:
    """Safely read closure inputs and atomically write their scoped verdict."""

    root = _workspace_root(workspace)
    merge, merge_file_sha, _ = _read_json_relative(root, merge_audit_path, "merge audit")
    census, census_file_sha, _ = _read_json_relative(root, census_path, "closure census")
    head_catchup, head_file_sha, _ = _read_json_relative(
        root,
        head_catchup_path,
        "head catch-up",
    )
    verified_census_pages: dict[str, dict[str, object]] = {}
    census_raw_descriptors = (
        census.get("raw_pages") if isinstance(census, dict) else None
    )
    if isinstance(census_raw_descriptors, list):
        for descriptor in census_raw_descriptors:
            raw_path = descriptor.get("path") if isinstance(descriptor, dict) else None
            if not isinstance(raw_path, str) or raw_path in verified_census_pages:
                continue
            try:
                raw_payload, raw_sha, _ = _read_json_relative(
                    root,
                    raw_path,
                    "closure census raw page",
                )
            except (OSError, ValueError) as exc:
                verified_census_pages[raw_path] = {"read_error": str(exc)}
            else:
                verified_census_pages[raw_path] = {
                    "payload": raw_payload,
                    "file_sha256": raw_sha,
                }
    verified_evidence: dict[str, dict[str, object]] = {}
    catchup_targets = head_catchup.get("targets") if isinstance(head_catchup, dict) else None
    if isinstance(catchup_targets, list):
        for item in catchup_targets:
            if not isinstance(item, dict) or not _valid_snowflake(item.get("id")):
                continue
            target_id = item["id"]
            if target_id in verified_evidence:
                continue
            evidence_path = item.get("evidence_path")
            try:
                payload, file_sha, _ = _read_json_relative(
                    root,
                    evidence_path,
                    f"head catch-up evidence {target_id}",
                )
            except (OSError, ValueError) as exc:
                verified_evidence[target_id] = {"read_error": str(exc)}
            else:
                verified_raw_pages: dict[str, dict[str, object]] = {}
                raw_descriptors = (
                    payload.get("raw_pages") if isinstance(payload, dict) else None
                )
                if isinstance(raw_descriptors, list):
                    for raw_descriptor in raw_descriptors:
                        raw_path = (
                            raw_descriptor.get("path")
                            if isinstance(raw_descriptor, dict)
                            else None
                        )
                        if not isinstance(raw_path, str) or raw_path in verified_raw_pages:
                            continue
                        try:
                            raw_payload, raw_sha, _ = _read_json_relative(
                                root,
                                raw_path,
                                f"head catch-up raw page {target_id}",
                            )
                        except (OSError, ValueError) as exc:
                            verified_raw_pages[raw_path] = {"read_error": str(exc)}
                        else:
                            verified_raw_pages[raw_path] = {
                                "payload": raw_payload,
                                "file_sha256": raw_sha,
                            }
                verified_evidence[target_id] = {
                    "payload": payload,
                    "file_sha256": file_sha,
                    "raw_pages": verified_raw_pages,
                }
    audit = audit_closure(
        merge,
        census,
        head_catchup,
        t_close=t_close,
        verified_head_evidence=verified_evidence,
        verified_census_evidence=verified_census_pages,
        require_verified_census=True,
        actual_merge_audit_file_sha256=merge_file_sha,
    )
    audit["input_file_sha256"] = {
        "merge_audit": merge_file_sha,
        "census": census_file_sha,
        "head_catchup": head_file_sha,
    }
    output_relative = _relative_path(output_path, "closure audit output")
    destination = root / output_relative
    _write_exclusive_or_same(destination, audit, root)
    return {
        "status": audit["status"],
        "output_path": output_relative.as_posix(),
        "output_sha256": _sha256_file(destination),
        "authorized_scope_point_in_time_complete": audit[
            "authorized_scope_point_in_time_complete"
        ],
        "full_private_scope_point_in_time_complete": audit[
            "full_private_scope_point_in_time_complete"
        ],
    }


def _validate_parent_snapshot(snapshot: object) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ValueError("Discord parent target snapshot must be a JSON object")
    guild_id = snapshot.get("guild_id")
    if not _valid_snowflake(guild_id):
        raise ValueError("Discord parent target snapshot guild_id is invalid")
    targets = snapshot.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("Discord parent target snapshot targets must be a non-empty list")
    seen: set[str] = set()
    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            raise ValueError(f"Discord parent target at index {index} must be an object")
        target_id = target.get("id")
        if not _valid_snowflake(target_id):
            raise ValueError(f"Discord parent target at index {index} has an invalid id")
        if target_id in seen:
            raise ValueError(f"Discord parent target id is duplicated: {target_id}")
        seen.add(target_id)
        for field in ("name", "kind"):
            value = target.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Discord parent target {target_id} field {field} is invalid")
        parent_id = target.get("parent_id")
        if parent_id is not None and not _valid_snowflake(parent_id):
            raise ValueError(f"Discord parent target {target_id} parent_id is invalid")
        source_labels = target.get("source_labels")
        if source_labels is not None and (
            not isinstance(source_labels, list)
            or any(
                not isinstance(label, str) or not label.strip()
                for label in source_labels
            )
        ):
            raise ValueError(
                f"Discord parent target {target_id} source_labels are invalid"
            )
    declared_count = snapshot.get("target_count")
    if (
        isinstance(declared_count, bool)
        or not isinstance(declared_count, int)
        or declared_count != len(targets)
    ):
        raise ValueError("Discord parent target_count does not match targets")
    actual_target_sha = target_set_sha256(seen)
    if snapshot.get("target_set_sha256") != actual_target_sha:
        raise ValueError("Discord parent target_set_sha256 does not match targets")
    return deepcopy(snapshot)


def _is_explicit_thread(target: Mapping[str, object]) -> bool:
    kind = str(target.get("kind", "")).upper()
    channel_type = _target_channel_type(target)
    if channel_type is not None:
        return channel_type in _THREAD_TYPES
    return "THREAD" in kind


def _target_channel_type(target: Mapping[str, object]) -> int | None:
    match = re.search(r"\(([0-9]+)\)\s*$", str(target.get("kind", "")))
    return int(match.group(1)) if match is not None else None


def _required_collection_streams(
    target_id: str,
    target: Mapping[str, object],
) -> tuple[str, ...]:
    if _target_channel_type(target) not in _MESSAGE_BEARING_TYPES:
        return ()
    return (f"messages_{target_id}", f"pins_{target_id}")


def _validate_family_weights(
    families: Mapping[str, list[str]],
    family_weights: Mapping[str, object] | None,
) -> tuple[dict[str, int | float], dict[str, dict[str, object]]]:
    supplied = dict(family_weights or {})
    unknown = set(supplied) - set(families)
    if unknown:
        raise ValueError(
            "Discord family weights contain unknown roots: "
            + ", ".join(sorted(unknown, key=str))
        )
    weights: dict[str, int | float] = {}
    details: dict[str, dict[str, object]] = {}
    for root_id, members in families.items():
        supplied_value = supplied.get(root_id)
        if root_id not in supplied:
            value: object = len(members)
            detail = {
                "weight": value,
                "source": "default-static-family-target-count",
                "metrics": {"static_family_target_count": len(members)},
                "input": None,
            }
        elif isinstance(supplied_value, Mapping):
            value = supplied_value.get("weight")
            source = supplied_value.get("source")
            metrics = supplied_value.get("metrics", {})
            if not isinstance(source, str) or not source.strip():
                raise ValueError(f"Discord family weight source is invalid: {root_id}")
            if not isinstance(metrics, Mapping):
                raise ValueError(f"Discord family weight metrics are invalid: {root_id}")
            normalized_metrics: dict[str, int | float | None] = {}
            for metric_name, metric_value in metrics.items():
                if not isinstance(metric_name, str) or not metric_name.strip():
                    raise ValueError(f"Discord family weight metric name is invalid: {root_id}")
                if metric_value is not None and not _valid_nonnegative_number(metric_value):
                    raise ValueError(
                        f"Discord family weight metric value is invalid: {root_id}"
                    )
                normalized_metrics[metric_name] = metric_value
            try:
                canonical_json_bytes(dict(supplied_value))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Discord family weight input is not canonical JSON: {root_id}"
                ) from exc
            detail = {
                "weight": value,
                "source": source.strip(),
                "metrics": normalized_metrics,
                "input": deepcopy(dict(supplied_value)),
            }
        else:
            value = supplied_value
            detail = {
                "weight": value,
                "source": "scalar-family-weight-input",
                "metrics": {},
                "input": value,
            }
        if not _valid_nonnegative_number(value):
            raise ValueError(f"Discord family weight is invalid: {root_id}")
        assert isinstance(value, (int, float)) and not isinstance(value, bool)
        weights[root_id] = value
        details[root_id] = detail
    return weights, details


def _valid_nonnegative_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value >= 0
    )


def _assert_exact_static_partition(
    target_by_id: Mapping[str, object],
    manifests: Iterable[Mapping[str, Any]],
) -> None:
    sets = [{target["id"] for target in manifest["targets"]} for manifest in manifests]
    if set().union(*sets) != set(target_by_id) or sum(map(len, sets)) != len(target_by_id):
        raise AssertionError("Discord shard planner produced an invalid static partition")


def _target_ids_or_error(
    targets: list[object],
    label: str,
    errors: list[str],
) -> set[str]:
    values: list[str] = []
    for target in targets:
        target_id = target.get("id") if isinstance(target, dict) else None
        if not _valid_snowflake(target_id):
            errors.append(f"{label} contains an invalid target identity")
            continue
        values.append(target_id)
    if len(values) != len(set(values)):
        errors.append(f"{label} contains duplicate target identities")
    return set(values)


def _indexed_objects(value: object, label: str) -> dict[int, dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"Discord {label} must be a list")
    indexed: dict[int, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"Discord {label} entry must be an object")
        index = item.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index <= 0:
            raise ValueError(f"Discord {label} index is invalid")
        if index in indexed:
            raise ValueError(f"Discord {label} indices are duplicated")
        indexed[index] = item
    return indexed


def _is_private_only_stream(stream_name: str) -> bool:
    """Only the unjoined private archive endpoint is outside bot-visible scope."""

    return re.fullmatch(r"threads_[0-9]+_private_archived", stream_name) is not None


def _audit_transitive_run_evidence(
    root: Path,
    run_relative: Path,
    checkpoint: object,
    run_manifest: object,
    request: object,
    request_sha256: object,
) -> dict[str, object]:
    """Verify every checkpoint-pinned page, nested evidence file, and asset."""

    errors: list[str] = []
    raw_page_count = 0
    inventory_evidence_count = 0
    message_evidence_page_count = 0
    expected_message_evidence_pages = 0
    message_totals: Counter[str] = Counter()
    downloadable_media_logical_keys: set[str] = set()
    observed_message_evidence_versions: set[int] = set()
    expected_message_evidence_version: int | None = None
    if isinstance(request, dict) and request.get("version") == 2:
        request_schema = request.get("schema")
        if (
            not isinstance(request_schema, dict)
            or set(request_schema) != {"message_evidence_version"}
            or request_schema.get("message_evidence_version") != 2
        ):
            errors.append("current request message evidence schema is invalid")
        else:
            expected_message_evidence_version = 2
    elif isinstance(request, dict) and request.get("version") == 1:
        request_options = request.get("options")
        legacy_evidence_version = (
            request_options.get("message_evidence_schema_version", 2)
            if isinstance(request_options, dict)
            else None
        )
        if legacy_evidence_version is not None:
            if (
                isinstance(legacy_evidence_version, bool)
                or legacy_evidence_version not in {1, 2}
            ):
                errors.append("legacy request message evidence schema is invalid")
            else:
                expected_message_evidence_version = legacy_evidence_version
    if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("streams"), dict):
        asset_evidence = _audit_asset_evidence(
            root,
            run_relative,
            checkpoint,
            run_manifest,
            request,
            request_sha256,
        )
        recovery_evidence = asset_evidence.pop("media_recovery_audit")
        reference_evidence, reference_errors = _audit_reference_resolution_evidence(
            root,
            run_relative,
            checkpoint,
            run_manifest,
            request,
            request_sha256,
        )
        return {
            "raw_page_count": 0,
            "inventory_evidence_count": 0,
            "message_evidence_page_count": 0,
            "downloadable_media_logical_keys": [],
            "asset_evidence": asset_evidence,
            "media_recovery_audit": recovery_evidence,
            "message_reference_resolution_audit": reference_evidence,
            "validation_errors": sorted(
                {
                    "checkpoint streams are unavailable for transitive audit",
                    *asset_evidence["validation_errors"],
                    *reference_errors,
                }
            ),
        }
    streams = checkpoint["streams"]
    expected_page_files: dict[str, set[str]] = {}
    expected_message_evidence_files: dict[str, set[str]] = {}
    for stream_name, state in streams.items():
        if (
            not isinstance(stream_name, str)
            or re.fullmatch(r"[A-Za-z0-9_.-]+", stream_name) is None
            or not isinstance(state, dict)
        ):
            errors.append("checkpoint contains an unsafe raw-page stream identity")
            continue
        hashes = state.get("page_hashes", [])
        if not isinstance(hashes, list):
            errors.append(f"raw page hash ledger is invalid: {stream_name}")
            continue
        declared_pages = state.get("pages")
        if declared_pages is not None and (
            isinstance(declared_pages, bool)
            or not isinstance(declared_pages, int)
            or declared_pages != len(hashes)
        ):
            errors.append(f"raw page count does not match hash ledger: {stream_name}")
        page_states = state.get("page_states", [])
        if not isinstance(page_states, list):
            errors.append(f"raw page processing ledger is invalid: {stream_name}")
            page_states = []
        elif len(page_states) != len(hashes):
            errors.append(f"raw page processing ledger is invalid: {stream_name}")
        message_bearing = stream_name.startswith(("messages_", "pins_"))
        if message_bearing:
            expected_message_evidence_pages += len(hashes)
        expected_names: set[str] = set()
        for page_number, expected_hash in enumerate(hashes, start=1):
            filename = f"{page_number:06d}.json"
            expected_names.add(filename)
            if not _valid_sha(expected_hash):
                errors.append(f"raw page hash is invalid: {stream_name}/{filename}")
                continue
            relative = run_relative / "pages" / stream_name / filename
            try:
                page_content = _read_regular_file_bytes(root, relative, "raw page")
                actual_hash = hashlib.sha256(page_content).hexdigest()
            except (OSError, ValueError) as exc:
                errors.append(f"raw page is missing or unsafe: {stream_name}/{filename}: {exc}")
                continue
            if actual_hash != expected_hash:
                errors.append(f"raw page hash mismatch: {stream_name}/{filename}")
            else:
                raw_page_count += 1

            page_state = (
                page_states[page_number - 1]
                if page_number <= len(page_states)
                else None
            )
            descriptor = (
                page_state.get("message_evidence")
                if isinstance(page_state, dict)
                else None
            )
            descriptor_version = (
                descriptor.get("schema_version")
                if isinstance(descriptor, dict)
                else 1
            )
            if (
                message_bearing
                and isinstance(descriptor, dict)
                and not isinstance(descriptor_version, bool)
                and descriptor_version in {1, 2}
            ):
                observed_message_evidence_versions.add(descriptor_version)
            expected_message_evidence = (
                _expected_message_evidence(
                    page_content,
                    stream_name,
                    filename,
                    expected_hash,
                    page_number,
                    descriptor_version,
                    errors,
                )
                if message_bearing
                else None
            )
            raw_message_count = (
                expected_message_evidence[0]
                if expected_message_evidence is not None
                else None
            )
            expected_evidence_content = (
                expected_message_evidence[1]
                if expected_message_evidence is not None
                else None
            )
            expected_fetched_at = (
                expected_message_evidence[2]
                if expected_message_evidence is not None
                else None
            )
            must_have_evidence = bool(
                message_bearing
                and state.get("status") == "complete"
            )
            if descriptor is None:
                if must_have_evidence:
                    errors.append(
                        f"message evidence descriptor is missing: "
                        f"{stream_name}/{page_number:06d}"
                    )
                continue
            totals_and_keys = _audit_message_evidence_descriptor(
                root,
                run_relative,
                stream_name,
                page_number,
                expected_hash,
                descriptor,
                raw_message_count,
                expected_evidence_content,
                expected_fetched_at,
                errors,
            )
            if totals_and_keys is None:
                continue
            totals, page_media_logical_keys = totals_and_keys
            expected_message_evidence_files.setdefault(stream_name, set()).add(
                f"{page_number:06d}.jsonl"
            )
            message_evidence_page_count += 1
            message_totals.update(totals)
            downloadable_media_logical_keys.update(page_media_logical_keys)
        if expected_names:
            expected_page_files[stream_name] = expected_names

        evidence_path = state.get("evidence_path")
        evidence_hash = state.get("evidence_sha256")
        if evidence_path is not None or evidence_hash is not None:
            if not isinstance(evidence_path, str) or not _valid_sha(evidence_hash):
                errors.append(f"single-response evidence ledger is invalid: {stream_name}")
                continue
            try:
                evidence_relative = _relative_path(
                    evidence_path,
                    f"single-response evidence {stream_name}",
                )
                if not evidence_relative.parts or evidence_relative.parts[0] != "inventory":
                    raise ValueError("Discord inventory evidence must stay under inventory/")
                evidence_content = _read_regular_file_bytes(
                    root,
                    run_relative / evidence_relative,
                    "single-response evidence",
                )
                actual_hash = hashlib.sha256(evidence_content).hexdigest()
            except (OSError, ValueError) as exc:
                errors.append(f"single-response evidence is missing or unsafe: {stream_name}: {exc}")
                continue
            if actual_hash != evidence_hash:
                errors.append(f"single-response evidence hash mismatch: {stream_name}")
            else:
                inventory_evidence_count += 1

    if len(observed_message_evidence_versions) > 1:
        errors.append("message evidence schemas are mixed within one run")
    if (
        expected_message_evidence_version is not None
        and observed_message_evidence_versions
        and observed_message_evidence_versions
        != {expected_message_evidence_version}
    ):
        errors.append("message evidence schema does not match request")

    pages_relative = run_relative / "pages"
    try:
        pages_root = _safe_directory(root, pages_relative, create=False)
    except ValueError as exc:
        if expected_page_files:
            errors.append(f"raw page root is missing or unsafe: {exc}")
        pages_root = None
    if pages_root is not None:
        actual_stream_dirs: set[str] = set()
        for stream_dir in pages_root.iterdir():
            try:
                mode = stream_dir.lstat().st_mode
            except OSError as exc:
                errors.append(f"raw page directory cannot be inspected: {exc}")
                continue
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                errors.append(f"raw page root contains an unsafe entry: {stream_dir.name}")
                continue
            actual_stream_dirs.add(stream_dir.name)
            actual_files: set[str] = set()
            for page in stream_dir.iterdir():
                try:
                    page_mode = page.lstat().st_mode
                except OSError as exc:
                    errors.append(f"raw page cannot be inspected: {stream_dir.name}: {exc}")
                    continue
                if stat.S_ISLNK(page_mode) or not stat.S_ISREG(page_mode):
                    errors.append(f"raw page directory contains an unsafe entry: {stream_dir.name}")
                    continue
                actual_files.add(page.name)
            if actual_files != expected_page_files.get(stream_dir.name, set()):
                errors.append(f"raw page files do not exactly match checkpoint: {stream_dir.name}")
        unexpected_dirs = actual_stream_dirs - set(expected_page_files)
        if unexpected_dirs:
            errors.append(
                "raw page stream directories are not checkpoint-pinned: "
                + ",".join(sorted(unexpected_dirs))
            )

    _audit_message_evidence_tree(
        root,
        run_relative,
        expected_message_evidence_files,
        errors,
    )
    _validate_message_evidence_manifest(
        run_manifest,
        expected_message_evidence_pages,
        message_evidence_page_count,
        message_totals,
        errors,
    )
    asset_evidence = _audit_asset_evidence(
        root,
        run_relative,
        checkpoint,
        run_manifest,
        request,
        request_sha256,
    )
    recovery_evidence = asset_evidence.pop("media_recovery_audit")
    errors.extend(asset_evidence["validation_errors"])
    reference_evidence, reference_errors = _audit_reference_resolution_evidence(
        root,
        run_relative,
        checkpoint,
        run_manifest,
        request,
        request_sha256,
    )
    errors.extend(reference_errors)
    return {
        "raw_page_count": raw_page_count,
        "inventory_evidence_count": inventory_evidence_count,
        "message_evidence_page_count": message_evidence_page_count,
        "downloadable_media_logical_keys": sorted(downloadable_media_logical_keys),
        "asset_evidence": asset_evidence,
        "media_recovery_audit": recovery_evidence,
        "message_reference_resolution_audit": reference_evidence,
        "validation_errors": sorted(set(errors)),
    }


def _audit_reference_resolution_evidence(
    root: Path,
    run_relative: Path,
    checkpoint: object,
    run_manifest: object,
    request: object,
    request_sha256: object,
) -> tuple[dict[str, object], list[str]]:
    descriptor = (
        run_manifest.get("message_reference_resolution_audit")
        if isinstance(run_manifest, dict)
        else None
    )
    run_id = request.get("run_id") if isinstance(request, dict) else None
    if (
        not isinstance(checkpoint, dict)
        or not isinstance(run_manifest, dict)
        or not isinstance(request, dict)
        or not isinstance(run_id, str)
        or not isinstance(request_sha256, str)
    ):
        return (
            {"verified": False, "sha256": None, "counts": {}},
            ["message reference audit inputs are invalid"],
        )
    try:
        verified = verify_published_message_reference_resolution_audit(
            run_root=root / run_relative,
            checkpoint=checkpoint,
            run_id=run_id,
            request_sha256=request_sha256,
            descriptor=descriptor,
        )
    except (OSError, TypeError, ValueError) as exc:
        descriptor_sha = (
            descriptor.get("sha256") if isinstance(descriptor, dict) else None
        )
        descriptor_counts = (
            descriptor.get("counts") if isinstance(descriptor, dict) else None
        )
        return (
            {
                "verified": False,
                "sha256": descriptor_sha if _valid_sha(descriptor_sha) else None,
                "counts": deepcopy(descriptor_counts)
                if isinstance(descriptor_counts, dict)
                else {},
            },
            [f"message reference audit verification failed: {exc}"],
        )
    return verified, []


def _expected_message_evidence(
    content: bytes,
    stream_name: str,
    filename: str,
    raw_page_sha256: str,
    page_number: int,
    schema_version: object,
    errors: list[str],
) -> tuple[int, bytes, str | None] | None:
    label = f"{stream_name}/{filename}"
    try:
        document = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        errors.append(f"raw message page JSON is invalid: {label}")
        return None
    if not isinstance(document, dict):
        errors.append(f"raw message page envelope is invalid: {label}")
        return None
    payload = document.get("payload")
    if stream_name.startswith("messages_"):
        messages = payload
    elif stream_name.startswith("pins_"):
        messages = payload.get("items") if isinstance(payload, dict) else None
    else:
        return None
    if not isinstance(messages, list):
        errors.append(f"raw message page payload is invalid: {label}")
        return None
    pagination = document.get("pagination")
    if (
        not isinstance(pagination, dict)
        or pagination.get("item_count") != len(messages)
    ):
        errors.append(f"raw message page item count is invalid: {label}")
    if schema_version not in {1, 2}:
        errors.append(f"message evidence schema is unsupported: {label}")
        return None
    fetched_at: str | None = None
    if schema_version == 2:
        acquisition = document.get("acquisition")
        if (
            not isinstance(acquisition, dict)
            or set(acquisition) != {"fetched_at", "source"}
            or acquisition.get("source")
            != "collector_local_clock_after_response"
            or not isinstance(acquisition.get("fetched_at"), str)
        ):
            errors.append(f"raw message page acquisition is invalid: {label}")
            return None
        fetched_at = acquisition["fetched_at"]
        try:
            _parse_timestamp(fetched_at, "raw message page fetched_at")
        except ValueError:
            errors.append(f"raw message page acquisition is invalid: {label}")
            return None
    evidence_messages: list[
        tuple[dict[str, Any], str, dict[str, Any] | None]
    ] = []
    target_id = stream_name.split("_", 1)[1]
    if stream_name.startswith("messages_"):
        for index, message in enumerate(messages):
            item_error = _raw_message_item_error(message, target_id)
            if item_error is not None:
                errors.append(
                    f"invalid raw message item: {label} item {index + 1}: "
                    f"{item_error}"
                )
                continue
            assert isinstance(message, dict)
            evidence_messages.append((message, f"/payload/{index}", None))
    else:
        for index, item in enumerate(messages):
            message = item.get("message") if isinstance(item, dict) else None
            pinned_at = item.get("pinned_at") if isinstance(item, dict) else None
            item_error = None
            if not isinstance(item, dict):
                item_error = "pin item is not an object"
            elif not isinstance(pinned_at, str) or not pinned_at:
                item_error = "pinned_at is missing or invalid"
            else:
                try:
                    _parse_timestamp(pinned_at, "pin pinned_at")
                except ValueError:
                    item_error = "pinned_at is not a timezone-aware timestamp"
            if item_error is None:
                item_error = _raw_message_item_error(message, target_id)
            if item_error is not None:
                errors.append(
                    f"invalid raw pin item: {label} item {index + 1}: {item_error}"
                )
                continue
            assert isinstance(message, dict)
            assert isinstance(pinned_at, str)
            pinned_at_utc = _parse_timestamp(
                pinned_at,
                "pin pinned_at",
            ).astimezone(timezone.utc).isoformat()
            evidence_messages.append(
                (
                    message,
                    f"/payload/items/{index}/message",
                    {
                        "event_key": (
                            f"pin_event:{target_id}:{message['id']}:{pinned_at_utc}"
                        ),
                        "channel_id": target_id,
                        "message_id": message["id"],
                        "pinned_at": pinned_at,
                        "pinned_at_utc": pinned_at_utc,
                        "json_pointer": f"/payload/items/{index}",
                    },
                )
            )
    rows: list[dict[str, Any]] = []
    evidence_path = f"pages/{stream_name}/{filename}"
    for message, pointer, pin_event in evidence_messages:
        try:
            evidence = extract_message_evidence(
                message,
                stream=stream_name,
                evidence_path=evidence_path,
                evidence_sha256=raw_page_sha256,
                json_pointer=pointer,
            )
        except (TypeError, ValueError) as exc:
            errors.append(f"raw message deterministic extraction failed: {label}: {exc}")
            return None
        evidence_mapping = asdict(evidence)
        if schema_version == 1:
            row = {"schema_version": 1, **evidence_mapping}
        else:
            row = {
                "schema_version": 2,
                "stream": stream_name,
                "channel_id": target_id,
                "page_number": page_number,
                "message_json_pointer": pointer,
                **evidence_mapping,
            }
            if pin_event is not None:
                row["pin_event"] = pin_event
        rows.append(row)
    return (
        len(rows),
        b"".join(canonical_json_bytes(row) for row in rows),
        fetched_at,
    )


def _raw_message_item_error(message: object, target_id: str) -> str | None:
    if not isinstance(message, dict):
        return "message is not an object"
    if not _valid_snowflake(message.get("id")):
        return "message id is invalid"
    channel_id = message.get("channel_id")
    if not _valid_snowflake(channel_id):
        return "message channel_id is invalid"
    if channel_id != target_id:
        return "message channel_id does not match the stream target"
    return None


def _audit_message_evidence_descriptor(
    root: Path,
    run_relative: Path,
    stream_name: str,
    page_number: int,
    raw_page_sha256: object,
    descriptor: object,
    expected_root_messages: int | None,
    expected_content: bytes | None,
    expected_fetched_at: str | None,
    errors: list[str],
) -> tuple[Counter[str], set[str]] | None:
    label = f"{stream_name}/{page_number:06d}"
    if not isinstance(descriptor, dict):
        errors.append(f"message evidence descriptor is invalid: {label}")
        return None
    evidence_relative = Path("message-evidence") / stream_name / f"{page_number:06d}.jsonl"
    raw_relative = Path("pages") / stream_name / f"{page_number:06d}.json"
    schema_version = descriptor.get("schema_version")
    descriptor_fields = (
        _MESSAGE_EVIDENCE_DESCRIPTOR_V2_FIELDS
        if schema_version == 2
        else _MESSAGE_EVIDENCE_DESCRIPTOR_V1_FIELDS
        if schema_version == 1
        else frozenset()
    )
    channel_id = stream_name.split("_", 1)[1]
    if (
        not descriptor_fields
        or set(descriptor) != descriptor_fields
        or descriptor.get("path") != evidence_relative.as_posix()
        or not _valid_sha(descriptor.get("sha256"))
        or descriptor.get("raw_page_path") != raw_relative.as_posix()
        or descriptor.get("raw_page_sha256") != raw_page_sha256
        or (
            schema_version == 2
            and (
                descriptor.get("stream") != stream_name
                or descriptor.get("channel_id") != channel_id
                or descriptor.get("page_number") != page_number
                or descriptor.get("fetched_at") != expected_fetched_at
            )
        )
    ):
        errors.append(f"message evidence identity/raw-page linkage is invalid: {label}")
        return None
    try:
        content = _read_regular_file_bytes(
            root,
            run_relative / evidence_relative,
            "message evidence",
        )
    except (OSError, ValueError) as exc:
        errors.append(f"message evidence is missing or unsafe: {label}: {exc}")
        return None
    if hashlib.sha256(content).hexdigest() != descriptor["sha256"]:
        errors.append(f"message evidence hash mismatch: {label}")
        return None
    if expected_content is not None and content != expected_content:
        errors.append(
            f"message evidence does not match deterministic extraction: {label}"
        )

    totals: Counter[str] = Counter()
    downloadable_media_logical_keys: set[str] = set()
    for line_number, line in enumerate(content.splitlines(), start=1):
        try:
            row = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            errors.append(f"message evidence JSONL is invalid: {label}:{line_number}")
            return None
        expected_row_fields = (
            _MESSAGE_EVIDENCE_ROW_V1_FIELDS
            if schema_version == 1
            else _MESSAGE_EVIDENCE_ROW_V2_FIELDS
            | ({"pin_event"} if stream_name.startswith("pins_") else set())
        )
        if (
            not isinstance(row, dict)
            or set(row) != expected_row_fields
            or row.get("schema_version") != schema_version
            or row.get("status") not in {"complete", "partial"}
            or (
                schema_version == 2
                and (
                    row.get("stream") != stream_name
                    or row.get("channel_id") != channel_id
                    or row.get("page_number") != page_number
                    or not isinstance(row.get("message_json_pointer"), str)
                )
            )
        ):
            errors.append(f"message evidence row schema is invalid: {label}:{line_number}")
            return None
        totals["root_messages"] += 1
        totals["partial_messages"] += row.get("status") == "partial"
        for field, key in (
            ("nodes", "nodes"),
            ("media_occurrences", "media"),
            ("references", "references"),
            ("diagnostics", "diagnostics"),
        ):
            value = row.get(key)
            if not isinstance(value, list):
                errors.append(
                    f"message evidence row {key} is invalid: {label}:{line_number}"
                )
                return None
            totals[field] += len(value)
            if key == "diagnostics":
                for diagnostic in value:
                    severity = (
                        diagnostic.get("severity")
                        if isinstance(diagnostic, dict)
                        else None
                    )
                    code = (
                        diagnostic.get("code")
                        if isinstance(diagnostic, dict)
                        else None
                    )
                    if severity not in {"error", "warning", "info"} or not isinstance(
                        code, str
                    ) or not code:
                        errors.append(
                            f"message evidence diagnostic is invalid: "
                            f"{label}:{line_number}"
                        )
                        continue
                    totals[f"diagnostics_{severity}"] += 1
                    totals[f"diagnostic_code:{severity}:{code}"] += 1
            if key in {"media", "references"}:
                for occurrence in value:
                    source = (
                        occurrence.get("source")
                        if isinstance(occurrence, dict)
                        else None
                    )
                    if (
                        not isinstance(source, dict)
                        or source.get("stream") != stream_name
                        or source.get("evidence_path")
                        != (Path("pages") / stream_name / f"{page_number:06d}.json").as_posix()
                        or source.get("evidence_sha256") != raw_page_sha256
                    ):
                        errors.append(
                            f"message evidence occurrence raw-page linkage is invalid: "
                            f"{label}:{line_number}"
                        )
                    if key == "media":
                        logical_key = (
                            occurrence.get("logical_key")
                            if isinstance(occurrence, dict)
                            else None
                        )
                        downloadable = (
                            occurrence.get("downloadable")
                            if isinstance(occurrence, dict)
                            else None
                        )
                        if not isinstance(logical_key, str) or not logical_key:
                            errors.append(
                                f"message evidence media logical key is invalid: "
                                f"{label}:{line_number}"
                            )
                        if not isinstance(downloadable, bool):
                            errors.append(
                                f"message evidence media downloadability is invalid: "
                                f"{label}:{line_number}"
                            )
                        elif downloadable and isinstance(logical_key, str) and logical_key:
                            downloadable_media_logical_keys.add(logical_key)
        if schema_version == 2 and stream_name.startswith("pins_"):
            pin_event = row.get("pin_event")
            pointer = row.get("message_json_pointer")
            if not _valid_pin_event(
                pin_event,
                channel_id=channel_id,
                message_pointer=pointer,
            ):
                errors.append(
                    f"message evidence pin event is invalid: {label}:{line_number}"
                )
            else:
                totals["pin_events"] += 1
    for field in (
        "root_messages",
        "partial_messages",
        "nodes",
        "media_occurrences",
        "references",
        "diagnostics",
    ):
        declared = descriptor.get(field)
        if not _valid_nonnegative_int(declared) or declared != totals[field]:
            errors.append(f"message evidence count mismatch: {label}:{field}")
    if schema_version == 2:
        severity = {
            level: totals[f"diagnostics_{level}"]
            for level in ("error", "warning", "info")
        }
        if descriptor.get("diagnostics_by_severity") != severity:
            errors.append(
                f"message evidence diagnostic severity mismatch: {label}"
            )
        if descriptor.get("pin_events") != totals["pin_events"]:
            errors.append(f"message evidence pin event count mismatch: {label}")
    if (
        expected_root_messages is not None
        and totals["root_messages"] != expected_root_messages
    ):
        errors.append(
            f"raw message count does not match message evidence: {label}: "
            f"raw={expected_root_messages},evidence={totals['root_messages']}"
        )
    return totals, downloadable_media_logical_keys


def _valid_pin_event(
    value: object,
    *,
    channel_id: str,
    message_pointer: object,
) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "event_key",
        "channel_id",
        "message_id",
        "pinned_at",
        "pinned_at_utc",
        "json_pointer",
    }:
        return False
    message_id = value.get("message_id")
    pinned_at = value.get("pinned_at")
    pinned_at_utc = value.get("pinned_at_utc")
    event_pointer = value.get("json_pointer")
    try:
        normalized = _parse_timestamp(
            pinned_at,
            "pin pinned_at",
        ).astimezone(timezone.utc).isoformat()
    except ValueError:
        return False
    return bool(
        value.get("channel_id") == channel_id
        and _valid_snowflake(message_id)
        and normalized == pinned_at_utc
        and isinstance(event_pointer, str)
        and message_pointer == f"{event_pointer}/message"
        and value.get("event_key")
        == f"pin_event:{channel_id}:{message_id}:{pinned_at_utc}"
    )


def _audit_message_evidence_tree(
    root: Path,
    run_relative: Path,
    expected: dict[str, set[str]],
    errors: list[str],
) -> None:
    relative = run_relative / "message-evidence"
    try:
        evidence_root = _safe_directory(root, relative, create=False)
    except ValueError as exc:
        if expected:
            errors.append(f"message evidence root is missing or unsafe: {exc}")
        return
    actual_streams: set[str] = set()
    for stream_dir in evidence_root.iterdir():
        try:
            mode = stream_dir.lstat().st_mode
        except OSError as exc:
            errors.append(f"message evidence directory cannot be inspected: {exc}")
            continue
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            errors.append(f"message evidence root contains an unsafe entry: {stream_dir.name}")
            continue
        actual_streams.add(stream_dir.name)
        actual_files: set[str] = set()
        for path in stream_dir.iterdir():
            try:
                child_mode = path.lstat().st_mode
            except OSError as exc:
                errors.append(f"message evidence file cannot be inspected: {exc}")
                continue
            if stat.S_ISLNK(child_mode) or not stat.S_ISREG(child_mode):
                errors.append(
                    f"message evidence directory contains an unsafe entry: "
                    f"{stream_dir.name}/{path.name}"
                )
                continue
            actual_files.add(path.name)
        if actual_files != expected.get(stream_dir.name, set()):
            errors.append(
                f"message evidence files do not exactly match checkpoint: {stream_dir.name}"
            )
    unexpected = actual_streams - set(expected)
    if unexpected:
        errors.append(
            "message evidence stream directories are not checkpoint-pinned: "
            + ",".join(sorted(unexpected))
        )


def _validate_message_evidence_manifest(
    run_manifest: object,
    expected_pages: int,
    actual_pages: int,
    totals: Counter[str],
    errors: list[str],
) -> None:
    summary = run_manifest.get("message_evidence") if isinstance(run_manifest, dict) else None
    if not isinstance(summary, dict):
        if expected_pages:
            errors.append("manifest message evidence summary is missing")
        return
    expected_status = (
        "not_applicable"
        if expected_pages == 0
        else "complete_with_warnings"
        if (
            actual_pages == expected_pages
            and totals["partial_messages"] == 0
            and totals["diagnostics_warning"] > 0
        )
        else "complete"
        if actual_pages == expected_pages and totals["partial_messages"] == 0
        else "partial"
    )
    expected_values = {
        "status": expected_status,
        "pages": actual_pages,
        "expected_pages": expected_pages,
        **{
            key: totals[key]
            for key in (
                "root_messages",
                "partial_messages",
                "nodes",
                "media_occurrences",
                "references",
                "diagnostics",
            )
        },
        "diagnostics_by_severity": {
            severity: totals[f"diagnostics_{severity}"]
            for severity in ("error", "warning", "info")
        },
    }
    if any(summary.get(key) != value for key, value in expected_values.items()):
        errors.append("manifest message evidence summary does not match checkpoint evidence")


def _request_max_asset_bytes(request: object) -> int:
    if not isinstance(request, Mapping):
        raise ValueError("Discord request identity is invalid")
    version = request.get("version")
    options = request.get("options")
    if version not in {1, 2} or not isinstance(options, Mapping):
        raise ValueError("Discord request asset options are invalid")
    if "max_asset_bytes" not in options:
        if version == 1:
            return _LEGACY_MAX_ASSET_BYTES
        raise ValueError("Discord current request max_asset_bytes is missing")
    value = options.get("max_asset_bytes")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("Discord request max_asset_bytes is invalid")
    return value


def _audit_asset_evidence(
    root: Path,
    run_relative: Path,
    checkpoint: object,
    run_manifest: object,
    request: object,
    request_sha256: object,
) -> dict[str, object]:
    """Verify the closed SQLite ledger, immutable records, and referenced blobs."""

    errors: list[str] = []
    statuses: Counter[str] = Counter()
    ledger_relative = run_relative / "asset-ledger.sqlite3"
    ledger_sha: str | None = None
    rows: list[tuple[str, str, str, str | None]] = []
    metadata: dict[str, str] = {}
    resolution_context: MediaResolutionContext | None = None
    max_asset_bytes: int | None = None
    recovery_errors: list[str] = []
    if not isinstance(request_sha256, str):
        recovery_errors.append("media recovery audit request hash is invalid")
    else:
        try:
            resolution_context = media_resolution_context(request, request_sha256)
        except ValueError:
            recovery_errors.append("media recovery audit request binding is invalid")
    try:
        max_asset_bytes = _request_max_asset_bytes(request)
    except ValueError:
        errors.append("asset request max_asset_bytes is invalid")

    if not isinstance(checkpoint, dict):
        errors.append("asset checkpoint is not an object")
    else:
        if checkpoint.get("asset_ledger") != {"backend": "sqlite", "version": 1}:
            errors.append("asset checkpoint marker is missing or invalid")
        if checkpoint.get("assets") != {}:
            errors.append("legacy checkpoint asset map must be empty after SQLite migration")

    try:
        ledger_content = _read_regular_file_bytes(root, ledger_relative, "asset ledger")
        ledger_sha = hashlib.sha256(ledger_content).hexdigest()
    except (OSError, ValueError) as exc:
        errors.append(f"asset ledger database is missing or unsafe: {exc}")
        ledger_content = None

    for suffix in ("-wal", "-shm"):
        sidecar = root / Path(str(ledger_relative) + suffix)
        try:
            mode = sidecar.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(f"asset ledger sidecar cannot be inspected: {suffix}: {exc}")
            continue
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            errors.append(f"asset ledger sidecar is unsafe: {suffix}")
        elif sidecar.stat().st_size != 0:
            errors.append(f"asset ledger WAL is not closed: {suffix}")

    if ledger_content is not None:
        try:
            validation_content = bytearray(ledger_content)
            if len(validation_content) < 100 or not validation_content.startswith(
                b"SQLite format 3\x00"
            ):
                raise sqlite3.DatabaseError("invalid SQLite header")
            # A fully checkpointed WAL database is self-contained, but its header
            # still requests a WAL sidecar.  Flip only the in-memory validation
            # copy to rollback-journal mode so SQLite never reopens the pathname.
            if validation_content[18:20] == b"\x02\x02":
                validation_content[18:20] = b"\x01\x01"
            elif validation_content[18:20] != b"\x01\x01":
                raise sqlite3.DatabaseError("invalid SQLite journal header")
            with closing(sqlite3.connect(":memory:")) as connection:
                connection.deserialize(bytes(validation_content))
                quick_check = connection.execute("PRAGMA quick_check").fetchone()
                if quick_check is None or quick_check[0] != "ok":
                    errors.append("asset ledger SQLite quick_check failed")
                if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                    errors.append("asset ledger schema version is invalid")
                expected_schema = {
                    "asset_records": (
                        ("logical_key", "TEXT", 1, 1),
                        ("record_name", "TEXT", 1, 0),
                        ("committed_sha256", "TEXT", 0, 0),
                        ("pending_sha256", "TEXT", 0, 0),
                    ),
                    "asset_metadata": (
                        ("key", "TEXT", 1, 1),
                        ("value", "TEXT", 1, 0),
                    ),
                }
                for table, expected_columns in expected_schema.items():
                    actual_columns = tuple(
                        (str(row[1]), str(row[2]).upper(), int(row[3]), int(row[5]))
                        for row in connection.execute(f"PRAGMA table_info({table})")
                    )
                    if actual_columns != expected_columns:
                        errors.append(f"asset ledger table schema is invalid: {table}")
                try:
                    rows = [
                        (str(row[0]), str(row[1]), str(row[2]), row[3])
                        for row in connection.execute(
                            "SELECT logical_key, record_name, committed_sha256, "
                            "pending_sha256 FROM asset_records ORDER BY logical_key"
                        )
                    ]
                    metadata = {
                        str(row[0]): str(row[1])
                        for row in connection.execute(
                            "SELECT key, value FROM asset_metadata ORDER BY key"
                        )
                    }
                except sqlite3.Error as exc:
                    errors.append(f"asset ledger rows cannot be read: {exc}")
        except sqlite3.Error as exc:
            errors.append(f"asset ledger database cannot be validated in memory: {exc}")

    required_metadata = {
        "records_generation",
        "index_generation",
        "asset_index_sha256",
    }
    if not required_metadata <= set(metadata):
        errors.append("asset ledger metadata is incomplete")
    else:
        try:
            records_generation = int(metadata["records_generation"])
            index_generation = int(metadata["index_generation"])
        except ValueError:
            errors.append("asset ledger generation metadata is invalid")
        else:
            if records_generation < 0 or index_generation != records_generation:
                errors.append("asset ledger is not checkpointed to its current generation")
        if not _valid_sha(metadata["asset_index_sha256"]):
            errors.append("asset index hash in ledger metadata is invalid")

    blob_cache: dict[str, tuple[Path, int]] = {}
    binary_captured_records = 0
    expected_record_names: set[str] = set()
    seen_logical_keys: set[str] = set()
    committed_records: dict[str, dict[str, Any]] = {}
    for logical_key, record_name, committed_sha, pending_sha in rows:
        if not logical_key or logical_key in seen_logical_keys:
            errors.append("asset logical identity is empty or duplicated")
            continue
        seen_logical_keys.add(logical_key)
        expected_name = hashlib.sha256(logical_key.encode("utf-8")).hexdigest() + ".json"
        if record_name != expected_name:
            errors.append(f"asset logical identity filename mismatch: {logical_key}")
            continue
        expected_record_names.add(record_name)
        if not _valid_sha(committed_sha):
            errors.append(f"asset record committed hash is invalid: {logical_key}")
            continue
        if pending_sha is not None:
            errors.append(f"asset record has unclosed pending state: {logical_key}")
        try:
            record_content = _read_regular_file_bytes(
                root,
                run_relative / "asset-records" / record_name,
                "asset record",
            )
        except (OSError, ValueError) as exc:
            errors.append(f"asset record is missing or unsafe: {logical_key}: {exc}")
            continue
        if hashlib.sha256(record_content).hexdigest() != committed_sha:
            errors.append(f"asset record hash mismatch: {logical_key}")
            continue
        try:
            record = json.loads(record_content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            errors.append(f"asset record is not readable JSON: {logical_key}")
            continue
        if isinstance(record, dict):
            committed_records[logical_key] = record
        status_value = _validate_asset_record_tree(
            root,
            run_relative,
            logical_key,
            record,
            blob_cache,
            errors,
            resolution_context,
            max_asset_bytes,
        )
        statuses[status_value] += 1
        if status_value in _BINARY_ASSET_STATUSES and _asset_record_has_valid_binary(
            root,
            run_relative,
            record,
            blob_cache,
        ):
            binary_captured_records += 1

    records_relative = run_relative / "asset-records"
    try:
        records_root = _safe_directory(root, records_relative, create=False)
    except ValueError as exc:
        if rows:
            errors.append(f"asset record directory is missing or unsafe: {exc}")
        records_root = None
    if records_root is not None:
        actual_record_names: set[str] = set()
        for path in records_root.iterdir():
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                errors.append(f"asset record directory cannot be inspected: {exc}")
                continue
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                errors.append(f"asset record directory contains an unsafe entry: {path.name}")
            else:
                actual_record_names.add(path.name)
        if actual_record_names != expected_record_names:
            errors.append("asset record files do not exactly match SQLite ledger")

    index_content: bytes | None = None
    try:
        index_content = _read_regular_file_bytes(
            root,
            run_relative / "asset-index.jsonl",
            "asset index",
        )
        index_sha = hashlib.sha256(index_content).hexdigest()
    except (OSError, ValueError) as exc:
        errors.append(f"asset index is missing or unsafe: {exc}")
        index_sha = None
    if index_sha != metadata.get("asset_index_sha256"):
        errors.append("asset index hash does not match SQLite ledger metadata")
    expected_index_content = b"".join(
        canonical_json_bytes(record) for record in committed_records.values()
    )
    if index_content is not None and index_content != expected_index_content:
        errors.append("asset index contents do not exactly match SQLite records")

    source_evidence_valid = not errors
    if not source_evidence_valid:
        recovery_errors.append("media recovery audit source evidence is invalid")

    expected_audit: dict[str, object] | None = None
    if source_evidence_valid and resolution_context is not None and index_sha is not None:
        try:
            expected_audit = build_media_recovery_audit(
                run_id=request.get("run_id") if isinstance(request, Mapping) else "",
                request_sha256=resolution_context.request_sha256,
                policy_inputs_sha256=resolution_context.policy_inputs_sha256,
                asset_index_sha256=index_sha,
                records=committed_records,
            )
        except (TypeError, ValueError):
            recovery_errors.append(
                "media recovery audit cannot be independently rebuilt"
            )
    elif index_sha is None:
        recovery_errors.append("media recovery audit asset-index binding is unavailable")

    descriptor = (
        run_manifest.get("media_recovery_audit")
        if isinstance(run_manifest, Mapping)
        else None
    )
    expected_counts = expected_audit["counts"] if expected_audit is not None else {}
    if (
        not isinstance(descriptor, Mapping)
        or set(descriptor) != _MEDIA_RECOVERY_AUDIT_DESCRIPTOR_FIELDS
        or descriptor.get("version") != MEDIA_RECOVERY_AUDIT_VERSION
        or descriptor.get("path") != MEDIA_RECOVERY_AUDIT_FILENAME
        or not _valid_sha(descriptor.get("sha256"))
        or descriptor.get("counts") != expected_counts
    ):
        recovery_errors.append("media recovery audit manifest descriptor is invalid")

    audit_sha: str | None = None
    audit_content: bytes | None = None
    try:
        audit_content = _read_regular_file_bytes(
            root,
            run_relative / MEDIA_RECOVERY_AUDIT_FILENAME,
            "media recovery audit",
        )
        audit_sha = hashlib.sha256(audit_content).hexdigest()
    except (OSError, ValueError):
        recovery_errors.append("media recovery audit artifact is missing or unsafe")
    if isinstance(descriptor, Mapping) and audit_sha != descriptor.get("sha256"):
        recovery_errors.append("media recovery audit hash does not match manifest")
    if expected_audit is not None and audit_content is not None:
        expected_audit_content = canonical_media_recovery_audit_bytes(expected_audit)
        if audit_content != expected_audit_content:
            recovery_errors.append(
                "media recovery audit does not match deterministic reconstruction"
            )

    errors.extend(recovery_errors)

    for status_name in (*sorted(_ASSET_STATUSES), "invalid"):
        statuses.setdefault(status_name, 0)
    return {
        "asset_ledger_sha256": ledger_sha,
        "record_count": len(rows),
        "logical_keys": sorted(seen_logical_keys),
        "status_counts": dict(statuses),
        "verified_blob_count": len(blob_cache),
        "binary_captured_record_count": binary_captured_records,
        "asset_index_sha256": index_sha,
        "media_recovery_audit": {
            "verified": not recovery_errors,
            "sha256": audit_sha,
            "counts": expected_counts,
        },
        "validation_errors": sorted(set(errors)),
    }


def _validate_asset_record_tree(
    root: Path,
    run_relative: Path,
    logical_key: str,
    record: object,
    blob_cache: dict[str, tuple[Path, int]],
    errors: list[str],
    resolution_context: MediaResolutionContext | None,
    max_asset_bytes: int | None,
) -> str:
    if not isinstance(record, dict):
        errors.append(f"asset logical identity record is not an object: {logical_key}")
        return "invalid"
    schema_version = record.get("schema_version")
    if record.get("logical_key") != logical_key or schema_version not in {2, 3, 4}:
        errors.append(f"asset logical identity does not match record: {logical_key}")
    kind = record.get("kind")
    field = record.get("field")
    metadata = record.get("declared_metadata")
    if kind not in _ASSET_KINDS:
        errors.append(f"asset logical identity kind is invalid: {logical_key}")
    if not isinstance(field, str) or not field:
        errors.append(f"asset record field is invalid: {logical_key}")
    if not isinstance(metadata, dict):
        errors.append(f"asset record declared metadata is invalid: {logical_key}")
        metadata = {}
    if not isinstance(record.get("identity_metadata"), dict):
        errors.append(f"asset record identity metadata is invalid: {logical_key}")
    if kind == "attachment":
        attachment_id = metadata.get("id")
        if (
            field != "attachment"
            or not isinstance(attachment_id, str)
            or re.fullmatch(
                rf".+:attachment:{re.escape(attachment_id)}",
                logical_key,
            )
            is None
        ):
            errors.append(f"asset logical identity is invalid: {logical_key}")
    elif kind == "embed":
        embed_fields = {"image", "thumbnail", "video", "author_icon", "footer_icon"}
        attachment_id = metadata.get("attachment_id")
        embed_identity = (
            field in embed_fields
            and re.fullmatch(rf".+:embed:[0-9]+:{field}", logical_key) is not None
        )
        attachment_identity = (
            isinstance(attachment_id, str)
            and logical_key.endswith(f":attachment:{attachment_id}")
        )
        if field not in embed_fields or not (embed_identity or attachment_identity):
            errors.append(f"asset logical identity is invalid: {logical_key}")
    elif kind == "component":
        attachment_id = metadata.get("attachment_id")
        direct_identity = ":component:/" in logical_key
        attachment_identity = (
            isinstance(attachment_id, str)
            and logical_key.endswith(f":attachment:{attachment_id}")
        )
        if not (direct_identity or attachment_identity):
            errors.append(f"asset logical identity is invalid: {logical_key}")
    elif kind == "sticker":
        sticker_id = metadata.get("id")
        if not isinstance(sticker_id, str) or logical_key != f"sticker:{sticker_id}":
            errors.append(f"asset logical identity is invalid: {logical_key}")
    elif kind == "emoji":
        emoji_id = metadata.get("id")
        if not isinstance(emoji_id, str) or logical_key != f"emoji:{emoji_id}":
            errors.append(f"asset logical identity is invalid: {logical_key}")
    if not isinstance(record.get("url"), str) or not record["url"]:
        errors.append(f"asset record URL is invalid: {logical_key}")
    candidate_urls = record.get("candidate_urls")
    effective_candidate_urls = (
        [record.get("url")]
        if schema_version == 3 and "candidate_urls" not in record
        else candidate_urls
    )
    if schema_version in {3, 4} and (
        not isinstance(effective_candidate_urls, list)
        or not effective_candidate_urls
        or any(
            not isinstance(value, str) or not value
            for value in effective_candidate_urls
        )
        or len(set(effective_candidate_urls)) != len(effective_candidate_urls)
        or record.get("url") not in effective_candidate_urls
    ):
        errors.append(f"asset record candidate URLs are invalid: {logical_key}")
    if (
        not isinstance(record.get("sources"), list)
        or not record["sources"]
        or any(not isinstance(source, dict) for source in record["sources"])
    ):
        errors.append(f"asset record has no sources: {logical_key}")
    observations = record.get("observations")
    if (
        not isinstance(observations, list)
        or not observations
        or any(
            not isinstance(observation, dict)
            or not isinstance(observation.get("source"), dict)
            or not isinstance(observation.get("metadata"), dict)
            or not isinstance(observation.get("url"), str)
            for observation in observations
        )
    ):
        errors.append(f"asset record has no observations: {logical_key}")
    observed_urls = record.get("observed_urls")
    if (
        not isinstance(observed_urls, list)
        or any(not isinstance(value, str) or not value for value in observed_urls)
        or len(set(observed_urls)) != len(observed_urls)
        or (
            schema_version in {3, 4}
            and isinstance(effective_candidate_urls, list)
            and any(
                value not in observed_urls for value in effective_candidate_urls
            )
        )
    ):
        errors.append(f"asset record observed URLs are invalid: {logical_key}")
    status_value = record.get("status")
    if status_value not in _ASSET_STATUSES:
        errors.append(f"asset record status is invalid: {logical_key}")
        status_name = "invalid"
    else:
        status_name = status_value
    actual_bytes = record.get("actual_bytes")
    if isinstance(actual_bytes, bool) or not isinstance(actual_bytes, int) or actual_bytes < 0:
        errors.append(f"asset record byte count is invalid: {logical_key}")
        actual_bytes = 0
    if (
        max_asset_bytes is not None
        and status_name in _COVERED_ASSET_STATUSES
        and actual_bytes > max_asset_bytes
    ):
        errors.append(
            f"asset record exceeds request max_asset_bytes: {logical_key}"
        )
    youtube_reference = _is_exact_youtube_player_reference(record)
    has_reference_marker = (
        "reference_provenance" in record
        or record.get("terminal_reason") == _YOUTUBE_EMBED_PLAYER_REFERENCE_REASON
    )
    if has_reference_marker and not youtube_reference:
        errors.append(f"asset record reference provenance is invalid: {logical_key}")
    no_blob = record.get("sha256") is None and record.get("blob_path") is None
    if status_name == "reference_only" and no_blob and not youtube_reference:
        errors.append(f"asset record reference provenance is invalid: {logical_key}")
    _validate_blob_evidence(
        root,
        run_relative,
        record.get("sha256"),
        record.get("blob_path"),
        actual_bytes,
        f"asset blob {logical_key}",
        blob_cache,
        errors,
        required=(
            status_name in _BINARY_ASSET_STATUSES
            or (
                status_name == "reference_only"
                and not youtube_reference
            )
        ),
    )
    attempts = record.get("attempt_history", [])
    if not isinstance(attempts, list):
        errors.append(f"asset attempt history is invalid: {logical_key}")
        attempts = []
    for attempt_number, attempt in enumerate(attempts, start=1):
        if not isinstance(attempt, dict):
            errors.append(f"asset attempt is invalid: {logical_key}/{attempt_number}")
            continue
        if attempt.get("status") not in _ASSET_STATUSES | {"interrupted"}:
            errors.append(f"asset attempt status is invalid: {logical_key}/{attempt_number}")
        attempt_bytes = attempt.get("actual_bytes")
        if (
            isinstance(attempt_bytes, bool)
            or not isinstance(attempt_bytes, int)
            or attempt_bytes < 0
        ):
            errors.append(f"asset attempt byte count is invalid: {logical_key}/{attempt_number}")
            attempt_bytes = 0
        if (
            max_asset_bytes is not None
            and attempt.get("status") in _COVERED_ASSET_STATUSES
            and attempt_bytes > max_asset_bytes
        ):
            errors.append(
                "asset attempt exceeds request max_asset_bytes: "
                f"{logical_key}/{attempt_number}"
            )
        _validate_blob_evidence(
            root,
            run_relative,
            attempt.get("sha256"),
            attempt.get("blob_path"),
            attempt_bytes,
            f"asset attempt blob {logical_key}/{attempt_number}",
            blob_cache,
            errors,
            required=attempt.get("status") in _COVERED_ASSET_STATUSES,
        )
    if resolution_context is not None:
        try:
            validate_resolution_attempt_history(
                record,
                context=resolution_context,
            )
        except ValueError:
            errors.append(
                f"asset resolution attempt history is invalid: {logical_key}"
            )
    return status_name


def _asset_record_has_valid_binary(
    root: Path,
    run_relative: Path,
    record: object,
    blob_cache: Mapping[str, tuple[Path, int]],
) -> bool:
    if not isinstance(record, Mapping):
        return False
    actual_bytes = record.get("actual_bytes")
    digest = record.get("sha256")
    blob_value = record.get("blob_path")
    if (
        record.get("status") not in _BINARY_ASSET_STATUSES
        or isinstance(actual_bytes, bool)
        or not isinstance(actual_bytes, int)
        or actual_bytes <= 0
        or not _valid_sha(digest)
        or not isinstance(blob_value, str)
    ):
        return False
    try:
        relative = _relative_path(blob_value, "asset blob")
    except ValueError:
        return False
    return blob_cache.get(digest) == (root / run_relative / relative, actual_bytes)


def _is_exact_youtube_player_reference(record: Mapping[str, object]) -> bool:
    if (
        record.get("status") != "reference_only"
        or record.get("terminal_reason") != _YOUTUBE_EMBED_PLAYER_REFERENCE_REASON
    ):
        return False
    source_url = record.get("url")
    metadata = record.get("declared_metadata")
    attempts = record.get("attempt_history")
    if (
        not isinstance(metadata, Mapping)
        or (
            isinstance(metadata.get("proxy_url"), str)
            and bool(metadata.get("proxy_url"))
        )
        or not isinstance(attempts, list)
        or not attempts
        or record.get("http_content_type") is not None
        or record.get("http_content_length") is not None
        or record.get("actual_bytes") != 0
        or record.get("sha256") is not None
        or record.get("blob_path") is not None
    ):
        return False
    failed_attempt_number = (
        record.get("reference_provenance", {}).get("failed_attempt_number")
        if isinstance(record.get("reference_provenance"), Mapping)
        else len(attempts)
    )
    expected = _youtube_player_reference_provenance(
        record,
        source_url=source_url,
        failed_attempt_number=failed_attempt_number,
    )
    return (
        expected is not None
        and discord_media_reference_candidate_ledger_is_exact(
            record,
            source_url=source_url,
            failed_attempt_number=failed_attempt_number,
        )
        and record.get("reference_provenance") == expected
    )


def _youtube_player_reference_provenance(
    record: Mapping[str, object],
    *,
    source_url: object,
    failed_attempt_number: object,
) -> dict[str, object] | None:
    identity = _youtube_player_url_identity(source_url)
    attempts = record.get("attempt_history")
    if (
        record.get("kind") != "embed"
        or record.get("field") != "video"
        or record.get("declared_content_type") is not None
        or identity is None
        or not isinstance(attempts, list)
        or isinstance(failed_attempt_number, bool)
        or not isinstance(failed_attempt_number, int)
        or failed_attempt_number < 1
        or failed_attempt_number > len(attempts)
        or discord_media_reference_source_observation(record, source_url)
        is None
    ):
        return None
    failed_attempt = attempts[failed_attempt_number - 1]
    if (
        not isinstance(failed_attempt, Mapping)
        or failed_attempt.get("url") != source_url
        or failed_attempt.get("status") != "failed"
        or failed_attempt.get("terminal_reason") != "unsafe_media_url"
        or failed_attempt.get("http_content_type") is not None
        or failed_attempt.get("http_content_length") is not None
        or failed_attempt.get("actual_bytes") != 0
        or failed_attempt.get("sha256") is not None
        or failed_attempt.get("blob_path") is not None
    ):
        return None
    for later_attempt in attempts[failed_attempt_number:]:
        if (
            not isinstance(later_attempt, Mapping)
            or later_attempt.get("url") == source_url
            or later_attempt.get("status") != "failed"
            or not isinstance(later_attempt.get("terminal_reason"), str)
            or not later_attempt.get("terminal_reason")
        ):
            return None
    return {
        "classification": "youtube_embed_player",
        "classification_rule": _YOUTUBE_EMBED_PLAYER_REFERENCE_RULE,
        "source_url": source_url,
        "url_identity": identity,
        "failed_attempt_number": failed_attempt_number,
        "failed_attempt_status": "failed",
        "failed_attempt_terminal_reason": "unsafe_media_url",
        "proxy_candidate_present": False,
        "binary_captured": False,
    }


def _youtube_player_url_identity(value: object) -> dict[str, str] | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    host = parsed.hostname
    if (
        parsed.scheme.lower() != "https"
        or host not in _YOUTUBE_EMBED_PLAYER_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
        or _YOUTUBE_EMBED_PLAYER_PATH.fullmatch(parsed.path) is None
    ):
        return None
    return {"scheme": "https", "host": host, "path": parsed.path}


def _validate_blob_evidence(
    root: Path,
    run_relative: Path,
    digest: object,
    blob_value: object,
    actual_bytes: int,
    label: str,
    blob_cache: dict[str, tuple[Path, int]],
    errors: list[str],
    *,
    required: bool,
) -> None:
    if digest is None and blob_value is None:
        if required:
            errors.append(f"{label} is required but missing")
        return
    if not _valid_sha(digest) or not isinstance(blob_value, str):
        errors.append(f"{label} identity is invalid")
        return
    try:
        relative = _relative_path(blob_value, label)
    except ValueError as exc:
        errors.append(f"{label} path is not contained: {exc}")
        return
    parts = relative.parts
    if (
        len(parts) != 4
        or parts[:2] != ("assets", "sha256")
        or parts[2] != digest[:2]
        or not parts[3].startswith(digest + ".")
    ):
        errors.append(f"{label} path does not match its hash")
        return
    try:
        blob_relative = run_relative / relative
        content = _read_regular_file_bytes(root, blob_relative, label)
        size = len(content)
    except (OSError, ValueError) as exc:
        errors.append(f"{label} is missing or unsafe: {exc}")
        return
    if size != actual_bytes:
        errors.append(f"{label} byte count does not match")
        return
    cached = blob_cache.get(digest)
    identity = (root / blob_relative, size)
    if cached is not None:
        if cached != identity:
            errors.append(f"{label} digest maps to inconsistent blob identities")
        return
    if hashlib.sha256(content).hexdigest() != digest:
        errors.append(f"{label} hash mismatch")
        return
    blob_cache[digest] = identity


def _validate_message_reference_state(
    message_evidence: object,
    reference_audit: object,
) -> tuple[bool, bool, str]:
    if not isinstance(message_evidence, dict) or not isinstance(
        reference_audit, dict
    ):
        return False, False, "message evidence or reference audit is unavailable"
    if reference_audit.get("verified") is not True:
        return False, False, "message reference audit is not verified"
    counts = reference_audit.get("counts")
    if not isinstance(counts, dict):
        return False, False, "message reference audit counts are unavailable"
    required = (
        "raw_errors",
        "occurrences",
        "local_resolved",
        "deleted",
        "unresolved",
        "effective_errors",
        "raw_error_diagnostics",
        "non_reference_error_diagnostics",
        "effective_error_diagnostics",
        "raw_partial_messages",
        "effective_partial_messages",
    )
    if any(not _valid_nonnegative_int(counts.get(key)) for key in required):
        return False, False, "message reference audit counts are invalid"
    if (
        counts["occurrences"] != counts["raw_errors"]
        or counts["local_resolved"] + counts["deleted"] + counts["unresolved"]
        != counts["raw_errors"]
        or counts["effective_errors"] != counts["unresolved"]
        or counts["raw_error_diagnostics"]
        != counts["non_reference_error_diagnostics"] + counts["raw_errors"]
        or counts["effective_error_diagnostics"]
        != counts["non_reference_error_diagnostics"] + counts["effective_errors"]
    ):
        return False, False, "message reference audit count identities differ"
    severity = message_evidence.get("diagnostics_by_severity")
    effective_severity = message_evidence.get(
        "effective_diagnostics_by_severity"
    )
    raw_count_severity = counts.get("raw_diagnostics_by_severity")
    effective_count_severity = counts.get(
        "effective_diagnostics_by_severity"
    )
    raw_codes = counts.get("raw_diagnostic_codes_by_severity")
    effective_codes = counts.get(
        "effective_diagnostic_codes_by_severity"
    )
    summary_raw_codes = message_evidence.get(
        "diagnostic_codes_by_severity"
    )
    summary_effective_codes = message_evidence.get(
        "effective_diagnostic_codes_by_severity"
    )
    if (
        not _diagnostic_code_counts_are_valid(raw_codes, raw_count_severity)
        or not _diagnostic_code_counts_are_valid(
            effective_codes,
            effective_count_severity,
        )
        or severity != raw_count_severity
        or effective_severity != effective_count_severity
        or summary_raw_codes != raw_codes
        or summary_effective_codes != effective_codes
        or severity.get("error") != counts["raw_error_diagnostics"]
        or effective_severity.get("error")
        != counts["effective_error_diagnostics"]
        or severity.get("warning") != effective_severity.get("warning")
        or severity.get("info") != effective_severity.get("info")
        or raw_codes.get("warning") != effective_codes.get("warning")
        or raw_codes.get("info") != effective_codes.get("info")
        or message_evidence.get("partial_messages")
        != counts["raw_partial_messages"]
        or message_evidence.get("effective_partial_messages")
        != counts["effective_partial_messages"]
    ):
        return False, False, "message evidence summary differs from reference audit"
    effective_complete = (
        counts["effective_errors"] == 0
        and counts["effective_error_diagnostics"] == 0
        and counts["effective_partial_messages"] == 0
    )
    expected_status = (
        "partial"
        if not effective_complete
        else "complete_with_warnings"
        if effective_severity.get("warning", 0)
        else "complete"
    )
    if message_evidence.get("effective_status") != expected_status:
        return False, False, "message evidence effective status is inconsistent"
    unsupported_warnings = set(effective_codes["warning"]) - set(
        _ALLOWED_MESSAGE_WARNING_CODES
    )
    if unsupported_warnings:
        return (
            True,
            False,
            "unsupported warning diagnostics remain: "
            + ",".join(sorted(unsupported_warnings)),
        )
    return (
        True,
        effective_complete,
        "complete" if effective_complete else "effective message reference errors remain",
    )


def _diagnostic_code_counts_are_valid(
    value: object,
    severity_counts: object,
) -> bool:
    if (
        not isinstance(value, dict)
        or set(value) != {"error", "warning", "info"}
        or not isinstance(severity_counts, dict)
        or set(severity_counts) != {"error", "warning", "info"}
    ):
        return False
    for severity in ("error", "warning", "info"):
        codes = value.get(severity)
        expected = severity_counts.get(severity)
        if (
            not isinstance(codes, dict)
            or not _valid_nonnegative_int(expected)
            or any(
                not isinstance(code, str)
                or not code
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count <= 0
                for code, count in codes.items()
            )
            or sum(codes.values()) != expected
        ):
            return False
    return True


def _validate_media_state(
    media: object,
    asset_evidence: object,
    media_recovery_audit: object,
    checkpoint: object,
    options: object,
) -> tuple[bool, str]:
    if (
        not isinstance(media, dict)
        or not isinstance(asset_evidence, dict)
        or not isinstance(media_recovery_audit, dict)
        or not isinstance(checkpoint, dict)
    ):
        return False, "media summary or transitive asset evidence is invalid"
    if media_recovery_audit.get("verified") is not True:
        return False, "media recovery audit evidence is not verified"
    recovery_counts = media_recovery_audit.get("counts")
    if not isinstance(recovery_counts, dict):
        return False, "media recovery audit counts are unavailable"
    unresolved_blockers = recovery_counts.get("unresolved_blockers")
    if not _valid_nonnegative_int(unresolved_blockers):
        return False, "media recovery audit blocker count is invalid"
    if checkpoint.get("asset_ledger") != {"backend": "sqlite", "version": 1}:
        return False, "checkpoint does not declare the SQLite asset ledger"
    if checkpoint.get("assets") != {}:
        return False, "checkpoint still contains a legacy asset map"
    count_keys = (
        "records",
        "complete",
        "captured_with_warning",
        "reference_only",
        "failed",
    )
    values = [
        media.get(key, 0) if key in {"captured_with_warning", "reference_only"} else media.get(key)
        for key in count_keys
    ]
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in values
    ):
        return False, "media counts are invalid"
    records, complete, captured_with_warning, reference_only, failed = values
    status_counts = asset_evidence.get("status_counts")
    if not isinstance(status_counts, dict):
        return False, "asset status counts are unavailable"
    if records != asset_evidence.get("record_count"):
        return False, "media records do not match SQLite asset ledger"
    declared_status_counts = {
        "complete": complete,
        "captured_with_warning": captured_with_warning,
        "reference_only": reference_only,
        "failed": failed,
    }
    if any(
        count != status_counts.get(status_name)
        for status_name, count in declared_status_counts.items()
    ):
        return False, "media status counts do not match asset records"
    if unresolved_blockers != failed or unresolved_blockers != status_counts.get("failed"):
        return False, "media recovery blockers do not match failed asset records"
    binary_captured = media.get("binary_captured")
    if binary_captured is not None and (
        not _valid_nonnegative_int(binary_captured)
        or binary_captured != asset_evidence.get("binary_captured_record_count")
    ):
        return False, "media binary_captured count does not match asset records"
    status_value = media.get("status")
    download_assets = options.get("download_assets") if isinstance(options, dict) else None
    if download_assets is False:
        valid = (
            status_value == "not_requested"
            and complete == 0
            and captured_with_warning == 0
            and reference_only == 0
            and failed == 0
            and status_counts.get("not_requested") == records
        )
        return valid, "not_requested" if valid else "disabled media summary is inconsistent"
    if download_assets is not True:
        return False, "request download_assets option is invalid"
    covered = complete + captured_with_warning + reference_only
    pending_or_disabled = sum(
        int(status_counts.get(key, 0))
        for key in ("failed", "in_progress", "not_requested", "invalid")
    )
    if status_value == "complete":
        valid = (
            covered == records
            and captured_with_warning == 0
            and reference_only == 0
            and pending_or_disabled == 0
        )
        return valid, "complete" if valid else "complete media summary has incomplete records"
    if status_value == "complete_with_warnings":
        valid = (
            covered == records
            and captured_with_warning + reference_only > 0
            and pending_or_disabled == 0
        )
        return (
            valid,
            "complete_with_warnings"
            if valid
            else "warning media summary is inconsistent",
        )
    if status_value == "partial":
        valid = covered != records or pending_or_disabled > 0
        return valid, "partial" if valid else "partial media summary has no incomplete records"
    return False, "media status is invalid"


def _parse_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Discord {label} must be an ISO-8601 timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Discord {label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Discord {label} must include a timezone")
    return parsed


def _discord_snowflake_lower_bound(value: datetime) -> str:
    """Return the first Discord snowflake value in ``value``'s millisecond."""

    unix_ms = _datetime_unix_ms(value)
    if unix_ms < _DISCORD_EPOCH_MS:
        raise ValueError("Discord t_close predates the Discord epoch")
    return str((unix_ms - _DISCORD_EPOCH_MS) << 22)


def _datetime_unix_ms(value: datetime) -> int:
    utc = value.astimezone(timezone.utc)
    unix_epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = utc - unix_epoch
    return (
        delta.days * 86_400_000
        + delta.seconds * 1_000
        + delta.microseconds // 1_000
    )


def _snowflake_set(value: object, label: str) -> set[str]:
    if not isinstance(value, list) or any(not _valid_snowflake(item) for item in value):
        raise ValueError(f"Discord {label} must contain snowflake strings")
    if len(value) != len(set(value)):
        raise ValueError(f"Discord {label} must not contain duplicates")
    return set(value)


def _workspace_root(workspace: str | os.PathLike[str]) -> Path:
    root = Path(workspace).absolute().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Discord workspace must be a directory")
    return root


def _relative_path(value: object, label: str) -> Path:
    raw = str(value or "")
    relative = Path(raw)
    if (
        not raw
        or relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"Discord {label} path must be a contained relative path")
    return relative


def _safe_directory(root: Path, relative: Path, *, create: bool) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if not create:
                raise ValueError("Discord directory path must exist") from None
            current.mkdir()
            mode = current.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError("Discord path contains a symbolic link")
        if not stat.S_ISDIR(mode):
            raise ValueError("Discord path component must be a directory")
    return current


def _safe_regular_file(root: Path, relative: Path, label: str) -> Path:
    parent = _safe_directory(root, Path(*relative.parts[:-1]), create=False)
    path = parent / relative.name
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        raise ValueError(f"Discord {label} path must be an existing regular file") from None
    if stat.S_ISLNK(mode):
        raise ValueError("Discord path contains a symbolic link")
    if not stat.S_ISREG(mode):
        raise ValueError(f"Discord {label} path must be a regular file")
    return path


def _read_regular_file_bytes(root: Path, relative: Path, label: str) -> bytes:
    """Read one contained regular file through a no-follow descriptor."""

    path = _safe_regular_file(root, relative, label)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"Discord {label} cannot be opened safely") from exc
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode):
            raise ValueError(f"Discord {label} path must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _read_json_relative(
    root: Path,
    value: str | os.PathLike[str],
    label: str,
) -> tuple[Any, str, Path]:
    relative = _relative_path(value, label)
    path = _safe_regular_file(root, relative, label)
    try:
        content = _read_regular_file_bytes(root, relative, label)
        payload = json.loads(content)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Discord {label} is not readable JSON") from exc
    return payload, hashlib.sha256(content).hexdigest(), path


def _write_exclusive_or_same(path: Path, value: object, root: Path) -> None:
    relative = path.relative_to(root)
    parent = _safe_directory(root, Path(*relative.parts[:-1]), create=True)
    destination = parent / relative.name
    content = canonical_json_bytes(value)
    try:
        mode = destination.lstat().st_mode
    except FileNotFoundError:
        mode = None
    if mode is not None:
        if stat.S_ISLNK(mode):
            raise ValueError("Discord output path contains a symbolic link")
        if not stat.S_ISREG(mode):
            raise ValueError("Discord output path must be a regular file")
        if destination.read_bytes() == content:
            return
        raise ValueError("Discord immutable output already exists with different content")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            try:
                mode = destination.lstat().st_mode
            except FileNotFoundError:
                raise ValueError("Discord output path changed during atomic publication") from None
            if stat.S_ISLNK(mode):
                raise ValueError("Discord output path contains a symbolic link") from None
            if not stat.S_ISREG(mode) or destination.read_bytes() != content:
                raise ValueError(
                    "Discord immutable output already exists with different content"
                ) from None
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_snowflake(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(_SNOWFLAKE.fullmatch(value))
        and int(value) > 0
    )


def _active_thread_census_order_is_valid(threads: object) -> bool:
    """Accept Discord's global or parent-grouped active-thread order."""

    if not isinstance(threads, list):
        return False
    identities: list[tuple[str, str]] = []
    for thread in threads:
        if not isinstance(thread, dict):
            return False
        thread_id = thread.get("id")
        parent_id = thread.get("parent_id")
        if not _valid_snowflake(thread_id) or not _valid_snowflake(parent_id):
            return False
        identities.append((thread_id, parent_id))

    thread_ids = [thread_id for thread_id, _ in identities]
    if len(thread_ids) != len(set(thread_ids)):
        return False

    globally_descending = thread_ids == sorted(thread_ids, key=int, reverse=True)
    completed_parents: set[str] = set()
    previous_parent: str | None = None
    previous_thread_id: str | None = None
    grouped_descending = True
    for thread_id, parent_id in identities:
        if parent_id != previous_parent:
            if parent_id in completed_parents:
                return False
            if previous_parent is not None:
                completed_parents.add(previous_parent)
            previous_parent = parent_id
        elif previous_thread_id is not None and int(thread_id) >= int(previous_thread_id):
            grouped_descending = False
        previous_thread_id = thread_id
    return globally_descending or grouped_descending


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256.fullmatch(value))


def _valid_nonnegative_int(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0
