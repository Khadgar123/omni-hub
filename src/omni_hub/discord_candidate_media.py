"""Build a redacted, closure-bound inventory of candidate Discord media."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, BinaryIO

from .discord_message_evidence import MediaOccurrence, extract_message_evidence
from .discord_reference_sidecar import _RootAnchor
from .discord_media_recovery import (
    discord_declared_size_mismatch,
    discord_media_mime_outcome,
    normalized_discord_media_mime,
    validate_media_record_attempt_consistency,
    validate_media_record_producer_metadata,
)
from .discord_sharding import canonical_json_sha256


_MANIFEST_KIND = "discord-candidate-media-manifest-v1"
_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}")
_BINARY_STATUSES = frozenset({"complete", "captured_with_warning"})
_PARTITIONS = ("captured", "failed", "reference_only", "pending")
_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "authorization",
        "candidate_urls",
        "content",
        "logical_key",
        "message_body",
        "observed_urls",
        "proxy_url",
        "token",
        "url",
    }
)
_MIME_EXTENSIONS = {
    "application/pdf": "pdf",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "image/gif": "gif",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "video/webm": "webm",
}


@dataclass(frozen=True, slots=True)
class _MessageProvenance:
    stream: str
    raw_path: Path
    evidence_path: Path
    raw_sha256: str
    json_pointer: str


@dataclass(frozen=True, slots=True)
class _Snapshot:
    message_id: str
    channel_id: str
    value: Mapping[str, Any]
    sha256: str
    provenances: tuple[_MessageProvenance, ...]


@dataclass(frozen=True, slots=True)
class _OccurrenceGroup:
    snapshot: _Snapshot
    occurrence: MediaOccurrence
    sources: tuple[Mapping[str, Any], ...]
    media_identity_sha256: str
    source_provenance_sha256: str


@dataclass(frozen=True, slots=True)
class _AssetMatch:
    shard_index: int
    run_root: Path
    record: Mapping[str, Any]


def build_candidate_media_manifest(
    *,
    export_root: Path,
    candidate_message_refs: Sequence[str],
    source_hashes: Mapping[str, str],
) -> dict[str, object]:
    """Return a deterministic media inventory without network or OCR work.

    ``source_hashes`` is a path-to-SHA256 commitment map.  It must identify one
    formal ``capture/closure-audit.json`` below ``export_root``.  Additional
    committed files are verified but never interpreted as URLs.
    """

    candidate_ids = _candidate_ids(candidate_message_refs)
    commitments, closure_relative = _source_commitments(source_hashes)
    anchor = _RootAnchor.open(Path(export_root).absolute())
    try:
        for relative, expected in commitments:
            content = anchor.read_regular(relative, "candidate media source")
            if _digest(content) != expected:
                raise ValueError("Discord candidate media source hash changed")

        closure, closure_sha = _read_json(
            anchor, closure_relative, "candidate media closure audit"
        )
        if closure_sha != dict(commitments)[closure_relative]:
            raise ValueError("Discord candidate media closure commitment changed")
        capture_dir = closure_relative.parent
        namespace_dir = capture_dir.parent
        head_relative = capture_dir / "head-catchup.json"
        merge_relative = namespace_dir / "merge-audit.json"
        request_relative = namespace_dir / "merge-request.json"
        head, head_sha = _read_json(anchor, head_relative, "head catch-up")
        merge, merge_sha = _read_json(anchor, merge_relative, "merge audit")
        request, request_sha = _read_json(anchor, request_relative, "merge request")
        _validate_closure(
            closure,
            closure_sha=closure_sha,
            head=head,
            head_sha=head_sha,
            merge=merge,
            merge_sha=merge_sha,
            request=request,
            request_sha=request_sha,
        )

        snapshots, snapshot_sources = _load_candidate_snapshots(
            anchor,
            head=head,
            request=request,
            merge=merge,
            candidate_ids=candidate_ids,
        )
        occurrences: list[_OccurrenceGroup] = []
        candidate_keys: set[str] = set()
        for snapshot in snapshots:
            groups = _snapshot_occurrence_groups(snapshot)
            occurrences.extend(groups)
            candidate_keys.update(group.occurrence.logical_key for group in groups)

        asset_matches, asset_sources = _load_candidate_asset_records(
            anchor,
            request=request,
            merge=merge,
            logical_keys=candidate_keys,
        )
        items = [
            _manifest_item(anchor, group, asset_matches)
            for group in occurrences
        ]
        items.sort(key=lambda item: (int(str(item["message_id"])), str(item["occurrence_id"])))
        counts = _counts(candidate_ids, items)
        sources = {
            "closure_audit": _source_row(closure_relative, closure_sha),
            "head_catchup": _source_row(head_relative, head_sha),
            "merge_audit": _source_row(merge_relative, merge_sha),
            "merge_request": _source_row(request_relative, request_sha),
            **snapshot_sources,
            "asset_indexes": asset_sources,
            "input_commitments": [
                _source_row(path, digest) for path, digest in commitments
            ],
        }
        manifest: dict[str, object] = {
            "schema_version": _SCHEMA_VERSION,
            "kind": _MANIFEST_KIND,
            "candidate_message_ids": sorted(candidate_ids, key=int),
            "sources": sources,
            "counts": counts,
            "items": items,
        }
        _reject_sensitive_output(manifest)
        anchor.verify_root_binding()
        return manifest
    finally:
        anchor.close()


def iter_ocr_input_rows(
    *, export_root: Path, manifest: Mapping[str, object]
) -> Iterator[dict[str, object]]:
    """Yield byte snapshots for verified captured images only."""

    _validate_manifest(manifest)
    root = Path(export_root).absolute()
    source_hashes = _manifest_source_hashes(manifest)
    candidate_ids = manifest.get("candidate_message_ids")
    assert isinstance(candidate_ids, list)
    rebuilt = build_candidate_media_manifest(
        export_root=root,
        candidate_message_refs=candidate_ids,
        source_hashes=source_hashes,
    )
    if dict(manifest) != rebuilt:
        raise ValueError("Discord candidate media manifest provenance changed")
    items = rebuilt["items"]
    assert isinstance(items, list)
    for item in items:
        if item["ocr_eligible"] is not True:
            continue
        blob = item.get("blob")
        if not isinstance(blob, Mapping):
            raise ValueError("Discord candidate OCR blob descriptor is missing")
        anchor = _RootAnchor.open(root)
        try:
            content = _validated_manifest_blob(anchor, blob)
            row: dict[str, object] = {
                "occurrence_id": item["occurrence_id"],
                "message_id": item["message_id"],
                "sha256": blob["sha256"],
                "bytes": blob["bytes"],
                "mime_type": blob["mime_type"],
                "content": content,
            }
            anchor.verify_root_binding()
        finally:
            anchor.close()
        yield row


def _candidate_ids(values: Sequence[str]) -> set[str]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise ValueError("Discord candidate message IDs are invalid")
    ids = list(values)
    if not ids or len(ids) != len(set(ids)) or any(not _snowflake(value) for value in ids):
        raise ValueError("Discord candidate message IDs are invalid")
    return set(ids)


def _source_commitments(
    source_hashes: Mapping[str, str],
) -> tuple[list[tuple[Path, str]], Path]:
    if not isinstance(source_hashes, Mapping) or not source_hashes:
        raise ValueError("Discord candidate media source commitments are invalid")
    commitments: list[tuple[Path, str]] = []
    closures: list[Path] = []
    for value, digest in source_hashes.items():
        relative = _relative_path(value, "source commitment")
        if not _valid_sha(digest):
            raise ValueError("Discord candidate media source commitment is invalid")
        commitments.append((relative, digest))
        if relative.name == "closure-audit.json" and relative.parent.name == "capture":
            closures.append(relative)
    commitments.sort(key=lambda item: item[0].as_posix())
    if len(closures) != 1:
        raise ValueError("Discord candidate media closure commitment is ambiguous")
    return commitments, closures[0]


def _validate_closure(
    closure: object,
    *,
    closure_sha: str,
    head: object,
    head_sha: str,
    merge: object,
    merge_sha: str,
    request: object,
    request_sha: str,
) -> None:
    if (
        not isinstance(closure, Mapping)
        or closure.get("audit_kind") != "discord-parent-family-closure-v1"
        or closure.get("validation_errors") != []
        or not _valid_sha(closure_sha)
    ):
        raise ValueError("Discord candidate media closure audit is invalid")
    bindings = closure.get("input_file_sha256")
    canonical = closure.get("input_canonical_sha256")
    if (
        not isinstance(bindings, Mapping)
        or not isinstance(canonical, Mapping)
        or bindings.get("head_catchup") != head_sha
        or bindings.get("merge_audit") != merge_sha
        or not isinstance(head, Mapping)
        or not isinstance(merge, Mapping)
        or canonical.get("head_catchup") != canonical_json_sha256(head)
        or canonical.get("merge_audit") != canonical_json_sha256(merge)
    ):
        raise ValueError("Discord candidate media closure bindings are invalid")
    unresolved = closure.get("unresolved")
    if not isinstance(unresolved, Mapping):
        raise ValueError("Discord candidate media closure unresolved state is invalid")
    for field in (
        "target_ids",
        "missing_target_ids",
        "unexpected_target_ids",
        "invalid_delta_target_ids",
        "unverified_evidence_target_ids",
        "non_private_incomplete_streams",
        "message_reference_incomplete_shards",
    ):
        if unresolved.get(field) != []:
            raise ValueError("Discord candidate media closure is message-incomplete")
    captured = closure.get("captured_delta")
    captured_ids = _id_list(
        captured.get("message_ids") if isinstance(captured, Mapping) else None,
        "closure delta",
    )
    head_targets = head.get("targets")
    if not isinstance(head_targets, list):
        raise ValueError("Discord candidate media head targets are invalid")
    head_ids: set[str] = set()
    target_ids: set[str] = set()
    for target in head_targets:
        if not isinstance(target, Mapping) or not _snowflake(target.get("id")):
            raise ValueError("Discord candidate media head target is invalid")
        target_id = str(target["id"])
        if target_id in target_ids:
            raise ValueError("Discord candidate media head targets are duplicated")
        target_ids.add(target_id)
        new_ids = _id_list(target.get("new_message_ids"), "head target delta")
        if head_ids.intersection(new_ids):
            raise ValueError("Discord candidate media head message ownership overlaps")
        head_ids.update(new_ids)
    if captured_ids != head_ids:
        raise ValueError("Discord explicit closure delta is inconsistent")
    if (
        merge.get("audit_kind") != "discord-parent-family-merge-v1"
        or merge.get("validation_errors") != []
        or merge.get("merge_request_sha256") != request_sha
        or not isinstance(request, Mapping)
    ):
        raise ValueError("Discord candidate media merge binding is invalid")
    scope = merge.get("static_scope")
    if not isinstance(scope, Mapping) or scope.get("exact_union") is not True or scope.get("pairwise_disjoint") is not True:
        raise ValueError("Discord candidate media merge scope is invalid")
    for field in (
        "non_private_incomplete_streams",
        "failed_streams",
        "truncated_streams",
        "message_reference_incomplete_shards",
    ):
        if merge.get(field) != []:
            raise ValueError("Discord candidate media merge is message-incomplete")


def _load_candidate_snapshots(
    anchor: _RootAnchor,
    *,
    head: Mapping[str, object],
    request: Mapping[str, object],
    merge: Mapping[str, object],
    candidate_ids: set[str],
) -> tuple[list[_Snapshot], dict[str, object]]:
    closure, target_sources, closure_raw_sources = _closure_candidate_snapshots(
        anchor, head, candidate_ids
    )
    (
        baseline,
        baseline_artifacts,
        baseline_raw_sources,
        message_evidence_sources,
    ) = _baseline_candidate_snapshots(anchor, request, merge, candidate_ids)

    selected: dict[str, _Snapshot] = {}
    for message_id in sorted(candidate_ids, key=int):
        current = closure.get(message_id)
        historical = baseline.get(message_id)
        if current is None and historical is None:
            raise ValueError(
                "Discord candidate message is outside the authorized baseline and closure union"
            )
        if current is None:
            assert historical is not None
            selected[message_id] = historical
            continue
        provenances = list(current.provenances)
        if historical is not None and historical.sha256 == current.sha256:
            provenances.extend(historical.provenances)
        selected[message_id] = _Snapshot(
            current.message_id,
            current.channel_id,
            current.value,
            current.sha256,
            _deduplicated_provenances(provenances),
        )

    sources: dict[str, object] = {
        "target_evidence": sorted(target_sources, key=lambda row: row["path"]),
        "raw_pages": _deduplicated_source_rows(
            [*closure_raw_sources, *baseline_raw_sources]
        ),
        "message_evidence": _deduplicated_source_rows(message_evidence_sources),
        "baseline_artifacts": sorted(
            baseline_artifacts,
            key=lambda row: (int(row["shard_index"]), str(row["artifact"])),
        ),
    }
    return [selected[value] for value in sorted(selected, key=int)], sources


def _closure_candidate_snapshots(
    anchor: _RootAnchor,
    head: Mapping[str, object],
    candidate_ids: set[str],
) -> tuple[dict[str, _Snapshot], list[dict[str, str]], list[dict[str, str]]]:
    selected: dict[str, _Snapshot] = {}
    target_sources: list[dict[str, str]] = []
    raw_sources: list[dict[str, str]] = []
    targets = head.get("targets")
    if not isinstance(targets, list):
        raise ValueError("Discord candidate media head targets are invalid")
    for target in targets:
        if not isinstance(target, Mapping):
            raise ValueError("Discord candidate media head target is invalid")
        target_id = str(target.get("id"))
        target_new_ids = _id_list(target.get("new_message_ids"), "head target delta")
        wanted = candidate_ids.intersection(target_new_ids)
        if not wanted:
            continue
        evidence_relative = _relative_path(
            target.get("evidence_path"), "head target evidence"
        )
        evidence, evidence_sha = _read_json(
            anchor, evidence_relative, "head target evidence"
        )
        if (
            evidence_sha != target.get("evidence_sha256")
            or not isinstance(evidence, Mapping)
            or evidence.get("audit_kind") != "discord-head-catchup-target-v1"
            or evidence.get("target_id") != target_id
            or _id_list(evidence.get("new_message_ids"), "target evidence delta")
            != target_new_ids
            or target.get("new_message_count") != len(target_new_ids)
        ):
            raise ValueError("Discord candidate media target evidence is invalid")
        target_sources.append(_source_row(evidence_relative, evidence_sha))
        descriptors = evidence.get("raw_pages")
        if not isinstance(descriptors, list) or not descriptors:
            raise ValueError("Discord candidate media raw-page ledger is invalid")
        observed: set[str] = set()
        for descriptor in sorted(
            descriptors,
            key=lambda row: str(row.get("path", ""))
            if isinstance(row, Mapping)
            else "",
        ):
            if not isinstance(descriptor, Mapping):
                raise ValueError("Discord candidate media raw-page descriptor is invalid")
            relative = _relative_path(descriptor.get("path"), "closure raw page")
            expected_sha = descriptor.get("sha256")
            if not _valid_sha(expected_sha):
                raise ValueError("Discord candidate media raw-page hash is invalid")
            raw, raw_sha = _read_json(anchor, relative, "closure raw page")
            if raw_sha != expected_sha or not isinstance(raw, Mapping):
                raise ValueError("Discord candidate media raw-page binding is invalid")
            response = raw.get("response")
            messages = response.get("messages") if isinstance(response, Mapping) else None
            if not isinstance(messages, list):
                raise ValueError("Discord candidate media closure response is invalid")
            page_used = False
            for index, message in enumerate(messages):
                if not isinstance(message, Mapping) or message.get("channel_id") != target_id:
                    raise ValueError("Discord candidate media closure message is invalid")
                message_id = message.get("id")
                if message_id not in wanted:
                    continue
                observed.add(str(message_id))
                page_used = True
                provenance = _MessageProvenance(
                    stream=f"messages_{target_id}",
                    raw_path=relative,
                    evidence_path=relative,
                    raw_sha256=raw_sha,
                    json_pointer=f"/response/messages/{index}",
                )
                _retain_snapshot(
                    selected,
                    _Snapshot(
                        str(message_id),
                        target_id,
                        message,
                        canonical_json_sha256(message),
                        (provenance,),
                    ),
                    "closure",
                )
            if page_used:
                raw_sources.append(_source_row(relative, raw_sha))
        if observed != wanted:
            raise ValueError("Discord explicit closure messages are not locatable")
    return selected, target_sources, raw_sources


def _baseline_candidate_snapshots(
    anchor: _RootAnchor,
    request: Mapping[str, object],
    merge: Mapping[str, object],
    candidate_ids: set[str],
) -> tuple[
    dict[str, _Snapshot],
    list[dict[str, object]],
    list[dict[str, str]],
    list[dict[str, str]],
]:
    shards = request.get("shards")
    artifact_hashes = merge.get("artifact_hashes")
    artifact_flags = merge.get("artifact_hash_verification")
    static_targets = _id_list(merge.get("static_target_ids"), "merge static target")
    if (
        not isinstance(shards, list)
        or not shards
        or not isinstance(artifact_hashes, Mapping)
        or not isinstance(artifact_flags, Mapping)
    ):
        raise ValueError("Discord candidate media baseline shard evidence is invalid")
    selected: dict[str, _Snapshot] = {}
    artifact_sources: list[dict[str, object]] = []
    raw_sources: list[dict[str, str]] = []
    evidence_sources: list[dict[str, str]] = []
    seen_indices: set[int] = set()
    for shard in sorted(
        shards,
        key=lambda row: int(row.get("index", 0)) if isinstance(row, Mapping) else 0,
    ):
        if not isinstance(shard, Mapping):
            raise ValueError("Discord candidate media baseline shard is invalid")
        index = shard.get("index")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index <= 0
            or index in seen_indices
        ):
            raise ValueError("Discord candidate media baseline shard index is invalid")
        seen_indices.add(index)
        run_root = _relative_path(shard.get("run_root"), "baseline run root")
        checkpoint, rows = _verified_baseline_artifacts(
            anchor,
            run_root=run_root,
            shard=shard,
            merge_hashes=artifact_hashes.get(str(index)),
            merge_flags=artifact_flags.get(str(index)),
            shard_index=index,
        )
        artifact_sources.extend(rows)
        streams = checkpoint.get("streams")
        if not isinstance(streams, Mapping):
            raise ValueError("Discord candidate media checkpoint streams are invalid")
        for stream, state in sorted(streams.items(), key=lambda item: str(item[0])):
            if not isinstance(stream, str) or not stream.startswith("messages_"):
                continue
            channel_id = stream.removeprefix("messages_")
            if channel_id not in static_targets:
                continue
            if not isinstance(state, Mapping):
                raise ValueError("Discord candidate media checkpoint stream is invalid")
            page_hashes = state.get("page_hashes")
            page_states = state.get("page_states")
            if (
                not isinstance(page_hashes, list)
                or not isinstance(page_states, list)
                or len(page_hashes) != len(page_states)
            ):
                raise ValueError("Discord candidate media checkpoint page ledger is invalid")
            for page_number, (raw_sha, page_state) in enumerate(
                zip(page_hashes, page_states, strict=True), start=1
            ):
                if not _valid_sha(raw_sha) or not isinstance(page_state, Mapping):
                    raise ValueError("Discord candidate media checkpoint page is invalid")
                run_relative = Path("pages") / stream / f"{page_number:06d}.json"
                root_relative = run_root / run_relative
                content = anchor.read_regular(root_relative, "baseline raw page")
                if _digest(content) != raw_sha:
                    raise ValueError("Discord candidate media baseline page hash changed")
                if not any(value.encode("ascii") in content for value in candidate_ids):
                    continue
                try:
                    raw = json.loads(content)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("Discord candidate media baseline page is unreadable") from exc
                payload = raw.get("payload") if isinstance(raw, Mapping) else None
                if not isinstance(payload, list):
                    raise ValueError("Discord candidate media baseline payload is invalid")
                descriptor = page_state.get("message_evidence")
                rows, evidence_relative, evidence_sha = _baseline_evidence_rows(
                    anchor,
                    run_root=run_root,
                    run_relative=run_relative,
                    raw_sha=str(raw_sha),
                    descriptor=descriptor,
                    stream=stream,
                    channel_id=channel_id,
                    page_number=page_number,
                    payload=payload,
                )
                page_used = False
                for row_index, (message, evidence_row) in enumerate(
                    zip(payload, rows, strict=True)
                ):
                    message_id = message.get("id") if isinstance(message, Mapping) else None
                    if message_id not in candidate_ids:
                        continue
                    if message.get("channel_id") != channel_id:
                        raise ValueError("Discord candidate media baseline channel is invalid")
                    page_used = True
                    pointer = str(evidence_row["message_json_pointer"])
                    provenance = _MessageProvenance(
                        stream=stream,
                        raw_path=root_relative,
                        evidence_path=run_relative,
                        raw_sha256=str(raw_sha),
                        json_pointer=pointer,
                    )
                    _retain_snapshot(
                        selected,
                        _Snapshot(
                            str(message_id),
                            channel_id,
                            message,
                            canonical_json_sha256(message),
                            (provenance,),
                        ),
                        "baseline",
                    )
                if page_used:
                    raw_sources.append(_source_row(root_relative, str(raw_sha)))
                    evidence_sources.append(
                        _source_row(evidence_relative, evidence_sha)
                    )
    if set(str(key) for key in artifact_hashes) != {
        str(value) for value in seen_indices
    } or set(str(key) for key in artifact_flags) != {
        str(value) for value in seen_indices
    }:
        raise ValueError("Discord candidate media baseline shard coverage is invalid")
    return selected, artifact_sources, raw_sources, evidence_sources


def _verified_baseline_artifacts(
    anchor: _RootAnchor,
    *,
    run_root: Path,
    shard: Mapping[str, object],
    merge_hashes: object,
    merge_flags: object,
    shard_index: int,
) -> tuple[Mapping[str, object], list[dict[str, object]]]:
    filenames = {
        "request": "request.json",
        "manifest": "manifest.json",
        "checkpoint": "checkpoint.json",
        "targets_inventory": "inventory/targets.json",
    }
    if (
        not isinstance(merge_hashes, Mapping)
        or not isinstance(merge_flags, Mapping)
        or not set(filenames).issubset(merge_hashes)
        or set(merge_flags) != set(merge_hashes)
        or any(
            not isinstance(record, Mapping)
            or record.get("verified") is not True
            or merge_flags.get(name) is not True
            for name, record in merge_hashes.items()
        )
    ):
        raise ValueError("Discord candidate media baseline artifacts are invalid")
    checkpoint: Mapping[str, object] | None = None
    sources: list[dict[str, object]] = []
    for name, filename in filenames.items():
        expected = shard.get(f"{name}_sha256")
        record = merge_hashes.get(name)
        relative = run_root / filename
        value, actual = _read_json(anchor, relative, f"baseline {name}")
        if (
            not _valid_sha(expected)
            or actual != expected
            or not isinstance(record, Mapping)
            or record.get("expected") != expected
            or record.get("actual") != expected
            or record.get("verified") is not True
            or merge_flags.get(name) is not True
        ):
            raise ValueError("Discord candidate media baseline artifact hash changed")
        sources.append(
            {
                "shard_index": shard_index,
                "artifact": name,
                **_source_row(relative, actual),
            }
        )
        if name == "checkpoint":
            if not isinstance(value, Mapping):
                raise ValueError("Discord candidate media checkpoint is invalid")
            checkpoint = value
    assert checkpoint is not None
    return checkpoint, sources


def _baseline_evidence_rows(
    anchor: _RootAnchor,
    *,
    run_root: Path,
    run_relative: Path,
    raw_sha: str,
    descriptor: object,
    stream: str,
    channel_id: str,
    page_number: int,
    payload: Sequence[object],
) -> tuple[list[Mapping[str, object]], Path, str]:
    if not isinstance(descriptor, Mapping) or descriptor.get("schema_version") != 2:
        raise ValueError("Discord candidate media message-evidence descriptor is invalid")
    evidence_run_relative = _relative_path(
        descriptor.get("path"), "baseline message evidence"
    )
    evidence_relative = run_root / evidence_run_relative
    content = anchor.read_regular(evidence_relative, "baseline message evidence")
    evidence_sha = _digest(content)
    if (
        evidence_sha != descriptor.get("sha256")
        or descriptor.get("raw_page_path") != run_relative.as_posix()
        or descriptor.get("raw_page_sha256") != raw_sha
        or descriptor.get("stream") != stream
        or descriptor.get("channel_id") != channel_id
        or descriptor.get("page_number") != page_number
    ):
        raise ValueError("Discord candidate media message-evidence binding is invalid")
    rows: list[Mapping[str, object]] = []
    for line in content.splitlines():
        try:
            row = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Discord candidate media message evidence is unreadable") from exc
        if not isinstance(row, Mapping):
            raise ValueError("Discord candidate media message-evidence row is invalid")
        rows.append(row)
    if len(rows) != len(payload):
        raise ValueError("Discord candidate media raw/evidence row count differs")
    for message, row in zip(payload, rows, strict=True):
        _validate_baseline_evidence_row(
            message,
            row,
            stream=stream,
            channel_id=channel_id,
            page_number=page_number,
            raw_path=run_relative,
            raw_sha=raw_sha,
        )
    return rows, evidence_relative, evidence_sha


def _validate_baseline_evidence_row(
    message: object,
    row: Mapping[str, object],
    *,
    stream: str,
    channel_id: str,
    page_number: int,
    raw_path: Path,
    raw_sha: str,
) -> None:
    pointer = row.get("message_json_pointer")
    nodes = row.get("nodes")
    if (
        not isinstance(message, Mapping)
        or row.get("schema_version") != 2
        or row.get("stream") != stream
        or row.get("channel_id") != channel_id
        or row.get("page_number") != page_number
        or not isinstance(pointer, str)
        or not isinstance(nodes, list)
        or not nodes
        or not isinstance(nodes[0], Mapping)
    ):
        raise ValueError("Discord candidate media message-evidence row is invalid")
    root = nodes[0]
    if (
        root.get("kind") != "root"
        or root.get("message_id") != message.get("id")
        or root.get("channel_id") != message.get("channel_id")
        or root.get("json_pointer") != pointer
    ):
        raise ValueError("Discord candidate media message-evidence root is invalid")
    media = row.get("media")
    if not isinstance(media, list):
        raise ValueError("Discord candidate media message-evidence media is invalid")
    for occurrence in media:
        source = occurrence.get("source") if isinstance(occurrence, Mapping) else None
        if (
            not isinstance(source, Mapping)
            or source.get("stream") != stream
            or source.get("evidence_path") != raw_path.as_posix()
            or source.get("evidence_sha256") != raw_sha
        ):
            raise ValueError("Discord candidate media baseline source binding is invalid")


def _retain_snapshot(
    selected: dict[str, _Snapshot], snapshot: _Snapshot, label: str
) -> None:
    previous = selected.get(snapshot.message_id)
    if previous is None:
        selected[snapshot.message_id] = snapshot
        return
    if previous.sha256 != snapshot.sha256 or previous.channel_id != snapshot.channel_id:
        raise ValueError(f"Discord candidate {label} snapshots conflict")
    selected[snapshot.message_id] = _Snapshot(
        previous.message_id,
        previous.channel_id,
        previous.value,
        previous.sha256,
        _deduplicated_provenances([*previous.provenances, *snapshot.provenances]),
    )


def _deduplicated_provenances(
    values: Sequence[_MessageProvenance],
) -> tuple[_MessageProvenance, ...]:
    unique = {
        (
            value.stream,
            value.raw_path.as_posix(),
            value.evidence_path.as_posix(),
            value.raw_sha256,
            value.json_pointer,
        ): value
        for value in values
    }
    return tuple(unique[key] for key in sorted(unique))


def _snapshot_occurrence_groups(snapshot: _Snapshot) -> list[_OccurrenceGroup]:
    grouped: dict[tuple[str, str, str, str], tuple[MediaOccurrence, list[Mapping[str, Any]], str]] = {}
    for provenance in snapshot.provenances:
        evidence = extract_message_evidence(
            snapshot.value,
            stream=provenance.stream,
            evidence_path=provenance.evidence_path.as_posix(),
            evidence_sha256=provenance.raw_sha256,
            json_pointer=provenance.json_pointer,
        )
        for occurrence in evidence.media:
            key = (
                occurrence.logical_key,
                occurrence.kind,
                occurrence.field,
                occurrence.node_key,
            )
            value = asdict(occurrence)
            source = value.pop("source")
            # The page-local prefix differs between baseline ``/payload`` and
            # closure ``/response/messages`` snapshots; logical identity and
            # the retained producer metadata bind the same media occurrence.
            value.pop("json_pointer")
            occurrence_sha = canonical_json_sha256(value)
            previous = grouped.get(key)
            if previous is None:
                grouped[key] = (occurrence, [source], occurrence_sha)
            elif previous[2] != occurrence_sha:
                raise ValueError("Discord same-snapshot media occurrences conflict")
            else:
                previous[1].append(source)
    output: list[_OccurrenceGroup] = []
    for key in sorted(grouped):
        occurrence, sources, media_sha = grouped[key]
        unique_sources = {
            canonical_json_sha256(source): source for source in sources
        }
        ordered_sources = tuple(unique_sources[value] for value in sorted(unique_sources))
        output.append(
            _OccurrenceGroup(
                snapshot,
                occurrence,
                ordered_sources,
                media_sha,
                canonical_json_sha256(list(ordered_sources)),
            )
        )
    return output


def _load_candidate_asset_records(
    anchor: _RootAnchor,
    *,
    request: Mapping[str, object],
    merge: Mapping[str, object],
    logical_keys: set[str],
) -> tuple[dict[str, list[_AssetMatch]], list[dict[str, object]]]:
    shards = request.get("shards")
    transitive = merge.get("transitive_evidence")
    if not isinstance(shards, list) or not shards or not isinstance(transitive, Mapping):
        raise ValueError("Discord candidate media shard evidence is invalid")
    seen_indices: set[int] = set()
    matches: dict[str, list[_AssetMatch]] = {key: [] for key in logical_keys}
    source_rows: list[dict[str, object]] = []
    for shard in sorted(shards, key=lambda row: int(row.get("index", 0)) if isinstance(row, Mapping) else 0):
        if not isinstance(shard, Mapping):
            raise ValueError("Discord candidate media shard is invalid")
        index = shard.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index <= 0 or index in seen_indices:
            raise ValueError("Discord candidate media shard index is invalid")
        seen_indices.add(index)
        run_root = _relative_path(shard.get("run_root"), "shard run root")
        evidence = transitive.get(str(index))
        asset = evidence.get("asset_evidence") if isinstance(evidence, Mapping) else None
        expected_sha = asset.get("asset_index_sha256") if isinstance(asset, Mapping) else None
        if (
            not isinstance(evidence, Mapping)
            or evidence.get("validation_errors") != []
            or not isinstance(asset, Mapping)
            or asset.get("validation_errors") != []
            or not _valid_sha(expected_sha)
        ):
            raise ValueError("Discord candidate media asset evidence is invalid")
        index_relative = run_root / "asset-index.jsonl"
        found, actual_sha = _scan_asset_index(
            anchor,
            index_relative,
            expected_sha=str(expected_sha),
            logical_keys=logical_keys,
        )
        for logical_key, records in found.items():
            matches[logical_key].extend(
                _AssetMatch(index, run_root, record) for record in records
            )
        source_rows.append(
            {
                "shard_index": index,
                **_source_row(index_relative, actual_sha),
            }
        )
    if set(str(key) for key in transitive) != {str(value) for value in seen_indices}:
        raise ValueError("Discord candidate media shard evidence coverage is invalid")
    return matches, source_rows


def _scan_asset_index(
    anchor: _RootAnchor,
    relative: Path,
    *,
    expected_sha: str,
    logical_keys: set[str],
) -> tuple[dict[str, list[Mapping[str, Any]]], str]:
    found: dict[str, list[Mapping[str, Any]]] = {key: [] for key in logical_keys}
    needles = {
        key: b'"logical_key":'
        + json.dumps(key, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        for key in logical_keys
    }
    digest = hashlib.sha256()
    with _open_regular(anchor, relative, "asset index") as source:
        for line in source:
            digest.update(line)
            matched = [key for key, needle in needles.items() if needle in line]
            if not matched:
                continue
            try:
                record = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Discord candidate media asset row is unreadable") from exc
            if not isinstance(record, Mapping):
                raise ValueError("Discord candidate media asset row is invalid")
            logical_key = record.get("logical_key")
            if logical_key in found:
                found[str(logical_key)].append(record)
    actual_sha = digest.hexdigest()
    if actual_sha != expected_sha:
        raise ValueError("Discord candidate media asset index hash changed")
    return found, actual_sha


def _manifest_item(
    anchor: _RootAnchor,
    group: _OccurrenceGroup,
    matches: Mapping[str, list[_AssetMatch]],
) -> dict[str, object]:
    snapshot = group.snapshot
    occurrence = group.occurrence
    expected_sources = group.sources
    exact: list[_AssetMatch] = []
    for match in matches.get(occurrence.logical_key, []):
        record = match.record
        sources = record.get("sources")
        observations = record.get("observations")
        if not isinstance(sources, list) or not isinstance(observations, list):
            raise ValueError("Discord candidate media asset provenance is invalid")
        matched = False
        for expected_source in expected_sources:
            source_match = any(source == expected_source for source in sources)
            matching_observations = [
                observation
                for observation in observations
                if isinstance(observation, Mapping)
                and observation.get("source") == expected_source
            ]
            if source_match != bool(matching_observations):
                raise ValueError("Discord candidate media asset provenance conflicts")
            if source_match:
                if not any(
                    observation.get("metadata") == occurrence.metadata
                    for observation in matching_observations
                ):
                    raise ValueError(
                        "Discord candidate media observation metadata conflicts"
                    )
                matched = True
        if matched:
            validate_media_record_producer_metadata(record)
            exact.append(match)
    if len(exact) > 1:
        raise ValueError("Discord candidate media occurrence has duplicate asset states")

    declared_mime = normalized_discord_media_mime(
        occurrence.metadata.get("content_type")
        if isinstance(occurrence.metadata, Mapping)
        else None
    )
    status = "pending"
    reason = "not_present_in_closure_bound_asset_indexes"
    blob: dict[str, object] | None = None
    ocr_eligible = False
    if exact:
        match = exact[0]
        record = match.record
        if (
            record.get("kind") != occurrence.kind
            or record.get("field") != occurrence.field
        ):
            raise ValueError("Discord candidate media asset kind or field conflicts")
        if normalized_discord_media_mime(record.get("declared_content_type")) != declared_mime:
            raise ValueError("Discord candidate media declared MIME conflicts")
        record_status = record.get("status")
        if record_status in _BINARY_STATUSES:
            validate_media_record_attempt_consistency(record)
            content, descriptor = _validated_record_blob(anchor, match)
            del content
            status = "captured"
            reason = str(record.get("terminal_reason") or "binary_captured")
            blob = descriptor
            ocr_eligible = str(descriptor["mime_type"]).startswith("image/")
        elif record_status in {"failed", "reference_only"}:
            _validate_nonbinary_record(record)
            status = str(record_status)
            reason = str(record.get("terminal_reason") or record_status)
        elif record_status == "in_progress":
            validate_media_record_attempt_consistency(record)
            _validate_pending_record(record)
            reason = (
                "resolution_retry_interrupted"
                if record.get("terminal_reason") == "interrupted"
                else "resolution_retry_in_progress"
            )
        else:
            raise ValueError("Discord candidate media asset status is invalid")

    occurrence_id = canonical_json_sha256(
        {
            "message_id": snapshot.message_id,
            "snapshot_sha256": snapshot.sha256,
            "media_identity_sha256": group.media_identity_sha256,
            "source_provenance_sha256": group.source_provenance_sha256,
        }
    )
    return {
        "occurrence_id": occurrence_id,
        "message_id": snapshot.message_id,
        "channel_id": snapshot.channel_id,
        "snapshot_sha256": snapshot.sha256,
        "media_identity_sha256": group.media_identity_sha256,
        "source_provenance_sha256": group.source_provenance_sha256,
        "kind": occurrence.kind,
        "field": occurrence.field,
        "declared_mime_type": declared_mime,
        "downloadable": occurrence.downloadable,
        "resolution": occurrence.resolution,
        "status": status,
        "status_reason": reason,
        "ocr_eligible": ocr_eligible,
        "blob": blob,
    }


def _validated_record_blob(
    anchor: _RootAnchor, match: _AssetMatch
) -> tuple[bytes, dict[str, object]]:
    record = match.record
    actual_bytes = record.get("actual_bytes")
    digest = record.get("sha256")
    blob_value = record.get("blob_path")
    if (
        isinstance(actual_bytes, bool)
        or not isinstance(actual_bytes, int)
        or actual_bytes <= 0
        or not _valid_sha(digest)
        or not isinstance(blob_value, str)
    ):
        raise ValueError("Discord candidate media binary evidence is invalid")
    blob_relative = _relative_path(blob_value, "asset blob")
    _validate_content_address(blob_relative, str(digest))
    root_relative = match.run_root / blob_relative
    content = anchor.read_regular(root_relative, "candidate media blob")
    if len(content) != actual_bytes or _digest(content) != digest:
        raise ValueError("Discord candidate media blob evidence changed")
    http_length = record.get("http_content_length")
    if http_length is not None and http_length != actual_bytes:
        raise ValueError("Discord candidate media HTTP byte count conflicts")
    declared = normalized_discord_media_mime(record.get("declared_content_type"))
    actual_mime = normalized_discord_media_mime(record.get("http_content_type"))
    if actual_mime not in _MIME_EXTENSIONS:
        raise ValueError("Discord candidate media binary MIME is unsupported")
    outcome = discord_media_mime_outcome(record, declared, actual_mime)
    status = record.get("status")
    reason = record.get("terminal_reason")
    if outcome is not None:
        if outcome[0] != status or outcome[1] != reason:
            raise ValueError("Discord candidate media MIME outcome conflicts")
    elif status == "complete":
        if (
            reason != "downloaded"
            or discord_declared_size_mismatch(record, actual_bytes)
        ):
            raise ValueError("Discord candidate media complete outcome is invalid")
    elif status == "captured_with_warning":
        if not (
            reason == "declared_size_mismatch"
            and discord_declared_size_mismatch(record, actual_bytes)
        ):
            raise ValueError("Discord candidate media warning outcome is invalid")
    else:
        raise ValueError("Discord candidate media binary status is invalid")
    if blob_relative.suffix != f".{_MIME_EXTENSIONS[actual_mime]}":
        raise ValueError("Discord candidate media blob extension conflicts with MIME")
    _validate_magic(content, actual_mime)
    return content, {
        "path": root_relative.as_posix(),
        "sha256": str(digest),
        "bytes": actual_bytes,
        "mime_type": actual_mime,
    }


def _validate_nonbinary_record(record: Mapping[str, object]) -> None:
    actual_bytes = record.get("actual_bytes")
    if (
        actual_bytes != 0
        or record.get("sha256") is not None
        or record.get("blob_path") is not None
        or record.get("http_content_type") is not None
        or record.get("http_content_length") is not None
        or record.get("failure_detail") is not None
        or not isinstance(record.get("terminal_reason"), str)
        or not record.get("terminal_reason")
    ):
        raise ValueError("Discord candidate media non-binary state is invalid")


def _validate_pending_record(record: Mapping[str, object]) -> None:
    attempts = record.get("attempt_history")
    reason = record.get("terminal_reason")
    if (
        reason not in {None, "interrupted"}
        or record.get("failure_detail") is not None
        or record.get("actual_bytes") != 0
        or record.get("sha256") is not None
        or record.get("blob_path") is not None
        or record.get("http_content_type") is not None
        or record.get("http_content_length") is not None
        or not isinstance(attempts, list)
        or not attempts
        or not isinstance(attempts[-1], Mapping)
        or attempts[-1].get("status")
        != ("interrupted" if reason == "interrupted" else "in_progress")
        or attempts[-1].get("terminal_reason") != reason
        or attempts[-1].get("failure_detail") is not None
    ):
        raise ValueError("Discord candidate media pending state is invalid")


def _counts(candidate_ids: set[str], items: Sequence[Mapping[str, object]]) -> dict[str, int]:
    by_status = {status: 0 for status in _PARTITIONS}
    with_media: set[str] = set()
    ocr_eligible = 0
    occurrence_ids: set[str] = set()
    for item in items:
        status = item.get("status")
        occurrence_id = item.get("occurrence_id")
        message_id = item.get("message_id")
        if (
            status not in by_status
            or not _valid_sha(occurrence_id)
            or occurrence_id in occurrence_ids
            or message_id not in candidate_ids
        ):
            raise ValueError("Discord candidate media item partition is invalid")
        occurrence_ids.add(str(occurrence_id))
        by_status[str(status)] += 1
        with_media.add(str(message_id))
        ocr_eligible += item.get("ocr_eligible") is True
    if sum(by_status.values()) != len(items):
        raise ValueError("Discord candidate media partition does not conserve occurrences")
    return {
        "candidate_messages": len(candidate_ids),
        "candidate_messages_with_media": len(with_media),
        "occurrences": len(items),
        **by_status,
        "ocr_eligible_images": ocr_eligible,
    }


def _validate_manifest(manifest: Mapping[str, object]) -> list[Mapping[str, object]]:
    if (
        not isinstance(manifest, Mapping)
        or set(manifest)
        != {"schema_version", "kind", "candidate_message_ids", "sources", "counts", "items"}
        or manifest.get("schema_version") != _SCHEMA_VERSION
        or manifest.get("kind") != _MANIFEST_KIND
    ):
        raise ValueError("Discord candidate media manifest is invalid")
    _reject_sensitive_output(manifest)
    candidate_ids = _candidate_ids(manifest.get("candidate_message_ids"))  # type: ignore[arg-type]
    items = manifest.get("items")
    if not isinstance(items, list) or any(not isinstance(item, Mapping) for item in items):
        raise ValueError("Discord candidate media manifest items are invalid")
    expected_counts = _counts(candidate_ids, items)
    if manifest.get("counts") != expected_counts:
        raise ValueError("Discord candidate media manifest counts are invalid")
    for item in items:
        if set(item) != {
            "occurrence_id",
            "message_id",
            "channel_id",
            "snapshot_sha256",
            "media_identity_sha256",
            "source_provenance_sha256",
            "kind",
            "field",
            "declared_mime_type",
            "downloadable",
            "resolution",
            "status",
            "status_reason",
            "ocr_eligible",
            "blob",
        }:
            raise ValueError("Discord candidate media manifest item schema is invalid")
        if not all(
            _valid_sha(item.get(field))
            for field in (
                "occurrence_id",
                "snapshot_sha256",
                "media_identity_sha256",
                "source_provenance_sha256",
            )
        ):
            raise ValueError("Discord candidate media manifest item identity is invalid")
        blob = item.get("blob")
        status = item.get("status")
        eligible = item.get("ocr_eligible")
        if status == "captured":
            if not isinstance(blob, Mapping):
                raise ValueError("Discord candidate media captured blob is missing")
            mime = blob.get("mime_type")
            if eligible is not (isinstance(mime, str) and mime.startswith("image/")):
                raise ValueError("Discord candidate media OCR eligibility is invalid")
        elif blob is not None or eligible is not False:
            raise ValueError("Discord candidate media non-binary item is invalid")
    return items


def _manifest_source_hashes(manifest: Mapping[str, object]) -> dict[str, str]:
    sources = manifest.get("sources")
    expected_fields = {
        "closure_audit",
        "head_catchup",
        "merge_audit",
        "merge_request",
        "target_evidence",
        "raw_pages",
        "message_evidence",
        "baseline_artifacts",
        "asset_indexes",
        "input_commitments",
    }
    if not isinstance(sources, Mapping) or set(sources) != expected_fields:
        raise ValueError("Discord candidate media manifest sources are invalid")
    commitments = sources.get("input_commitments")
    if not isinstance(commitments, list) or not commitments:
        raise ValueError("Discord candidate media manifest commitments are invalid")
    output: dict[str, str] = {}
    for row in commitments:
        if not isinstance(row, Mapping) or set(row) != {"path", "sha256"}:
            raise ValueError("Discord candidate media manifest commitment is invalid")
        relative = _relative_path(row.get("path"), "manifest commitment")
        digest = row.get("sha256")
        if not _valid_sha(digest) or relative.as_posix() in output:
            raise ValueError("Discord candidate media manifest commitment is invalid")
        output[relative.as_posix()] = str(digest)
    return output


def _validated_manifest_blob(
    anchor: _RootAnchor, blob: Mapping[str, object]
) -> bytes:
    if set(blob) != {"path", "sha256", "bytes", "mime_type"}:
        raise ValueError("Discord candidate OCR blob descriptor is invalid")
    relative = _relative_path(blob.get("path"), "OCR blob")
    digest = blob.get("sha256")
    actual_bytes = blob.get("bytes")
    mime = normalized_discord_media_mime(blob.get("mime_type"))
    if (
        not _valid_sha(digest)
        or isinstance(actual_bytes, bool)
        or not isinstance(actual_bytes, int)
        or actual_bytes <= 0
        or mime not in _MIME_EXTENSIONS
        or not mime.startswith("image/")
    ):
        raise ValueError("Discord candidate OCR blob identity is invalid")
    _validate_content_address(_asset_suffix(relative), str(digest))
    if relative.suffix != f".{_MIME_EXTENSIONS[mime]}":
        raise ValueError("Discord candidate OCR MIME extension conflicts")
    content = anchor.read_regular(relative, "candidate OCR blob")
    if len(content) != actual_bytes or _digest(content) != digest:
        raise ValueError("Discord candidate OCR blob changed")
    _validate_magic(content, mime)
    return content


def _validate_content_address(relative: Path, digest: str) -> None:
    if (
        len(relative.parts) != 4
        or relative.parts[:2] != ("assets", "sha256")
        or relative.parts[2] != digest[:2]
        or not relative.parts[3].startswith(f"{digest}.")
    ):
        raise ValueError("Discord candidate media blob path is not content-addressed")


def _asset_suffix(relative: Path) -> Path:
    parts = relative.parts
    for index in range(len(parts) - 3):
        if parts[index : index + 2] == ("assets", "sha256"):
            suffix = Path(*parts[index:])
            if len(suffix.parts) == 4:
                return suffix
    raise ValueError("Discord candidate media blob path is outside an asset namespace")


def _validate_magic(content: bytes, mime: str) -> None:
    valid = {
        "application/pdf": lambda value: value.startswith(b"%PDF-"),
        "audio/mpeg": lambda value: value.startswith(b"ID3")
        or (len(value) >= 2 and value[0] == 0xFF and value[1] & 0xE0 == 0xE0),
        "audio/ogg": lambda value: value.startswith(b"OggS"),
        "image/gif": lambda value: value.startswith((b"GIF87a", b"GIF89a")),
        "image/jpeg": lambda value: value.startswith(b"\xff\xd8\xff"),
        "image/png": lambda value: value.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": lambda value: len(value) >= 12
        and value.startswith(b"RIFF")
        and value[8:12] == b"WEBP",
        "video/mp4": lambda value: len(value) >= 12 and value[4:8] == b"ftyp",
        "video/webm": lambda value: value.startswith(b"\x1aE\xdf\xa3"),
    }[mime](content)
    if not valid:
        raise ValueError("Discord candidate media blob does not match MIME")


class _RegularReader:
    def __init__(self, anchor: _RootAnchor, relative: Path, label: str) -> None:
        self.anchor = anchor
        self.relative = relative
        self.label = label
        self.chain = None
        self.file: BinaryIO | None = None

    def __enter__(self) -> BinaryIO:
        self.chain = self.anchor.directory(self.relative.parent, create=False)
        self.chain.verify()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.relative.name, flags, dir_fd=self.chain.fd)
        except OSError as exc:
            self.chain.close()
            self.chain = None
            raise ValueError(f"Discord {self.label} is missing or unsafe") from exc
        try:
            opened = os.fstat(descriptor)
            named = os.stat(
                self.relative.name, dir_fd=self.chain.fd, follow_symlinks=False
            )
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(named.st_mode)
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise ValueError(f"Discord {self.label} is not a regular file")
            self.file = os.fdopen(descriptor, "rb")
            return self.file
        except BaseException:
            os.close(descriptor)
            self.chain.close()
            self.chain = None
            raise

    def __exit__(self, *_args: object) -> None:
        assert self.file is not None and self.chain is not None
        self.file.close()
        self.chain.verify()
        self.chain.close()


def _open_regular(
    anchor: _RootAnchor, relative: Path, label: str
) -> _RegularReader:
    return _RegularReader(anchor, relative, label)


def _read_json(
    anchor: _RootAnchor, relative: Path, label: str
) -> tuple[object, str]:
    content = anchor.read_regular(relative, label)
    try:
        return json.loads(content), _digest(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Discord {label} is unreadable") from exc


def _source_row(relative: Path, digest: str) -> dict[str, str]:
    return {"path": relative.as_posix(), "sha256": digest}


def _deduplicated_source_rows(rows: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    by_path: dict[str, dict[str, str]] = {}
    for row in rows:
        previous = by_path.setdefault(row["path"], row)
        if previous != row:
            raise ValueError("Discord candidate media source hash conflicts")
    return [by_path[path] for path in sorted(by_path)]


def _relative_path(value: object, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise ValueError(f"Discord candidate media {label} path is invalid")
    path = Path(value)
    if not path.parts or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Discord candidate media {label} path is unsafe")
    return path


def _id_list(value: object, label: str) -> set[str]:
    if (
        not isinstance(value, list)
        or any(not _snowflake(item) for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(f"Discord candidate media {label} IDs are invalid")
    return set(value)


def _snowflake(value: object) -> bool:
    return isinstance(value, str) and value.isdecimal() and int(value) > 0


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _reject_sensitive_output(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = key.lower() if isinstance(key, str) else ""
            if (
                not isinstance(key, str)
                or lowered in _FORBIDDEN_OUTPUT_KEYS
                or any(
                    marker in lowered
                    for marker in ("authorization", "logical_key", "token", "url")
                )
            ):
                raise ValueError("Discord candidate media output contains a sensitive field")
            _reject_sensitive_output(item)
    elif isinstance(value, list):
        for item in value:
            _reject_sensitive_output(item)
    elif isinstance(value, str) and re.search(r"https?://", value, re.IGNORECASE):
        raise ValueError("Discord candidate media output contains a raw URL")


__all__ = ["build_candidate_media_manifest", "iter_ocr_input_rows"]
