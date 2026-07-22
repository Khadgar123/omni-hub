from __future__ import annotations

import argparse
from collections import Counter
import contextlib
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

import omni_hub.builtins as builtins_module
import omni_hub.discord_sharding as discord_sharding_module
from omni_hub.builtins import build_default_registry
from omni_hub.cli import discord
from omni_hub.connectors.discord import (
    DiscordAPIError,
    rfc2544_fake_ip_media_policy_descriptor,
)
from omni_hub.discord_sharding import (
    SHARD_SCHEME,
    audit_closure,
    build_parent_family_plan,
    canonical_json_sha256,
    target_set_sha256,
    write_closure_audit,
    write_merged_shard_audit,
    write_parent_family_plan,
)
from omni_hub.discord_message_evidence import extract_message_evidence
from omni_hub.discord_media_audit import (
    MEDIA_RECOVERY_AUDIT_FILENAME,
    MEDIA_RECOVERY_AUDIT_VERSION,
    build_media_recovery_audit,
    canonical_media_recovery_audit_bytes,
)
from omni_hub.discord_media_recovery import (
    discord_media_identity_metadata,
    media_resolution_context,
    migrate_legacy_media_record,
    normalized_discord_media_mime,
)
from omni_hub.discord_reference_sidecar import (
    MESSAGE_REFERENCE_RESOLUTION_AUDIT_VERSION,
    build_message_reference_resolution_audit,
    canonical_message_reference_resolution_audit_bytes,
    publish_message_reference_resolution_audit,
)
from omni_hub.models import OperationResult, OperationSpec, OperationStatus, RiskLevel
from omni_hub.runner import OperationRunner


_OXSUN_ROOT_ID = "1448266318880051292"
_DISCORD_EPOCH_MS = 1_420_070_400_000
_PREFLIGHT_SHA = "800d24f71fbf64bef234ae31f3d2e337c77ca9ff6228fc968aca44b53ceb44f1"


class _ScriptedClosureTransport:
    base_url = "https://discord.test/api/v10"

    def __init__(self) -> None:
        self.responses: dict[
            tuple[str, tuple[tuple[str, object], ...]],
            list[object],
        ] = {}
        self.calls: list[tuple[str, dict[str, object]]] = []

    def add(
        self,
        path: str,
        payload: object,
        params: dict[str, object] | None = None,
    ) -> None:
        key = (path, tuple(sorted((params or {}).items())))
        self.responses.setdefault(key, []).append(payload)

    def get_json(
        self,
        path: str,
        params: dict[str, object] | None = None,
    ) -> object:
        copied_params = dict(params or {})
        self.calls.append((path, copied_params))
        key = (path, tuple(sorted(copied_params.items())))
        queued = self.responses.get(key)
        if not queued:
            raise AssertionError(f"unexpected closure request: {path} {copied_params}")
        result = queued.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def assert_exhausted(self, test: unittest.TestCase) -> None:
        remaining = {
            key: values
            for key, values in self.responses.items()
            if values
        }
        test.assertEqual(remaining, {})


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _complete_message(message_id: str, channel_id: str) -> dict[str, object]:
    milliseconds = (int(message_id) >> 22) + _DISCORD_EPOCH_MS
    return {
        "id": message_id,
        "channel_id": channel_id,
        "timestamp": datetime.fromtimestamp(
            milliseconds / 1000,
            timezone.utc,
        ).isoformat(),
        "edited_timestamp": None,
        "author": {"id": "1", "username": "fixture-author"},
        "content": "",
        "attachments": [],
        "embeds": [],
        "components": [],
    }


def _fixture_binary_attempt(index: int = 0) -> dict[str, object]:
    blob_bytes = f"fixture-blob-{index}".encode()
    digest = _sha256_bytes(blob_bytes)
    return {
        "url": f"https://cdn.example/{index}",
        "status": "complete",
        "terminal_reason": "downloaded",
        "http_content_type": "application/octet-stream",
        "http_content_length": len(blob_bytes),
        "actual_bytes": len(blob_bytes),
        "sha256": digest,
        "blob_path": f"assets/sha256/{digest[:2]}/{digest}.bin",
    }


def _snowflake_lower_bound(timestamp: str) -> str:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    utc = parsed.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = utc - epoch
    unix_ms = (
        delta.days * 86_400_000
        + delta.seconds * 1_000
        + delta.microseconds // 1_000
    )
    return str((unix_ms - _DISCORD_EPOCH_MS) << 22)


def _write_json(path: Path, value: object) -> str:
    content = _canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return _sha256_bytes(content)


def _head_catchup_target(
    target_id: str,
    *,
    guild_id: str,
    t_close: str,
    caught_through: str | None = None,
    new_message_ids: tuple[str, ...] = (),
    new_thread_ids: tuple[str, ...] = (),
) -> tuple[dict[str, object], dict[str, object], dict[str, dict[str, object]]]:
    return _head_catchup_pages(
        target_id,
        guild_id=guild_id,
        t_close=t_close,
        caught_through=caught_through,
        message_pages=(new_message_ids,),
        new_thread_ids=new_thread_ids,
    )


def _head_catchup_pages(
    target_id: str,
    *,
    guild_id: str,
    t_close: str,
    message_pages: tuple[tuple[str, ...], ...],
    caught_through: str | None = None,
    new_thread_ids: tuple[str, ...] = (),
    page_limit: int = 100,
) -> tuple[dict[str, object], dict[str, object], dict[str, dict[str, object]]]:
    caught = caught_through or t_close
    raw_descriptors: list[dict[str, object]] = []
    raw_files: dict[str, dict[str, object]] = {}
    lower_bound = int(_snowflake_lower_bound(t_close))
    high_exclusive = int(_snowflake_lower_bound(caught))
    before = str(high_exclusive)
    for page_number, page_message_ids in enumerate(message_pages, start=1):
        terminal = page_number == len(message_pages)
        if terminal:
            next_cursor = None
            if not page_message_ids:
                terminal_reason = "empty_page"
            elif min(map(int, page_message_ids)) <= lower_bound:
                terminal_reason = "crossed_lower_bound"
            elif len(page_message_ids) < page_limit:
                terminal_reason = "short_page"
            else:
                raise ValueError("terminal catch-up fixture has no exhaustion proof")
        else:
            next_cursor = str(min(map(int, page_message_ids)))
            terminal_reason = None
        request = {
            "method": "GET",
            "path": f"/channels/{target_id}/messages",
            "params": {"before": before, "limit": page_limit},
        }
        response = {
            "status_code": 200,
            "messages": [
                {"id": message_id, "channel_id": target_id}
                for message_id in page_message_ids
            ],
            "threads": [
                {"id": thread_id, "parent_id": target_id}
                for thread_id in (new_thread_ids if terminal else ())
            ],
            "next_cursor": next_cursor,
            "terminal": terminal,
            "terminal_reason": terminal_reason,
        }
        raw_page = {
            "schema_version": 1,
            "audit_kind": "discord-head-catchup-raw-page-v1",
            "guild_id": guild_id,
            "target_id": target_id,
            "t_close": t_close,
            "t_close_source_sha256": _PREFLIGHT_SHA,
            "caught_through": caught,
            "request": request,
            "response": response,
        }
        raw_path = f"closure-evidence/raw/{target_id}/{page_number:06d}.json"
        raw_descriptors.append(
            {
                "path": raw_path,
                "sha256": canonical_json_sha256(raw_page),
                "request_sha256": canonical_json_sha256(request),
                "response_sha256": canonical_json_sha256(response),
            }
        )
        raw_files[raw_path] = raw_page
        if next_cursor is not None:
            before = next_cursor
    new_message_ids = tuple(
        message_id
        for page_message_ids in message_pages
        for message_id in page_message_ids
        if lower_bound < int(message_id) < high_exclusive
    )
    window_thread_ids = tuple(
        thread_id
        for thread_id in new_thread_ids
        if lower_bound < int(thread_id) < high_exclusive
    )
    evidence = {
        "schema_version": 1,
        "audit_kind": "discord-head-catchup-target-v1",
        "guild_id": guild_id,
        "target_id": target_id,
        "t_close": t_close,
        "t_close_source_sha256": _PREFLIGHT_SHA,
        "caught_through": caught,
        "high_exclusive": str(high_exclusive),
        "new_message_count": len(new_message_ids),
        "new_message_ids": list(new_message_ids),
        "new_thread_count": len(window_thread_ids),
        "new_thread_ids": list(window_thread_ids),
        "raw_pages": raw_descriptors,
    }
    descriptor = {
        "id": target_id,
        "caught_through": caught,
        "evidence_path": f"closure-evidence/{target_id}.json",
        "evidence_sha256": canonical_json_sha256(evidence),
        "new_message_count": len(new_message_ids),
        "new_message_ids": list(new_message_ids),
        "new_thread_count": len(window_thread_ids),
        "new_thread_ids": list(window_thread_ids),
    }
    return descriptor, evidence, raw_files


def _verified_head_target(
    descriptor: dict[str, object],
    evidence: dict[str, object],
    raw_files: dict[str, dict[str, object]],
) -> dict[str, object]:
    return {
        "payload": evidence,
        "file_sha256": descriptor["evidence_sha256"],
        "raw_pages": {
            path: {
                "payload": payload,
                "file_sha256": canonical_json_sha256(payload),
            }
            for path, payload in raw_files.items()
        },
    }


def _recommit_head_target(
    descriptor: dict[str, object],
    evidence: dict[str, object],
    raw_files: dict[str, dict[str, object]],
) -> dict[str, object]:
    message_ids: list[str] = []
    thread_ids: list[str] = []
    lower_bound = int(_snowflake_lower_bound(str(evidence["t_close"])))
    high_exclusive = int(str(evidence["high_exclusive"]))
    for raw_descriptor in evidence["raw_pages"]:
        raw_page = raw_files[raw_descriptor["path"]]
        request = raw_page["request"]
        response = raw_page["response"]
        raw_descriptor.update(
            {
                "sha256": canonical_json_sha256(raw_page),
                "request_sha256": canonical_json_sha256(request),
                "response_sha256": canonical_json_sha256(response),
            }
        )
        message_ids.extend(
            item["id"]
            for item in response["messages"]
            if lower_bound < int(item["id"]) < high_exclusive
        )
        thread_ids.extend(
            item["id"]
            for item in response["threads"]
            if lower_bound < int(item["id"]) < high_exclusive
        )
    for value in (descriptor, evidence):
        value["new_message_count"] = len(message_ids)
        value["new_message_ids"] = message_ids
        value["new_thread_count"] = len(thread_ids)
        value["new_thread_ids"] = thread_ids
    descriptor["evidence_sha256"] = canonical_json_sha256(evidence)
    return _verified_head_target(descriptor, evidence, raw_files)


def _write_asset_ledger_fixture(
    run_root: Path,
    media: dict[str, object],
    record_specs: list[dict[str, object]] | None = None,
    *,
    schema_version: int = 3,
) -> tuple[str, dict[str, dict[str, object]], str]:
    records: list[dict[str, object]] = []
    complete_count = int(media["complete"])
    warning_count = int(media.get("captured_with_warning", 0))
    reference_count = int(media.get("reference_only", 0))
    failed_count = int(media["failed"])
    record_count = int(media["records"])
    for index in range(record_count):
        message_id = str(3_000_000 + index)
        attachment_id = f"asset-{index}"
        logical_key = f"{message_id}:attachment:{attachment_id}"
        status = (
            "complete"
            if index < complete_count
            else "captured_with_warning"
            if index < complete_count + warning_count
            else "reference_only"
            if index < complete_count + warning_count + reference_count
            else "failed"
            if index < complete_count + warning_count + reference_count + failed_count
            else "in_progress"
        )
        is_reference = status == "reference_only"
        blob_bytes = f"fixture-blob-{index}".encode()
        blob_sha = (
            _sha256_bytes(blob_bytes)
            if status in {"complete", "captured_with_warning"}
            else None
        )
        blob_path = (
            f"assets/sha256/{blob_sha[:2]}/{blob_sha}.bin"
            if blob_sha is not None
            else None
        )
        if blob_path is not None:
            blob = run_root / blob_path
            blob.parent.mkdir(parents=True, exist_ok=True)
            blob.write_bytes(blob_bytes)
        source = {
            "message_id": message_id,
            "channel_id": "100",
            "stream": "messages_100",
        }
        declared = {
            "id": attachment_id,
            "filename": f"asset-{index}.bin",
            "size": len(blob_bytes),
        }
        if status == "captured_with_warning":
            declared["content_type"] = "application/json"
        url = (
            f"https://www.youtube.com/embed/Fixture{index}?si=opaque"
            if is_reference
            else f"https://cdn.example/{index}"
        )
        record = {
            "schema_version": schema_version,
            "logical_key": logical_key,
            "kind": "attachment",
            "field": "attachment",
            "url": url,
            "declared_metadata": declared,
            "declared_content_type": normalized_discord_media_mime(
                declared.get("content_type")
            ),
            "identity_metadata": discord_media_identity_metadata(
                "attachment",
                declared,
            ),
            "sources": [source],
            "observations": [
                {
                    "source": source,
                    "metadata": declared,
                    "url": url,
                    "proxy_url": None,
                }
            ],
            "identity_conflicts": [],
            "observed_urls": [url],
            "attempt_history": [],
            "status": status,
            "terminal_reason": (
                "downloaded"
                if status == "complete"
                else "mime_mismatch"
                if status == "captured_with_warning"
                else "candidate_urls_changed"
                if status == "in_progress"
                else "download_failed"
            ),
            "http_content_type": (
                "application/octet-stream" if blob_sha is not None else None
            ),
            "http_content_length": len(blob_bytes) if blob_sha is not None else None,
            "actual_bytes": len(blob_bytes) if blob_sha is not None else 0,
            "sha256": blob_sha,
            "blob_path": blob_path,
        }
        if schema_version == 3:
            record["candidate_urls"] = [url]
        elif schema_version == 2:
            record["identity_metadata"] = {
                "id": declared["id"],
                "filename": declared["filename"],
                "size": declared["size"],
                "content_type": normalized_discord_media_mime(
                    declared.get("content_type")
                ),
            }
        if is_reference:
            reference_metadata = {"url": url, "proxy_url": None}
            record.update(
                {
                    "logical_key": f"{message_id}:embed:0:video",
                    "kind": "embed",
                    "field": "video",
                    "declared_metadata": reference_metadata,
                    "declared_content_type": None,
                    "identity_metadata": {},
                    "observations": [
                        {
                            "source": source,
                            "metadata": reference_metadata,
                            "url": url,
                            "proxy_url": None,
                        }
                    ],
                    "attempt_history": [
                        {
                            "url": url,
                            "status": "failed",
                            "terminal_reason": "unsafe_media_url",
                            "http_content_type": None,
                            "http_content_length": None,
                            "actual_bytes": 0,
                            "sha256": None,
                            "blob_path": None,
                        }
                    ],
                    "terminal_reason": "youtube_embed_player_reference",
                    "http_content_type": None,
                    "http_content_length": None,
                    "actual_bytes": 0,
                    "sha256": None,
                    "blob_path": None,
                    "reference_provenance": {
                        "classification": "youtube_embed_player",
                        "classification_rule": (
                            "youtube_embed_player_url_rejected_by_media_policy_v1"
                        ),
                        "source_url": url,
                        "url_identity": {
                            "scheme": "https",
                            "host": "www.youtube.com",
                            "path": f"/embed/Fixture{index}",
                        },
                        "failed_attempt_number": 1,
                        "failed_attempt_status": "failed",
                        "failed_attempt_terminal_reason": "unsafe_media_url",
                        "proxy_candidate_present": False,
                        "binary_captured": False,
                    },
                }
            )
        if record_specs is not None:
            spec = record_specs[index]
            record.update(spec)
            metadata = record["declared_metadata"]
            assert isinstance(metadata, dict)
            if "declared_content_type" not in spec:
                record["declared_content_type"] = (
                    None
                    if record["kind"] == "sticker"
                    else normalized_discord_media_mime(metadata.get("content_type"))
                )
            if "identity_metadata" not in spec:
                record["identity_metadata"] = discord_media_identity_metadata(
                    record["kind"],
                    metadata,
                )
            if (
                record["status"] in {"complete", "captured_with_warning"}
                and "http_content_type" not in spec
            ):
                if record["kind"] in {"sticker", "emoji"} or (
                    record["kind"] == "embed"
                    and record["field"] in {
                        "image",
                        "thumbnail",
                        "author_icon",
                        "footer_icon",
                    }
                ):
                    record["http_content_type"] = "image/png"
                elif record["kind"] == "embed" and record["field"] == "video":
                    record["http_content_type"] = "video/mp4"
            if "observations" not in spec:
                record["observations"] = [
                    {
                        "source": source,
                        "metadata": record["declared_metadata"],
                        "url": record["url"],
                        "proxy_url": metadata.get("proxy_url"),
                    }
                ]
            if "observed_urls" not in spec:
                record["observed_urls"] = [record["url"]]
        if (
            record["status"] == "failed"
            and not record["attempt_history"]
            and record["terminal_reason"]
            not in {"logical_identity_conflict", "byte_transport_unavailable"}
        ):
            failure_detail = {
                "media_resolution_failed_transient": "resolver_timeout",
                "media_resolution_retry_exhausted": "resolver_timeout",
                "media_resolution_unresolved": "resolver_no_data",
                "media_resolution_invalid_answer": "resolver_invalid_answer",
            }.get(record["terminal_reason"])
            record["attempt_history"] = [
                {
                    key: record[key]
                    for key in (
                        "url",
                        "status",
                        "terminal_reason",
                        "http_content_type",
                        "http_content_length",
                        "actual_bytes",
                        "sha256",
                        "blob_path",
                    )
                }
            ]
            record["attempt_history"][0]["failure_detail"] = failure_detail
        if (
            record["status"] in {"complete", "captured_with_warning"}
            and (record_specs is None or "attempt_history" not in record_specs[index])
        ):
            record["attempt_history"] = [
                {
                    key: record[key]
                    for key in (
                        "url",
                        "status",
                        "terminal_reason",
                        "http_content_type",
                        "http_content_length",
                        "actual_bytes",
                        "sha256",
                        "blob_path",
                    )
                }
            ]
        records.append(record)

    ledger_rows: list[tuple[str, str, str]] = []
    for record in records:
        logical_key = str(record["logical_key"])
        record_name = hashlib.sha256(logical_key.encode()).hexdigest() + ".json"
        record_sha = _write_json(run_root / "asset-records" / record_name, record)
        ledger_rows.append((logical_key, record_name, record_sha))
    index_content = b"".join(
        _canonical_bytes(record)
        for record in sorted(records, key=lambda item: str(item["logical_key"]))
    )
    index_path = run_root / "asset-index.jsonl"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_bytes(index_content)
    index_sha = _sha256_bytes(index_content)

    ledger_path = run_root / "asset-ledger.sqlite3"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(sqlite3.connect(ledger_path)) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            "CREATE TABLE asset_records ("
            "logical_key TEXT PRIMARY KEY NOT NULL, "
            "record_name TEXT NOT NULL UNIQUE, "
            "committed_sha256 TEXT, pending_sha256 TEXT)"
        )
        connection.execute(
            "CREATE TABLE asset_metadata (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO asset_records VALUES (?, ?, ?, NULL)",
            ledger_rows,
        )
        connection.executemany(
            "INSERT INTO asset_metadata VALUES (?, ?)",
            (
                ("records_generation", str(len(records))),
                ("index_generation", str(len(records))),
                ("asset_index_sha256", index_sha),
            ),
        )
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    return (
        _sha256_bytes(ledger_path.read_bytes()),
        {str(record["logical_key"]): record for record in records},
        index_sha,
    )


def _snapshot(
    *,
    root_count: int = 123,
    thread_count: int = 9,
    forum_count: int = 1,
) -> dict[str, object]:
    roots = [
        {
            "id": _OXSUN_ROOT_ID if index == 0 else str(1_000_000 + index),
            "name": f"root-{index}",
            "kind": (
                "GUILD_FORUM (15)" if index < forum_count else "GUILD_TEXT (0)"
            ),
            "parent_id": "900000",
            "source_labels": [f"source-{index}"],
            "audit_metadata": {"ordinal": index},
        }
        for index in range(root_count)
    ]
    threads = [
        {
            "id": str(2_000_000 + index),
            "name": f"thread-{index}",
            "kind": "GUILD_PUBLIC_THREAD (11)",
            "parent_id": roots[index]["id"],
            "source_labels": [f"thread-source-{index}"],
            "audit_metadata": {"thread": True, "ordinal": index},
        }
        for index in range(thread_count)
    ]
    targets = roots + threads
    return {
        "schema_version": 7,
        "guild_id": "777777",
        "generated_at": "2026-07-19T01:02:03+00:00",
        "source": {"kind": "fixture", "note": "保留非 ASCII 审计元数据"},
        "target_count": len(targets),
        "target_set_sha256": _sha256_bytes(
            "\n".join(sorted((item["id"] for item in targets), key=int)).encode()
        ),
        "targets": targets,
        "pending_resolution": [],
    }


def _closure_snapshot(
    *targets: dict[str, object],
    guild_id: str = "1",
) -> dict[str, object]:
    target_ids = [str(target["id"]) for target in targets]
    return {
        "schema_version": 1,
        "guild_id": guild_id,
        "target_count": len(targets),
        "target_set_sha256": _sha256_bytes(
            "\n".join(sorted(target_ids, key=int)).encode("utf-8")
        ),
        "targets": list(targets),
    }


def _closure_merge(
    snapshot: dict[str, object],
    *,
    message_bearing_ids: tuple[str, ...],
    discovered_threads: tuple[tuple[str, str], ...] = (),
) -> dict[str, object]:
    static_ids = sorted(
        (str(item["id"]) for item in snapshot["targets"]),
        key=int,
    )
    discovered = [
        {"id": thread_id, "parent_id": parent_id, "owner_index": 1}
        for thread_id, parent_id in discovered_threads
    ]
    thread_parent_ids = sorted(
        (
            str(item["id"])
            for item in snapshot["targets"]
            if str(item.get("kind", "")).endswith(("(0)", "(5)", "(15)", "(16)"))
        ),
        key=int,
    )
    return {
        "schema_version": 1,
        "audit_kind": "discord-parent-family-merge-v1",
        "status": "complete",
        "guild_id": snapshot["guild_id"],
        "parent_snapshot_sha256": canonical_json_sha256(snapshot),
        "static_scope": {"exact_union": True, "pairwise_disjoint": True},
        "static_target_ids": static_ids,
        "message_bearing_static_target_ids": sorted(message_bearing_ids, key=int),
        "thread_parent_static_target_ids": thread_parent_ids,
        "required_head_catchup_target_ids": sorted(
            set(message_bearing_ids) | {item[0] for item in discovered_threads},
            key=int,
        ),
        "discovered_threads": discovered,
        "private_archived_blocked_streams": [],
        "private_archived_incomplete_streams": [],
        "non_private_incomplete_streams": [],
        "media_incomplete_shards": [],
        "validation_errors": [],
    }


class _CaptureRunner:
    def __init__(self) -> None:
        self.specs: list[OperationSpec] = []

    def run(self, spec: OperationSpec, *, approved: bool = False) -> OperationResult:
        del approved
        self.specs.append(spec)
        return OperationResult(
            operation_id=spec.operation_id,
            status=OperationStatus.SUCCEEDED,
            output={"ok": True},
        )


class ParentFamilyPlanningTests(unittest.TestCase):
    def test_four_shards_cover_all_132_targets_and_keep_threads_with_parents(self) -> None:
        snapshot = _snapshot()
        parent_sha = _sha256_bytes(_canonical_bytes(snapshot))
        parent_id_sha = snapshot["target_set_sha256"]
        self.assertNotEqual(parent_sha, parent_id_sha)

        plan, manifests = build_parent_family_plan(
            snapshot,
            shard_count=4,
            family_weights={
                _OXSUN_ROOT_ID: {
                    "weight": 292_055,
                    "source": "live-census-plus-old-file-2026-07-19",
                    "metrics": {
                        "estimated_messages": 292_055,
                        "estimated_unexported_messages": 289_120,
                        "estimated_media": None,
                        "estimated_declared_bytes": None,
                        "thread_count": 3,
                        "old_file_messages": 2_935,
                        "old_live_messages": 2_913,
                    },
                    "thread_message_estimates": [153_710, 135_410, 2_935],
                    "notes": "dynamic OXSUN threads remain one atomic family",
                }
            },
        )

        self.assertEqual(SHARD_SCHEME, "parent-family-v1")
        self.assertEqual(plan["parent_snapshot_sha256"], parent_sha)
        self.assertEqual(plan["parent_target_set_sha256"], parent_id_sha)
        self.assertEqual(plan["count"], 4)
        self.assertEqual(plan["parent_family_count"], 123)
        self.assertEqual(len(manifests), 4)
        self.assertEqual(sum(shard["family_count"] for shard in manifests), 123)

        target_sets = [{item["id"] for item in shard["targets"]} for shard in manifests]
        self.assertEqual(set().union(*target_sets), {item["id"] for item in snapshot["targets"]})
        self.assertEqual(sum(map(len, target_sets)), 132)
        for left_index, left in enumerate(target_sets):
            for right in target_sets[left_index + 1 :]:
                self.assertFalse(left & right)

        owner = {
            target_id: shard["index"]
            for shard, target_ids in zip(manifests, target_sets, strict=True)
            for target_id in target_ids
        }
        for thread in snapshot["targets"][-9:]:
            self.assertEqual(owner[thread["id"]], owner[thread["parent_id"]])

        heavy = next(
            shard for shard in manifests if _OXSUN_ROOT_ID in shard["family_root_ids"]
        )
        self.assertEqual(heavy["family_estimated_weights"][_OXSUN_ROOT_ID], 292_055)
        heavy_detail = heavy["family_weight_details"][_OXSUN_ROOT_ID]
        self.assertEqual(
            heavy_detail["source"],
            "live-census-plus-old-file-2026-07-19",
        )
        self.assertEqual(heavy_detail["metrics"]["estimated_messages"], 292_055)
        self.assertEqual(
            heavy_detail["metrics"]["estimated_unexported_messages"],
            289_120,
        )
        self.assertIsNone(heavy_detail["metrics"]["estimated_media"])
        self.assertEqual(
            heavy_detail["input"]["thread_message_estimates"],
            [153_710, 135_410, 2_935],
        )
        self.assertEqual(
            heavy_detail["input"]["notes"],
            "dynamic OXSUN threads remain one atomic family",
        )
        self.assertEqual(
            {item["id"] for item in heavy["targets"]}
            & {_OXSUN_ROOT_ID, "2000000"},
            {_OXSUN_ROOT_ID, "2000000"},
        )
        for shard in manifests:
            self.assertEqual(shard["schema_version"], 1)
            self.assertEqual(shard["shard_scheme"], SHARD_SCHEME)
            self.assertEqual(shard["count"], 4)
            self.assertEqual(shard["parent_snapshot_sha256"], parent_sha)
            self.assertEqual(shard["parent_target_set_sha256"], parent_id_sha)
            self.assertEqual(
                shard["target_set_sha256"],
                target_set_sha256(item["id"] for item in shard["targets"]),
            )
            self.assertEqual(shard["target_count"], len(shard["targets"]))
            self.assertEqual(
                shard["estimated_weight"],
                sum(shard["family_estimated_weights"].values()),
            )
        copied_thread = next(
            item for shard in manifests for item in shard["targets"] if item["id"] == "2000000"
        )
        self.assertEqual(copied_thread, snapshot["targets"][-9])

    def test_parent_hash_changes_when_metadata_changes_but_id_hash_does_not(self) -> None:
        original = _snapshot(root_count=4, thread_count=1)
        changed = json.loads(json.dumps(original))
        changed["source"]["note"] = "different audit metadata"

        self.assertEqual(
            target_set_sha256(item["id"] for item in original["targets"]),
            target_set_sha256(item["id"] for item in changed["targets"]),
        )
        self.assertNotEqual(canonical_json_sha256(original), canonical_json_sha256(changed))

    def test_zero_weight_families_still_populate_every_requested_shard(self) -> None:
        snapshot = _snapshot(root_count=4, thread_count=0)
        weights = {target["id"]: 0 for target in snapshot["targets"]}
        _, manifests = build_parent_family_plan(
            snapshot,
            shard_count=4,
            family_weights=weights,
        )
        self.assertEqual([manifest["target_count"] for manifest in manifests], [1, 1, 1, 1])

    def test_rejects_declared_scope_drift_and_orphan_explicit_thread(self) -> None:
        wrong_count = _snapshot(root_count=4, thread_count=1)
        wrong_count["target_count"] = 999
        with self.assertRaisesRegex(ValueError, "target_count"):
            build_parent_family_plan(wrong_count, shard_count=2)

        wrong_hash = _snapshot(root_count=4, thread_count=1)
        wrong_hash["target_set_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "target_set_sha256"):
            build_parent_family_plan(wrong_hash, shard_count=2)

        orphan = _snapshot(root_count=4, thread_count=1)
        orphan["targets"][-1]["parent_id"] = "888888"
        orphan["target_set_sha256"] = target_set_sha256(
            item["id"] for item in orphan["targets"]
        )
        with self.assertRaisesRegex(ValueError, "thread parent"):
            build_parent_family_plan(orphan, shard_count=2)

    def test_writer_is_deterministic_atomic_and_symlink_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            snapshot = _snapshot(root_count=4, thread_count=1)
            _write_json(workspace / "inputs/targets.json", snapshot)
            _write_json(
                workspace / "inputs/weights.json",
                {"schema_version": 1, "family_weights": {_OXSUN_ROOT_ID: 50}},
            )

            first = write_parent_family_plan(
                workspace=workspace,
                targets_path="inputs/targets.json",
                output_dir="plans/four",
                shard_count=2,
                weights_path="inputs/weights.json",
            )
            second = write_parent_family_plan(
                workspace=workspace,
                targets_path="inputs/targets.json",
                output_dir="plans/four",
                shard_count=2,
                weights_path="inputs/weights.json",
            )
            self.assertEqual(first, second)
            self.assertEqual(first["plan_path"], "plans/four/plan.json")
            self.assertTrue((workspace / first["plan_path"]).read_bytes().endswith(b"\n"))
            self.assertFalse(list((workspace / "plans/four").glob(".*.tmp")))

            (workspace / "escape").symlink_to(workspace / "inputs", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                write_parent_family_plan(
                    workspace=workspace,
                    targets_path="escape/targets.json",
                    output_dir="plans/rejected",
                    shard_count=2,
                )
            with self.assertRaisesRegex(ValueError, "contained relative"):
                write_parent_family_plan(
                    workspace=workspace,
                    targets_path="inputs/targets.json",
                    output_dir="../outside",
                    shard_count=2,
                )


class MergeAndClosureAuditTests(unittest.TestCase):
    def test_merge_accepts_current_schema4_positive_asset_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(
                workspace,
                media_overrides={
                    1: {
                        "status": "complete",
                        "records": 1,
                        "complete": 1,
                        "reference_only": 0,
                        "binary_captured": 1,
                        "failed": 0,
                    }
                },
                asset_schema_versions={1: 4},
                asset_record_overrides={
                    1: [
                        {
                            "url": "https://cdn.example/schema4-positive.bin",
                            "candidate_urls": [
                                "https://cdn.example/schema4-positive.bin"
                            ],
                        }
                    ]
                },
            )

            audit = self._write_merge(
                workspace,
                "audits/schema4-positive.json",
            )

            self.assertEqual(
                audit["status"],
                "complete",
                audit["validation_errors"],
            )
            self.assertEqual(audit["validation_errors"], [])

    def test_merge_rebuilds_zero_byte_reclassification_as_nonbinary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            direct_url = "https://cdn.discordapp.com/attachments/1/2/empty.png"
            proxy_url = "https://media.discordapp.net/external/empty.png"
            empty_sha = _sha256_bytes(b"")
            empty_path = f"assets/sha256/{empty_sha[:2]}/{empty_sha}.bin"
            source_ref = {
                "message_id": "3000000",
                "channel_id": "100",
                "stream": "messages_100",
            }
            metadata = {
                "id": "asset-0",
                "filename": "empty.png",
                "size": 0,
                "content_type": "image/png",
                "url": direct_url,
                "proxy_url": proxy_url,
            }
            zero_attempt = {
                "url": direct_url,
                "status": "complete",
                "terminal_reason": "downloaded",
                "http_content_type": "image/png",
                "http_content_length": 0,
                "actual_bytes": 0,
                "sha256": empty_sha,
                "blob_path": empty_path,
            }
            source_record = {
                "schema_version": 3,
                "logical_key": "3000000:attachment:asset-0",
                "kind": "attachment",
                "field": "attachment",
                "url": direct_url,
                "candidate_urls": [direct_url, proxy_url],
                "declared_metadata": metadata,
                "declared_content_type": "image/png",
                "identity_metadata": {
                    "id": "asset-0",
                    "size": 0,
                    "content_type": "image/png",
                },
                "sources": [source_ref],
                "observations": [
                    {
                        "source": source_ref,
                        "metadata": metadata,
                        "url": direct_url,
                        "proxy_url": proxy_url,
                    }
                ],
                "identity_conflicts": [],
                "observed_urls": [direct_url, proxy_url],
                "attempt_history": [zero_attempt],
                **{
                    key: deepcopy(zero_attempt[key])
                    for key in (
                        "status",
                        "terminal_reason",
                        "http_content_type",
                        "http_content_length",
                        "actual_bytes",
                        "sha256",
                        "blob_path",
                    )
                },
            }
            migrated, changed = migrate_legacy_media_record(
                source_record,
                source_record_sha256=_sha256_bytes(
                    _canonical_bytes(source_record)
                ),
                verified_empty_blob=True,
            )
            self.assertTrue(changed)
            positive = b"fixed"
            positive_sha = _sha256_bytes(positive)
            positive_path = (
                f"assets/sha256/{positive_sha[:2]}/{positive_sha}.png"
            )
            positive_attempt = {
                "url": direct_url,
                "status": "captured_with_warning",
                "terminal_reason": "declared_size_mismatch",
                "http_content_type": "image/png",
                "http_content_length": len(positive),
                "actual_bytes": len(positive),
                "sha256": positive_sha,
                "blob_path": positive_path,
            }
            migrated["attempt_history"].append(positive_attempt)
            for key, value in positive_attempt.items():
                migrated[key] = deepcopy(value)

            self._build_run_fixture(
                workspace,
                media_overrides={
                    1: {
                        "status": "complete_with_warnings",
                        "records": 1,
                        "complete": 0,
                        "captured_with_warning": 1,
                        "reference_only": 0,
                        "binary_captured": 1,
                        "failed": 0,
                    }
                },
                asset_record_overrides={1: [migrated]},
            )
            run_root = workspace / "runs/shard-1"
            for relative, content in (
                (empty_path, b""),
                (positive_path, positive),
            ):
                blob = run_root / relative
                blob.parent.mkdir(parents=True, exist_ok=True)
                blob.write_bytes(content)

            audit = self._write_merge(
                workspace,
                "audits/zero-reclassified.json",
            )

            self.assertEqual(
                audit["status"],
                "complete",
                audit["validation_errors"],
            )
            recovery = audit["transitive_evidence"]["1"][
                "media_recovery_audit"
            ]
            self.assertTrue(recovery["verified"])
            self.assertEqual(
                recovery["counts"][
                    "legacy_zero_byte_reclassification_rows"
                ],
                1,
            )

    def test_request_asset_limit_defaults_only_for_legacy_v1(self) -> None:
        self.assertEqual(
            discord_sharding_module._request_max_asset_bytes(
                {
                    "version": 1,
                    "options": {"max_pages": None, "download_assets": True},
                }
            ),
            512 * 1024 * 1024,
        )
        self.assertEqual(
            discord_sharding_module._request_max_asset_bytes(
                {"version": 2, "options": {"max_asset_bytes": 7}}
            ),
            7,
        )
        invalid = (
            {"version": 2, "options": {}},
            {"version": 1, "options": {"max_asset_bytes": True}},
            {"version": 1, "options": {"max_asset_bytes": 0}},
            {"version": 1, "options": {"max_asset_bytes": -1}},
        )
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ValueError):
                discord_sharding_module._request_max_asset_bytes(request)

    def _build_run_fixture(
        self,
        workspace: Path,
        *,
        snapshot: dict[str, object] | None = None,
        stream_overrides: dict[int, dict[str, dict[str, object]]] | None = None,
        media_overrides: dict[int, dict[str, object]] | None = None,
        asset_record_overrides: dict[int, list[dict[str, object]]] | None = None,
        asset_schema_versions: dict[int, int] | None = None,
        request_option_overrides: dict[int, dict[str, object]] | None = None,
        request_versions: dict[int, int] | None = None,
        legacy_default_schema_shards: set[int] | None = None,
        message_evidence_schema_versions: dict[int, int] | None = None,
        mixed_message_evidence_schema_shards: set[int] | None = None,
        allowed_message_warning_shards: set[int] | None = None,
        non_message_raw_page_shards: set[int] | None = None,
        manifest_status_overrides: dict[int, str] | None = None,
        populated_v2_evidence: bool = False,
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        snapshot = snapshot or _snapshot(root_count=4, thread_count=1)
        _write_json(workspace / "inputs/targets.json", snapshot)
        write_parent_family_plan(
            workspace=workspace,
            targets_path="inputs/targets.json",
            output_dir="plans/two",
            shard_count=2,
        )
        plan_path = workspace / "plans/two/plan.json"
        plan = json.loads(plan_path.read_text())
        merge_request: dict[str, object] = {
            "schema_version": 1,
            "shard_scheme": SHARD_SCHEME,
            "parent_snapshot_sha256": canonical_json_sha256(snapshot),
            "plan_sha256": _sha256_bytes(plan_path.read_bytes()),
            "shards": [],
        }
        stream_overrides = stream_overrides or {}
        media_overrides = media_overrides or {}
        asset_record_overrides = asset_record_overrides or {}
        asset_schema_versions = asset_schema_versions or {}
        request_option_overrides = request_option_overrides or {}
        request_versions = request_versions or {}
        legacy_default_schema_shards = legacy_default_schema_shards or set()
        message_evidence_schema_versions = message_evidence_schema_versions or {}
        mixed_message_evidence_schema_shards = (
            mixed_message_evidence_schema_shards or set()
        )
        allowed_message_warning_shards = allowed_message_warning_shards or set()
        non_message_raw_page_shards = non_message_raw_page_shards or set()
        manifest_status_overrides = manifest_status_overrides or {}

        for shard_entry in plan["shards"]:
            index = shard_entry["index"]
            shard_manifest_path = workspace / "plans/two" / shard_entry["manifest_file"]
            shard_snapshot = json.loads(shard_manifest_path.read_text())
            run_root = workspace / f"runs/shard-{index}"
            run_id = f"shard-{index}"
            request_version = request_versions.get(index, 1)
            request: dict[str, object] = {
                "version": request_version,
                "run_id": run_id,
                "target_snapshot": shard_snapshot,
                "target_sha256": canonical_json_sha256(shard_snapshot),
                "options": {
                    "max_pages": None,
                    "download_assets": True,
                    **(
                        {"message_evidence_schema_version": 1}
                        if request_version == 1
                        and index not in legacy_default_schema_shards
                        else {}
                    ),
                },
            }
            if request_version == 2:
                request.update(
                    {
                        "identity": {
                            "bot_principal_id": "9",
                            "api_origin": "https://discord.com",
                        },
                        "schema": {"message_evidence_version": 2},
                        "telemetry": {"initial_asset_chunk_size": 65536},
                    }
                )
                options = request["options"]
                assert isinstance(options, dict)
                options["max_asset_bytes"] = 512 * 1024 * 1024
            request["options"].update(request_option_overrides.get(index, {}))
            streams: dict[str, dict[str, object]] = {
                "inventory_targets": {
                    "status": "complete",
                    "terminal_reason": "inventory_saved",
                },
            }
            message_evidence_pages = 0
            message_evidence_totals: Counter[str] = Counter()
            for target in shard_snapshot["targets"]:
                if target["kind"] == "GUILD_FORUM (15)":
                    continue
                for prefix in ("messages", "pins"):
                    stream_name = f"{prefix}_{target['id']}"
                    evidence_version = (
                        2
                        if (
                            index in mixed_message_evidence_schema_shards
                            and prefix == "pins"
                        )
                        else message_evidence_schema_versions.get(index, 1)
                    )
                    message = _complete_message("30", str(target["id"]))
                    populated = populated_v2_evidence and evidence_version == 2
                    if (
                        populated
                        and index in allowed_message_warning_shards
                        and prefix == "messages"
                    ):
                        root_timestamp = datetime.fromisoformat(str(message["timestamp"]))
                        message["message_reference"] = {
                            "type": 1,
                            "message_id": str(message["id"]),
                            "channel_id": str(target["id"]),
                        }
                        message["message_snapshots"] = [
                            {
                                "message": {
                                    "type": 0,
                                    "content": "immutable snapshot",
                                    "timestamp": (
                                        root_timestamp + timedelta(minutes=2)
                                    ).isoformat(),
                                    "edited_timestamp": None,
                                    "attachments": [],
                                    "embeds": [],
                                    "components": [],
                                }
                            }
                        ]
                    pinned_at = "2026-07-20T08:00:00+08:00"
                    payload: object = (
                        [message]
                        if prefix == "messages" and populated
                        else {
                            "items": [
                                {"pinned_at": pinned_at, "message": message}
                            ]
                        }
                        if prefix == "pins" and populated
                        else []
                        if prefix == "messages"
                        else {"items": []}
                    )
                    fetched_at = "2026-07-20T00:00:01+00:00"
                    page_hash = _write_json(
                        run_root / f"pages/{stream_name}/000001.json",
                        {
                            "request": {
                                "path": f"/channels/{target['id']}/{prefix}"
                            },
                            "payload": payload,
                            "acquisition": {
                                "fetched_at": fetched_at,
                                "source": "collector_local_clock_after_response",
                            },
                            "pagination": {
                                "item_count": 1 if populated else 0,
                                "next_cursor": None,
                                "terminal_status": "complete",
                            },
                        },
                    )
                    evidence_path = f"message-evidence/{stream_name}/000001.jsonl"
                    evidence_file = run_root / evidence_path
                    evidence_file.parent.mkdir(parents=True, exist_ok=True)
                    rows: list[dict[str, object]] = []
                    if populated:
                        pointer = (
                            "/payload/0"
                            if prefix == "messages"
                            else "/payload/items/0/message"
                        )
                        evidence = asdict(
                            extract_message_evidence(
                                message,
                                stream=stream_name,
                                evidence_path=f"pages/{stream_name}/000001.json",
                                evidence_sha256=page_hash,
                                json_pointer=pointer,
                            )
                        )
                        row: dict[str, object] = {
                            "schema_version": 2,
                            "stream": stream_name,
                            "channel_id": str(target["id"]),
                            "page_number": 1,
                            "message_json_pointer": pointer,
                            **evidence,
                        }
                        if prefix == "pins":
                            pinned_at_utc = "2026-07-20T00:00:00+00:00"
                            row["pin_event"] = {
                                "event_key": (
                                    f"pin_event:{target['id']}:30:{pinned_at_utc}"
                                ),
                                "channel_id": str(target["id"]),
                                "message_id": "30",
                                "pinned_at": pinned_at,
                                "pinned_at_utc": pinned_at_utc,
                                "json_pointer": "/payload/items/0",
                            }
                        rows.append(row)
                    evidence_content = b"".join(
                        _canonical_bytes(row) for row in rows
                    )
                    evidence_file.write_bytes(evidence_content)
                    diagnostics_by_severity = {
                        "error": 0,
                        "warning": 0,
                        "info": 0,
                    }
                    for row in rows:
                        diagnostics = row.get("diagnostics", [])
                        assert isinstance(diagnostics, (list, tuple))
                        for diagnostic in diagnostics:
                            assert isinstance(diagnostic, dict)
                            severity = str(diagnostic["severity"])
                            diagnostics_by_severity[severity] += 1
                            message_evidence_totals[
                                f"diagnostic_code:{severity}:{diagnostic['code']}"
                            ] += 1
                    page_totals = {
                        "root_messages": len(rows),
                        "partial_messages": sum(
                            row.get("status") == "partial" for row in rows
                        ),
                        "nodes": sum(len(row.get("nodes", [])) for row in rows),
                        "media_occurrences": sum(
                            len(row.get("media", [])) for row in rows
                        ),
                        "references": sum(
                            len(row.get("references", [])) for row in rows
                        ),
                        "diagnostics": sum(
                            len(row.get("diagnostics", [])) for row in rows
                        ),
                    }
                    message_evidence_totals.update(page_totals)
                    for severity, count in diagnostics_by_severity.items():
                        message_evidence_totals[f"diagnostics_{severity}"] += count
                    descriptor: dict[str, object] = {
                        "schema_version": evidence_version,
                        "path": evidence_path,
                        "sha256": _sha256_bytes(evidence_content),
                        "raw_page_path": f"pages/{stream_name}/000001.json",
                        "raw_page_sha256": page_hash,
                        **page_totals,
                    }
                    if evidence_version == 2:
                        descriptor.update(
                            {
                                "stream": stream_name,
                                "channel_id": str(target["id"]),
                                "page_number": 1,
                                "fetched_at": fetched_at,
                                "diagnostics_by_severity": diagnostics_by_severity,
                                "pin_events": sum(
                                    "pin_event" in row for row in rows
                                ),
                            }
                        )
                    streams[stream_name] = {
                        "status": "complete",
                        "pages": 1,
                        "processed_pages": 1,
                        "page_hashes": [page_hash],
                        "page_states": [
                            {
                                "processing_status": "processed",
                                "message_evidence": descriptor,
                            },
                        ],
                        "terminal_reason": "empty_page",
                    }
                    message_evidence_pages += 1
            if index in non_message_raw_page_shards:
                stream_name = f"threads_{shard_snapshot['guild_id']}_active"
                page_hash = _write_json(
                    run_root / f"pages/{stream_name}/000001.json",
                    {
                        "request": {"path": "/guilds/1/threads/active"},
                        "payload": {"threads": [], "members": []},
                        "pagination": {
                            "item_count": 0,
                            "next_cursor": None,
                            "terminal_status": "complete",
                        },
                    },
                )
                streams[stream_name] = {
                    "status": "complete",
                    "pages": 1,
                    "processed_pages": 1,
                    "page_hashes": [page_hash],
                    "page_states": [{"processing_status": "processed"}],
                    "terminal_reason": "empty_page",
                }
            streams.update(stream_overrides.get(index, {}))
            checkpoint = {
                "version": 1,
                "run_id": run_id,
                "streams": streams,
                "assets": {},
                "asset_ledger": {"backend": "sqlite", "version": 1},
                "errors": [],
            }
            media = {
                "status": "complete",
                "records": 0,
                "complete": 0,
                "captured_with_warning": 0,
                "reference_only": 0,
                "binary_captured": 0,
                "failed": 0,
            }
            media.update(media_overrides.get(index, {}))
            asset_ledger_sha, asset_records, asset_index_sha = _write_asset_ledger_fixture(
                run_root,
                media,
                asset_record_overrides.get(index),
                schema_version=asset_schema_versions.get(index, 3),
            )
            request_sha = _write_json(run_root / "request.json", request)
            checkpoint["request_sha256"] = request_sha
            resolution_context = media_resolution_context(request, request_sha)
            media_recovery_audit = build_media_recovery_audit(
                run_id=run_id,
                request_sha256=request_sha,
                policy_inputs_sha256=resolution_context.policy_inputs_sha256,
                asset_index_sha256=asset_index_sha,
                records=asset_records,
            )
            media_recovery_audit_content = canonical_media_recovery_audit_bytes(
                media_recovery_audit
            )
            (run_root / MEDIA_RECOVERY_AUDIT_FILENAME).write_bytes(
                media_recovery_audit_content
            )
            reference_resolution_audit = build_message_reference_resolution_audit(
                run_root=run_root,
                checkpoint=checkpoint,
                run_id=run_id,
                request_sha256=request_sha,
            )
            reference_resolution_descriptor = (
                publish_message_reference_resolution_audit(
                    run_root=run_root,
                    audit=reference_resolution_audit,
                )
            )
            diagnostic_codes_by_severity = {
                severity: {
                    key.removeprefix(f"diagnostic_code:{severity}:"): count
                    for key, count in message_evidence_totals.items()
                    if key.startswith(f"diagnostic_code:{severity}:")
                }
                for severity in ("error", "warning", "info")
            }
            message_evidence_complete = (
                message_evidence_totals["partial_messages"] == 0
                and message_evidence_totals["diagnostics_error"] == 0
            )
            message_evidence_effective_status = (
                "partial"
                if not message_evidence_complete
                else "complete_with_warnings"
                if message_evidence_totals["diagnostics_warning"]
                else "complete"
            )
            message_evidence_status = (
                "not_applicable"
                if message_evidence_pages == 0
                else message_evidence_effective_status
            )
            streams_complete = all(
                item["status"] == "complete" for item in streams.values()
            )
            media_complete = media["status"] in {
                "complete",
                "complete_with_warnings",
            }
            manifest = {
                "version": 1,
                "run_id": run_id,
                "status": manifest_status_overrides.get(
                    index,
                    (
                        "complete_with_warnings"
                        if streams_complete
                        and media_complete
                        and message_evidence_complete
                        and (
                            media["status"] == "complete_with_warnings"
                            or message_evidence_effective_status
                            == "complete_with_warnings"
                        )
                        else "complete"
                        if streams_complete
                        and media_complete
                        and message_evidence_complete
                        else "partial"
                    ),
                ),
                "streams": streams,
                "media": media,
                "media_recovery_audit": {
                    "version": MEDIA_RECOVERY_AUDIT_VERSION,
                    "path": MEDIA_RECOVERY_AUDIT_FILENAME,
                    "sha256": _sha256_bytes(media_recovery_audit_content),
                    "counts": media_recovery_audit["counts"],
                },
                "message_reference_resolution_audit": (
                    reference_resolution_descriptor
                ),
                "message_evidence": {
                    "status": message_evidence_status,
                    "effective_status": message_evidence_effective_status,
                    "pages": message_evidence_pages,
                    "expected_pages": message_evidence_pages,
                    "root_messages": message_evidence_totals["root_messages"],
                    "partial_messages": message_evidence_totals["partial_messages"],
                    "nodes": message_evidence_totals["nodes"],
                    "media_occurrences": message_evidence_totals["media_occurrences"],
                    "references": message_evidence_totals["references"],
                    "diagnostics": message_evidence_totals["diagnostics"],
                    "diagnostics_by_severity": {
                        "error": message_evidence_totals["diagnostics_error"],
                        "warning": message_evidence_totals["diagnostics_warning"],
                        "info": message_evidence_totals["diagnostics_info"],
                    },
                    "diagnostic_codes_by_severity": diagnostic_codes_by_severity,
                    "effective_partial_messages": message_evidence_totals[
                        "partial_messages"
                    ],
                    "effective_diagnostics_by_severity": {
                        "error": message_evidence_totals["diagnostics_error"],
                        "warning": message_evidence_totals["diagnostics_warning"],
                        "info": message_evidence_totals["diagnostics_info"],
                    },
                    "effective_diagnostic_codes_by_severity": (
                        diagnostic_codes_by_severity
                    ),
                },
                "errors": 0,
                "not_api_exposed": ["discord_go_live", "personal_favorites"],
            }
            explicit_threads = [
                item
                for item in shard_snapshot["targets"]
                if "THREAD" in item["kind"].upper()
            ]
            inventory = {
                "targets": [
                    {"requested": item, "metadata": {"id": item["id"]}}
                    for item in shard_snapshot["targets"]
                ],
                "threads": [
                    {**item, "sources": ["explicit_target"]}
                    for item in explicit_threads
                ],
            }
            manifest_sha = _write_json(run_root / "manifest.json", manifest)
            checkpoint_sha = _write_json(run_root / "checkpoint.json", checkpoint)
            inventory_sha = _write_json(run_root / "inventory/targets.json", inventory)
            merge_request["shards"].append(
                {
                    "index": index,
                    "run_root": f"runs/shard-{index}",
                    "request_sha256": request_sha,
                    "manifest_sha256": manifest_sha,
                    "checkpoint_sha256": checkpoint_sha,
                    "targets_inventory_sha256": inventory_sha,
                    "asset_ledger_sha256": asset_ledger_sha,
                }
            )
        _write_json(workspace / "inputs/merge-request.json", merge_request)
        return snapshot, plan, merge_request

    def _write_invalid_merge_request(self, workspace: Path) -> None:
        _, _, merge_request = self._build_run_fixture(workspace)
        merge_request["parent_snapshot_sha256"] = "0" * 64
        _write_json(workspace / "inputs/invalid-merge-request.json", merge_request)

    def _write_attacker_synced_media_tamper(
        self,
        workspace: Path,
        merge_request: dict[str, object],
        *,
        mutate_audit: object | None = None,
        mutate_descriptor: object | None = None,
        noncanonical_bytes: bool = False,
    ) -> None:
        shard = merge_request["shards"][0]
        assert isinstance(shard, dict)
        run_root = workspace / str(shard["run_root"])
        audit_path = run_root / MEDIA_RECOVERY_AUDIT_FILENAME
        audit = json.loads(audit_path.read_text())
        if callable(mutate_audit):
            mutate_audit(audit)
        audit_content = (
            json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=False).encode("utf-8")
            + b"\n"
            if noncanonical_bytes
            else canonical_media_recovery_audit_bytes(audit)
        )
        audit_path.write_bytes(audit_content)

        manifest_path = run_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        descriptor = manifest["media_recovery_audit"]
        descriptor["sha256"] = _sha256_bytes(audit_content)
        descriptor["counts"] = audit["counts"]
        if callable(mutate_descriptor):
            mutate_descriptor(descriptor)
        shard["manifest_sha256"] = _write_json(manifest_path, manifest)
        _write_json(workspace / "inputs/merge-request.json", merge_request)

    def _write_attacker_synced_asset_source_tamper(
        self,
        workspace: Path,
        merge_request: dict[str, object],
        *,
        case: str,
    ) -> None:
        shard = merge_request["shards"][0]
        assert isinstance(shard, dict)
        run_root = workspace / str(shard["run_root"])
        record_path = next((run_root / "asset-records").glob("*.json"))
        record = json.loads(record_path.read_text())
        logical_key = str(record["logical_key"])
        records_for_audit: dict[str, dict[str, object]] = {logical_key: record}

        if case == "actual_index_mismatch":
            index_content = _canonical_bytes({"attacker": "index-rebind"})
        elif case == "record_hash_mismatch":
            record_path.write_bytes(_canonical_bytes({"attacker": "record-rebind"}))
            records_for_audit = {}
            index_content = b""
        elif case == "record_parse_failure":
            record_content = b"not-json\n"
            record_path.write_bytes(record_content)
            records_for_audit = {}
            index_content = b""
        else:
            raise AssertionError(f"unknown source tamper case: {case}")

        index_path = run_root / "asset-index.jsonl"
        index_path.write_bytes(index_content)
        index_sha = _sha256_bytes(index_content)
        ledger_path = run_root / "asset-ledger.sqlite3"
        with contextlib.closing(sqlite3.connect(ledger_path)) as connection:
            if case == "record_parse_failure":
                connection.execute(
                    "UPDATE asset_records SET committed_sha256 = ? WHERE logical_key = ?",
                    (_sha256_bytes(record_path.read_bytes()), logical_key),
                )
            connection.execute(
                "UPDATE asset_metadata SET value = ? WHERE key = 'asset_index_sha256'",
                (index_sha,),
            )
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()

        shard["asset_ledger_sha256"] = _sha256_bytes(ledger_path.read_bytes())
        request_path = run_root / "request.json"
        request = json.loads(request_path.read_text())
        request_sha = _sha256_bytes(request_path.read_bytes())
        context = media_resolution_context(request, request_sha)
        recovery_audit = build_media_recovery_audit(
            run_id=str(request["run_id"]),
            request_sha256=request_sha,
            policy_inputs_sha256=context.policy_inputs_sha256,
            asset_index_sha256=index_sha,
            records=records_for_audit,
        )
        recovery_content = canonical_media_recovery_audit_bytes(recovery_audit)
        (run_root / MEDIA_RECOVERY_AUDIT_FILENAME).write_bytes(recovery_content)

        manifest_path = run_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["media_recovery_audit"] = {
            "version": MEDIA_RECOVERY_AUDIT_VERSION,
            "path": MEDIA_RECOVERY_AUDIT_FILENAME,
            "sha256": _sha256_bytes(recovery_content),
            "counts": recovery_audit["counts"],
        }
        shard["manifest_sha256"] = _write_json(manifest_path, manifest)
        _write_json(workspace / "inputs/merge-request.json", merge_request)

    def _write_attacker_synced_candidate_urls_tamper(
        self,
        workspace: Path,
        merge_request: dict[str, object],
        *,
        candidate_urls: object,
    ) -> None:
        shard = merge_request["shards"][0]
        assert isinstance(shard, dict)
        run_root = workspace / str(shard["run_root"])
        record_path = next((run_root / "asset-records").glob("*.json"))
        record = json.loads(record_path.read_text())
        record["candidate_urls"] = candidate_urls
        record_content = _canonical_bytes(record)
        record_path.write_bytes(record_content)
        record_sha = _sha256_bytes(record_content)

        index_content = _canonical_bytes(record)
        index_path = run_root / "asset-index.jsonl"
        index_path.write_bytes(index_content)
        index_sha = _sha256_bytes(index_content)
        ledger_path = run_root / "asset-ledger.sqlite3"
        with contextlib.closing(sqlite3.connect(ledger_path)) as connection:
            connection.execute(
                "UPDATE asset_records SET committed_sha256 = ? "
                "WHERE logical_key = ?",
                (record_sha, record["logical_key"]),
            )
            connection.execute(
                "UPDATE asset_metadata SET value = ? "
                "WHERE key = 'asset_index_sha256'",
                (index_sha,),
            )
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        shard["asset_ledger_sha256"] = _sha256_bytes(ledger_path.read_bytes())

        audit_path = run_root / MEDIA_RECOVERY_AUDIT_FILENAME
        recovery_audit = json.loads(audit_path.read_text())
        recovery_audit["asset_index_sha256"] = index_sha
        recovery_content = canonical_media_recovery_audit_bytes(recovery_audit)
        audit_path.write_bytes(recovery_content)

        manifest_path = run_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["media_recovery_audit"]["sha256"] = _sha256_bytes(
            recovery_content
        )
        shard["manifest_sha256"] = _write_json(manifest_path, manifest)
        _write_json(workspace / "inputs/merge-request.json", merge_request)

    def _write_attacker_synced_success_tail_removal(
        self,
        workspace: Path,
        merge_request: dict[str, object],
    ) -> None:
        shard = merge_request["shards"][0]
        assert isinstance(shard, dict)
        run_root = workspace / str(shard["run_root"])
        record_path = next((run_root / "asset-records").glob("*.json"))
        record = json.loads(record_path.read_text())
        attempts = record["attempt_history"]
        assert attempts[-1]["status"] == "complete"
        attempts.pop()
        assert attempts[-1]["terminal_reason"] == "download_http_404"

        record_content = _canonical_bytes(record)
        record_path.write_bytes(record_content)
        record_sha = _sha256_bytes(record_content)
        index_path = run_root / "asset-index.jsonl"
        index_path.write_bytes(record_content)
        index_sha = _sha256_bytes(record_content)

        ledger_path = run_root / "asset-ledger.sqlite3"
        with contextlib.closing(sqlite3.connect(ledger_path)) as connection:
            connection.execute(
                "UPDATE asset_records SET committed_sha256 = ?, pending_sha256 = NULL "
                "WHERE logical_key = ?",
                (record_sha, record["logical_key"]),
            )
            connection.execute(
                "UPDATE asset_metadata SET value = ? WHERE key = 'asset_index_sha256'",
                (index_sha,),
            )
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        shard["asset_ledger_sha256"] = _sha256_bytes(ledger_path.read_bytes())

        audit_path = run_root / MEDIA_RECOVERY_AUDIT_FILENAME
        audit = json.loads(audit_path.read_text())
        audit["asset_index_sha256"] = index_sha
        audit_content = canonical_media_recovery_audit_bytes(audit)
        audit_path.write_bytes(audit_content)

        manifest_path = run_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["media_recovery_audit"] = {
            "version": MEDIA_RECOVERY_AUDIT_VERSION,
            "path": MEDIA_RECOVERY_AUDIT_FILENAME,
            "sha256": _sha256_bytes(audit_content),
            "counts": audit["counts"],
        }
        shard["manifest_sha256"] = _write_json(manifest_path, manifest)
        _write_json(workspace / "inputs/merge-request.json", merge_request)

    def _write_merge(self, workspace: Path, output: str) -> dict[str, object]:
        write_merged_shard_audit(
            workspace=workspace,
            targets_path="inputs/targets.json",
            plan_path="plans/two/plan.json",
            merge_request_path="inputs/merge-request.json",
            output_path=output,
        )
        return json.loads((workspace / output).read_text())

    def test_merge_independently_rebuilds_and_binds_reference_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _, _, merge_request = self._build_run_fixture(workspace)

            audit = self._write_merge(workspace, "audits/reference-clean.json")
            reference = audit["transitive_evidence"]["1"][
                "message_reference_resolution_audit"
            ]
            manifest = json.loads(
                (workspace / "runs/shard-1/manifest.json").read_text()
            )
            descriptor = manifest["message_reference_resolution_audit"]

            self.assertEqual(audit["status"], "complete", audit["validation_errors"])
            self.assertEqual(
                reference,
                {
                    "verified": True,
                    "sha256": descriptor["sha256"],
                    "counts": descriptor["counts"],
                },
            )
            self.assertEqual(reference["counts"]["effective_errors"], 0)
            self.assertEqual(
                reference["counts"]["effective_error_diagnostics"],
                0,
            )

            shard = merge_request["shards"][0]
            assert isinstance(shard, dict)
            run_root = workspace / str(shard["run_root"])
            sidecar = json.loads((run_root / descriptor["path"]).read_text())
            sidecar["counts"]["effective_errors"] = 1
            sidecar["counts"]["effective_error_diagnostics"] = 1
            forged_content = canonical_message_reference_resolution_audit_bytes(
                sidecar
            )
            forged_sha = _sha256_bytes(forged_content)
            forged_relative = (
                f"message-reference-resolution-audits/{forged_sha}.json"
            )
            (run_root / forged_relative).write_bytes(forged_content)
            descriptor.update(
                {
                    "version": MESSAGE_REFERENCE_RESOLUTION_AUDIT_VERSION,
                    "path": forged_relative,
                    "sha256": forged_sha,
                    "counts": sidecar["counts"],
                }
            )
            shard["manifest_sha256"] = _write_json(
                run_root / "manifest.json",
                manifest,
            )
            _write_json(workspace / "inputs/merge-request.json", merge_request)

            forged = self._write_merge(
                workspace,
                "audits/reference-attacker-synced.json",
            )
            forged_reference = forged["transitive_evidence"]["1"][
                "message_reference_resolution_audit"
            ]
            self.assertEqual(forged["status"], "failed")
            self.assertFalse(forged_reference["verified"])
            self.assertTrue(
                any(
                    "reference audit differs from verified evidence" in error
                    for error in forged["validation_errors"]
                )
            )

    def test_merge_rebuilds_formal_v2_message_and_pin_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(
                workspace,
                request_versions={1: 2, 2: 2},
                message_evidence_schema_versions={1: 2, 2: 2},
                populated_v2_evidence=True,
            )

            audit = self._write_merge(workspace, "audits/v2-message-pin.json")

            self.assertEqual(audit["status"], "complete", audit["validation_errors"])
            self.assertEqual(audit["validation_errors"], [])
            self.assertTrue(
                all(
                    evidence["message_reference_resolution_audit"]["verified"]
                    for evidence in audit["transitive_evidence"].values()
                )
            )
            pin_descriptor = json.loads(
                (workspace / "runs/shard-1/checkpoint.json").read_text()
            )["streams"]
            pin_descriptor = next(
                state["page_states"][0]["message_evidence"]
                for stream, state in pin_descriptor.items()
                if stream.startswith("pins_")
            )
            self.assertEqual(pin_descriptor["schema_version"], 2)
            self.assertEqual(pin_descriptor["pin_events"], 1)

    def test_merge_accepts_verified_message_warning_manifest_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(
                workspace,
                request_versions={1: 2, 2: 2},
                message_evidence_schema_versions={1: 2, 2: 2},
                allowed_message_warning_shards={1},
                populated_v2_evidence=True,
            )

            audit = self._write_merge(
                workspace,
                "audits/verified-message-warning.json",
            )

            self.assertEqual(audit["status"], "complete", audit["validation_errors"])
            self.assertEqual(audit["validation_errors"], [])
            manifest = json.loads(
                (workspace / "runs/shard-1/manifest.json").read_text()
            )
            self.assertEqual(manifest["status"], "complete_with_warnings")
            self.assertEqual(
                manifest["message_evidence"]["effective_status"],
                "complete_with_warnings",
            )

    def test_merge_rejects_v1_evidence_bound_to_current_v2_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(
                workspace,
                request_versions={1: 2},
                message_evidence_schema_versions={1: 1},
            )

            audit = self._write_merge(
                workspace,
                "audits/request-v2-evidence-v1.json",
            )

            self.assertEqual(audit["status"], "failed")
            self.assertTrue(
                any(
                    "message evidence schema does not match request" in error
                    for error in audit["validation_errors"]
                )
            )

    def test_merge_accepts_explicit_legacy_v1_request_with_v1_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(
                workspace,
                request_option_overrides={
                    1: {"message_evidence_schema_version": 1},
                    2: {"message_evidence_schema_version": 1},
                },
                message_evidence_schema_versions={1: 1, 2: 1},
            )

            audit = self._write_merge(
                workspace,
                "audits/legacy-v1-evidence-v1.json",
            )

            self.assertEqual(audit["status"], "complete", audit["validation_errors"])
            self.assertEqual(audit["validation_errors"], [])

    def test_merge_rejects_v1_evidence_when_legacy_request_defaults_to_v2(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(
                workspace,
                legacy_default_schema_shards={1},
                message_evidence_schema_versions={1: 1},
            )

            audit = self._write_merge(
                workspace,
                "audits/legacy-default-v2-evidence-v1.json",
            )

            self.assertEqual(audit["status"], "failed")
            self.assertTrue(
                any(
                    "message evidence schema does not match request" in error
                    for error in audit["validation_errors"]
                )
            )

    def test_merge_rejects_mixed_message_evidence_schema_in_one_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(
                workspace,
                mixed_message_evidence_schema_shards={1},
            )

            audit = self._write_merge(
                workspace,
                "audits/mixed-message-evidence-schema.json",
            )

            self.assertEqual(audit["status"], "failed")
            self.assertTrue(
                any(
                    "message evidence schemas are mixed within one run" in error
                    for error in audit["validation_errors"]
                )
            )

    def test_non_message_raw_pages_do_not_invent_an_evidence_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(
                workspace,
                request_versions={1: 2, 2: 2},
                message_evidence_schema_versions={1: 2, 2: 2},
                non_message_raw_page_shards={1},
            )

            audit = self._write_merge(
                workspace,
                "audits/non-message-page-schema.json",
            )

            self.assertEqual(audit["status"], "complete", audit["validation_errors"])
            self.assertEqual(audit["validation_errors"], [])

    def test_current_v2_run_without_message_pages_has_no_schema_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            snapshot = _snapshot(root_count=2, thread_count=0, forum_count=2)
            self._build_run_fixture(
                workspace,
                snapshot=snapshot,
                request_versions={1: 2, 2: 2},
            )

            audit = self._write_merge(
                workspace,
                "audits/v2-no-message-pages.json",
            )

            self.assertEqual(audit["status"], "complete", audit["validation_errors"])
            self.assertEqual(audit["validation_errors"], [])

    def test_reference_closure_allows_only_the_audited_warning_code(self) -> None:
        raw_severity = {"error": 0, "warning": 1, "info": 1}
        raw_codes = {
            "error": {},
            "warning": {"snapshot_timestamp_reference_mismatch": 1},
            "info": {"referenced_message_deleted": 1},
        }
        counts = {
            "raw_errors": 0,
            "occurrences": 0,
            "local_resolved": 0,
            "deleted": 0,
            "unresolved": 0,
            "effective_errors": 0,
            "raw_error_diagnostics": 0,
            "non_reference_error_diagnostics": 0,
            "effective_error_diagnostics": 0,
            "raw_partial_messages": 0,
            "effective_partial_messages": 0,
            "raw_diagnostics_by_severity": raw_severity,
            "effective_diagnostics_by_severity": raw_severity,
            "raw_diagnostic_codes_by_severity": raw_codes,
            "effective_diagnostic_codes_by_severity": raw_codes,
        }
        summary = {
            "diagnostics_by_severity": raw_severity,
            "effective_diagnostics_by_severity": raw_severity,
            "diagnostic_codes_by_severity": raw_codes,
            "effective_diagnostic_codes_by_severity": raw_codes,
            "partial_messages": 0,
            "effective_partial_messages": 0,
            "effective_status": "complete_with_warnings",
        }

        valid, complete, reason = discord_sharding_module._validate_message_reference_state(
            summary,
            {"verified": True, "counts": counts},
        )
        self.assertTrue(valid, reason)
        self.assertTrue(complete, reason)

        unsupported_counts = deepcopy(counts)
        unsupported_codes = deepcopy(raw_codes)
        unsupported_codes["warning"] = {"attacker_warning": 1}
        unsupported_counts["raw_diagnostic_codes_by_severity"] = unsupported_codes
        unsupported_counts["effective_diagnostic_codes_by_severity"] = unsupported_codes
        unsupported_summary = deepcopy(summary)
        unsupported_summary["diagnostic_codes_by_severity"] = unsupported_codes
        unsupported_summary["effective_diagnostic_codes_by_severity"] = unsupported_codes
        valid, complete, reason = discord_sharding_module._validate_message_reference_state(
            unsupported_summary,
            {"verified": True, "counts": unsupported_counts},
        )
        self.assertTrue(valid, reason)
        self.assertFalse(complete)
        self.assertIn("unsupported warning", reason)

        swapped_summary = deepcopy(summary)
        swapped_summary["diagnostic_codes_by_severity"] = {
            "error": {},
            "warning": {},
            "info": {
                "referenced_message_deleted": 1,
                "snapshot_timestamp_reference_mismatch": 1,
            },
        }
        valid, complete, reason = discord_sharding_module._validate_message_reference_state(
            swapped_summary,
            {"verified": True, "counts": counts},
        )
        self.assertFalse(valid)
        self.assertFalse(complete)
        self.assertIn("summary differs", reason)

    def test_merge_rejects_missing_reference_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(workspace)
            manifest = json.loads(
                (workspace / "runs/shard-1/manifest.json").read_text()
            )
            (workspace / "runs/shard-1" / manifest[
                "message_reference_resolution_audit"
            ]["path"]).unlink()

            audit = self._write_merge(workspace, "audits/reference-missing.json")

            self.assertEqual(audit["status"], "failed")
            reference = audit["transitive_evidence"]["1"][
                "message_reference_resolution_audit"
            ]
            self.assertFalse(reference["verified"])
            self.assertTrue(
                any(
                    "reference audit" in error
                    for error in audit["validation_errors"]
                )
            )

    def test_merge_rejects_attacker_selected_reference_depth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _, _, merge_request = self._build_run_fixture(workspace)
            shard = merge_request["shards"][0]
            assert isinstance(shard, dict)
            run_root = workspace / str(shard["run_root"])
            manifest_path = run_root / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            descriptor = manifest["message_reference_resolution_audit"]
            sidecar = json.loads((run_root / descriptor["path"]).read_text())
            sidecar["max_depth"] = 9
            content = canonical_message_reference_resolution_audit_bytes(sidecar)
            digest = _sha256_bytes(content)
            relative = f"message-reference-resolution-audits/{digest}.json"
            (run_root / relative).write_bytes(content)
            descriptor.update(
                {
                    "path": relative,
                    "sha256": digest,
                    "counts": sidecar["counts"],
                }
            )
            shard["manifest_sha256"] = _write_json(manifest_path, manifest)
            _write_json(workspace / "inputs/merge-request.json", merge_request)

            audit = self._write_merge(workspace, "audits/reference-depth.json")

            self.assertEqual(audit["status"], "failed")
            self.assertFalse(
                audit["transitive_evidence"]["1"][
                    "message_reference_resolution_audit"
                ]["verified"]
            )
            self.assertTrue(
                any(
                    "reference audit max_depth is invalid" in error
                    for error in audit["validation_errors"]
                )
            )

    def test_failed_merge_audit_marks_operation_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_invalid_merge_request(workspace)
            runner = OperationRunner(build_default_registry(workspace))

            result = runner.run(
                OperationSpec(
                    name="discord_shard_merge_audit",
                    action="audit_logical_merge",
                    connector="discord",
                    payload={
                        "targets": "inputs/targets.json",
                        "plan": "plans/two/plan.json",
                        "merge_request": "inputs/invalid-merge-request.json",
                        "output": "audits/operation-failed.json",
                    },
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )

            self.assertEqual(result.status, OperationStatus.FAILED)
            self.assertIn("audit failed", result.error or "")
            audit = json.loads(
                (workspace / "audits/operation-failed.json").read_text()
            )
            self.assertEqual(audit["status"], "failed")

    def test_failed_merge_audit_returns_nonzero_from_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_invalid_merge_request(workspace)
            parser = argparse.ArgumentParser()
            subparsers = parser.add_subparsers(dest="command", required=True)
            discord.register(subparsers)
            args = parser.parse_args(
                [
                    "discord-shard-merge-audit",
                    "--targets",
                    "inputs/targets.json",
                    "--plan",
                    "plans/two/plan.json",
                    "--merge-request",
                    "inputs/invalid-merge-request.json",
                    "--output",
                    "audits/cli-failed.json",
                ]
            )
            runner = OperationRunner(build_default_registry(workspace))

            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                return_code = discord.COMMANDS[args.command](
                    args,
                    runner=runner,
                    workspace=workspace,
                )

            self.assertEqual(return_code, 1)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "failed")
            audit = json.loads((workspace / "audits/cli-failed.json").read_text())
            self.assertEqual(audit["status"], "failed")

    def test_incomplete_closure_audit_marks_operation_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            t_close = "2026-07-19T12:00:00+00:00"
            merge = {
                "schema_version": 1,
                "audit_kind": "discord-parent-family-merge-v1",
                "status": "complete",
                "guild_id": "1",
                "static_scope": {"exact_union": True, "pairwise_disjoint": True},
                "static_target_ids": ["100"],
                "message_bearing_static_target_ids": ["100"],
                "required_head_catchup_target_ids": ["100"],
                "discovered_threads": [],
                "private_archived_blocked_streams": [],
                "private_archived_incomplete_streams": [],
                "non_private_incomplete_streams": [],
                "media_incomplete_shards": [],
                "validation_errors": [],
            }
            census = {
                "schema_version": 1,
                "guild_id": "1",
                "t_close": t_close,
                "t_close_source_sha256": _PREFLIGHT_SHA,
                "caught_through": "2026-07-19T12:00:01+00:00",
                "high_exclusive": _snowflake_lower_bound(
                    "2026-07-19T12:00:01+00:00"
                ),
                "threads": [],
            }
            descriptor, evidence, _raw_files = _head_catchup_target(
                "100",
                guild_id="1",
                t_close=t_close,
                caught_through="2026-07-19T12:00:01+00:00",
                new_message_ids=(str(int(_snowflake_lower_bound(t_close)) + 1),),
            )
            catchup = {
                "schema_version": 1,
                "guild_id": "1",
                "t_close": t_close,
                "t_close_source_sha256": _PREFLIGHT_SHA,
                "caught_through": "2026-07-19T12:00:01+00:00",
                "high_exclusive": _snowflake_lower_bound(
                    "2026-07-19T12:00:01+00:00"
                ),
                "required_target_ids": ["100"],
                "targets": [descriptor],
            }
            _write_json(workspace / "inputs/merge.json", merge)
            _write_json(workspace / "inputs/census.json", census)
            _write_json(workspace / "inputs/catchup.json", catchup)
            _write_json(workspace / str(descriptor["evidence_path"]), evidence)
            runner = OperationRunner(build_default_registry(workspace))

            result = runner.run(
                OperationSpec(
                    name="discord_shard_closure_audit",
                    action="audit_t_close_closure",
                    connector="discord",
                    payload={
                        "merge_audit": "inputs/merge.json",
                        "census": "inputs/census.json",
                        "head_catchup": "inputs/catchup.json",
                        "t_close": t_close,
                        "output": "audits/incomplete-closure.json",
                    },
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )

            self.assertEqual(result.status, OperationStatus.FAILED)
            self.assertIn("audit incomplete", result.error or "")
            audit = json.loads(
                (workspace / "audits/incomplete-closure.json").read_text()
            )
            self.assertEqual(audit["status"], "incomplete")

    def test_merge_verifies_hashes_scope_owners_and_does_not_copy_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            snapshot, _, _ = self._build_run_fixture(workspace)

            result = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/merge-request.json",
                output_path="audits/merge.json",
            )
            audit = json.loads((workspace / "audits/merge.json").read_text())

            self.assertEqual(result["status"], "complete")
            self.assertEqual(audit["status"], "complete")
            self.assertTrue(audit["static_scope"]["exact_union"])
            self.assertTrue(audit["static_scope"]["pairwise_disjoint"])
            self.assertEqual(audit["static_scope"]["target_count"], 5)
            self.assertEqual(audit["parent_snapshot_sha256"], canonical_json_sha256(snapshot))
            self.assertEqual(audit["stream_status_counts"]["blocked"], 0)
            self.assertEqual(audit["stream_status_counts"]["failed"], 0)
            self.assertEqual(audit["stream_status_counts"]["truncated_by_limit"], 0)
            self.assertTrue(
                all(all(item.values()) for item in audit["artifact_hash_verification"].values())
            )
            self.assertTrue(
                all(
                    item["asset_ledger"]
                    for item in audit["artifact_hash_verification"].values()
                )
            )
            self.assertFalse((workspace / "audits/raw").exists())
            self.assertFalse((workspace / "audits/assets").exists())

    def test_merge_independently_rejects_attacker_synced_media_audit_tampering(self) -> None:
        cases = (
            "counts",
            "one_row",
            "row_deleted",
            "row_order",
            "row_id",
            "candidate_url_hash",
            "descriptor_version",
            "descriptor_extra_field",
            "descriptor_path",
            "descriptor_hash",
            "descriptor_counts",
            "artifact_bytes",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                _, _, merge_request = self._build_run_fixture(
                    workspace,
                    media_overrides={
                        1: {
                            "status": "complete",
                            "records": 1,
                            "complete": 1,
                            "reference_only": 0,
                            "binary_captured": 1,
                            "failed": 0,
                        }
                    },
                    asset_record_overrides={
                        1: [
                            {
                                "attempt_history": [
                                    {
                                        "url": "https://cdn.example/stale-1",
                                        "status": "failed",
                                        "terminal_reason": "download_http_404",
                                        "http_content_type": None,
                                        "http_content_length": None,
                                        "actual_bytes": 0,
                                        "sha256": None,
                                        "blob_path": None,
                                    },
                                    {
                                        "url": "https://cdn.example/stale-2",
                                        "status": "failed",
                                        "terminal_reason": "download_http_400",
                                        "http_content_type": None,
                                        "http_content_length": None,
                                        "actual_bytes": 0,
                                        "sha256": None,
                                        "blob_path": None,
                                    },
                                    _fixture_binary_attempt(),
                                ]
                            }
                        ]
                    },
                )

                def mutate_audit(audit: dict[str, object]) -> None:
                    counts = audit["counts"]
                    items = audit["items"]
                    assert isinstance(counts, dict)
                    assert isinstance(items, list)
                    if case == "counts":
                        counts["rows_total"] = int(counts["rows_total"]) + 1
                    elif case == "one_row":
                        items[0]["candidate_host"] = "attacker.example"
                    elif case == "row_deleted":
                        items.pop()
                        for key in (
                            "rows_total",
                            "attempt_rows",
                            "http_400_404_415_attempt_rows",
                            "candidate_failed_record_covered_attempt_rows",
                        ):
                            counts[key] = int(counts[key]) - 1
                    elif case == "row_order":
                        items.reverse()
                    elif case == "row_id":
                        items[0]["row_id"] = "0" * 64
                    elif case == "candidate_url_hash":
                        items[0]["candidate_url_sha256"] = "0" * 64

                def mutate_descriptor(descriptor: dict[str, object]) -> None:
                    if case == "descriptor_version":
                        descriptor["version"] = MEDIA_RECOVERY_AUDIT_VERSION + 1
                    elif case == "descriptor_extra_field":
                        descriptor["attacker"] = True
                    elif case == "descriptor_path":
                        alternate_name = "attacker-media-audit.json"
                        alternate_path = (
                            workspace / "runs/shard-1" / alternate_name
                        )
                        alternate_path.write_bytes(
                            (
                                workspace
                                / "runs/shard-1"
                                / MEDIA_RECOVERY_AUDIT_FILENAME
                            ).read_bytes()
                        )
                        descriptor["path"] = alternate_name
                    elif case == "descriptor_hash":
                        descriptor["sha256"] = "0" * 64
                    elif case == "descriptor_counts":
                        descriptor["counts"] = {
                            **descriptor["counts"],
                            "rows_total": 999,
                        }

                self._write_attacker_synced_media_tamper(
                    workspace,
                    merge_request,
                    mutate_audit=mutate_audit,
                    mutate_descriptor=mutate_descriptor,
                    noncanonical_bytes=case == "artifact_bytes",
                )
                if case == "descriptor_path":
                    run_root = workspace / "runs/shard-1"
                    manifest = json.loads((run_root / "manifest.json").read_text())
                    descriptor = manifest["media_recovery_audit"]
                    alternate = run_root / descriptor["path"]
                    self.assertTrue(alternate.is_file())
                    self.assertEqual(
                        _sha256_bytes(alternate.read_bytes()),
                        descriptor["sha256"],
                    )
                audit = self._write_merge(workspace, f"audits/tamper-{case}.json")
                recovery = audit["transitive_evidence"]["1"].get(
                    "media_recovery_audit"
                )

                self.assertEqual(audit["status"], "failed")
                self.assertIsInstance(recovery, dict)
                self.assertFalse(recovery["verified"])
                self.assertTrue(
                    any(
                        "media recovery audit" in error
                        for error in audit["validation_errors"]
                    )
                )

    def test_merge_rejects_symlink_and_non_regular_media_audit_artifact(self) -> None:
        for kind in ("symlink", "directory"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                self._build_run_fixture(workspace)
                run_root = workspace / "runs/shard-1"
                audit_path = run_root / MEDIA_RECOVERY_AUDIT_FILENAME
                content = audit_path.read_bytes()
                audit_path.unlink()
                if kind == "symlink":
                    target = run_root / "copied-media-audit.json"
                    target.write_bytes(content)
                    audit_path.symlink_to(target)
                else:
                    audit_path.mkdir()

                audit = self._write_merge(workspace, f"audits/{kind}-media-audit.json")
                recovery = audit["transitive_evidence"]["1"].get(
                    "media_recovery_audit"
                )
                self.assertEqual(audit["status"], "failed")
                self.assertIsInstance(recovery, dict)
                self.assertFalse(recovery["verified"])
                self.assertTrue(
                    any(
                        "media recovery audit" in error
                        for error in audit["validation_errors"]
                    )
                )

    def test_merge_marks_synced_invalid_asset_sources_unverified(self) -> None:
        for case in (
            "actual_index_mismatch",
            "record_hash_mismatch",
            "record_parse_failure",
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                _, _, merge_request = self._build_run_fixture(
                    workspace,
                    media_overrides={
                        1: {
                            "status": "complete",
                            "records": 1,
                            "complete": 1,
                            "binary_captured": 1,
                            "failed": 0,
                        }
                    },
                )
                self._write_attacker_synced_asset_source_tamper(
                    workspace,
                    merge_request,
                    case=case,
                )

                audit = self._write_merge(
                    workspace,
                    f"audits/source-tamper-{case}.json",
                )
                recovery = audit["transitive_evidence"]["1"].get(
                    "media_recovery_audit"
                )

                self.assertEqual(audit["status"], "failed")
                self.assertIsInstance(recovery, dict)
                self.assertFalse(recovery["verified"])
                self.assertTrue(
                    any(
                        error.endswith("media recovery audit source evidence is invalid")
                        for error in audit["validation_errors"]
                    )
                )

    def test_merge_redacts_signed_candidate_from_stable_validation_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            signed_url = (
                "https://cdn.example/media.bin?"
                "X-Amz-Signature=super-secret-token&Authorization=Bearer-secret"
            )
            self._build_run_fixture(
                workspace,
                media_overrides={
                    1: {
                        "status": "complete",
                        "records": 1,
                        "complete": 1,
                        "binary_captured": 1,
                        "failed": 0,
                    }
                },
                asset_record_overrides={
                    1: [
                        {
                            "attempt_history": [
                                {
                                    "url": signed_url,
                                    "status": "failed",
                                    "terminal_reason": (
                                        "media_resolution_failed_transient"
                                    ),
                                    "failure_detail": "resolver_timeout",
                                    "http_content_type": None,
                                    "http_content_length": None,
                                    "actual_bytes": 0,
                                    "sha256": None,
                                    "blob_path": None,
                                    "policy_inputs_sha256": None,
                                    "resolution_retry_sequence": 1,
                                },
                                {
                                    "url": signed_url,
                                    "status": "failed",
                                    "terminal_reason": (
                                        "media_resolution_failed_transient"
                                    ),
                                    "failure_detail": "resolver_eai_again",
                                    "http_content_type": None,
                                    "http_content_length": None,
                                    "actual_bytes": 0,
                                    "sha256": None,
                                    "blob_path": None,
                                    "retry_trigger": "media_resolution_retry_v1",
                                    "retry_of_attempt_number": 1,
                                    "policy_inputs_sha256": None,
                                    "resolution_retry_sequence": 3,
                                },
                                _fixture_binary_attempt(),
                            ]
                        }
                    ]
                },
            )

            audit = self._write_merge(workspace, "audits/signed-url-error.json")
            audit_bytes = (workspace / "audits/signed-url-error.json").read_bytes()
            errors = audit["validation_errors"]
            recovery = audit["transitive_evidence"]["1"]["media_recovery_audit"]

            self.assertEqual(audit["status"], "failed")
            self.assertFalse(recovery["verified"])
            self.assertIn(
                "shard 1 asset resolution attempt history is invalid: "
                "3000000:attachment:asset-0",
                errors,
            )
            for forbidden in (
                signed_url,
                "X-Amz-Signature",
                "super-secret-token",
                "Authorization",
                "Bearer-secret",
            ):
                with self.subTest(forbidden=forbidden):
                    self.assertNotIn(forbidden, "\n".join(errors))
                    self.assertNotIn(forbidden.encode("utf-8"), audit_bytes)

    def test_merge_accepts_schema_v3_and_preserves_schema_v2_compatibility(self) -> None:
        for label, fixture_version, record_override in (
            ("v3", 3, {}),
            ("v3-missing-candidate-ledger", 2, {"schema_version": 3}),
            ("v2", 2, {}),
        ):
            with self.subTest(schema=label), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                self._build_run_fixture(
                    workspace,
                    media_overrides={
                        1: {
                            "status": "complete",
                            "records": 1,
                            "complete": 1,
                            "binary_captured": 1,
                            "failed": 0,
                        }
                    },
                    asset_schema_versions={1: fixture_version},
                    **(
                        {"asset_record_overrides": {1: [record_override]}}
                        if record_override
                        else {}
                    ),
                )
                audit = self._write_merge(
                    workspace,
                    f"audits/schema-{label}.json",
                )
                recovery = audit["transitive_evidence"]["1"].get(
                    "media_recovery_audit"
                )
                record = json.loads(
                    next((workspace / "runs/shard-1/asset-records").glob("*.json")).read_text()
                )

                self.assertEqual(audit["status"], "complete")
                expected_version = record_override.get(
                    "schema_version",
                    fixture_version,
                )
                self.assertEqual(record["schema_version"], expected_version)
                self.assertEqual(
                    "candidate_urls" in record,
                    fixture_version == 3,
                )
                self.assertEqual(
                    recovery,
                    {
                        "verified": True,
                        "sha256": json.loads(
                            (workspace / "runs/shard-1/manifest.json").read_text()
                        )["media_recovery_audit"]["sha256"],
                        "counts": json.loads(
                            (
                                workspace
                                / "runs/shard-1"
                                / MEDIA_RECOVERY_AUDIT_FILENAME
                            ).read_text()
                        )["counts"],
                    },
                )

    def test_merge_rebuilds_policy_binding_from_actual_opt_in_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            policy = rfc2544_fake_ip_media_policy_descriptor()
            self._build_run_fixture(
                workspace,
                request_option_overrides={
                    1: {
                        "allow_rfc2544_fake_ip": True,
                        "rfc2544_fake_ip_policy": policy,
                    }
                },
            )
            audit = self._write_merge(workspace, "audits/opt-in-request.json")
            recovery = audit["transitive_evidence"]["1"]["media_recovery_audit"]
            artifact = json.loads(
                (
                    workspace
                    / "runs/shard-1"
                    / MEDIA_RECOVERY_AUDIT_FILENAME
                ).read_text()
            )

            self.assertEqual(audit["status"], "complete")
            self.assertTrue(recovery["verified"])
            self.assertEqual(
                artifact["policy_inputs_sha256"],
                policy["inputs_sha256"],
            )

    def test_merge_rejects_invalid_schema_v3_candidate_urls(self) -> None:
        invalid_values = (None, [], [""], ["https://cdn.example/other"])
        for candidate_urls in invalid_values:
            with (
                self.subTest(candidate_urls=candidate_urls),
                tempfile.TemporaryDirectory() as directory,
            ):
                workspace = Path(directory)
                _, _, merge_request = self._build_run_fixture(
                    workspace,
                    media_overrides={
                        1: {
                            "status": "complete",
                            "records": 1,
                            "complete": 1,
                            "binary_captured": 1,
                            "failed": 0,
                        }
                    },
                    asset_record_overrides={1: [{}]},
                )
                self._write_attacker_synced_candidate_urls_tamper(
                    workspace,
                    merge_request,
                    candidate_urls=candidate_urls,
                )
                audit = self._write_merge(
                    workspace,
                    "audits/invalid-candidate-urls.json",
                )
                self.assertEqual(audit["status"], "failed")
                self.assertTrue(
                    any(
                        "candidate URLs" in error
                        for error in audit["validation_errors"]
                    )
                )

    def test_reference_only_is_not_binary_and_only_exact_youtube_reference_may_omit_blob(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(
                workspace,
                media_overrides={
                    1: {
                        "status": "complete_with_warnings",
                        "records": 1,
                        "complete": 0,
                        "reference_only": 1,
                        "binary_captured": 0,
                        "failed": 0,
                    }
                },
            )
            audit = self._write_merge(workspace, "audits/youtube-reference.json")
            evidence = audit["transitive_evidence"]["1"]["asset_evidence"]
            recovery = audit["transitive_evidence"]["1"].get(
                "media_recovery_audit"
            )

            self.assertEqual(audit["status"], "complete")
            self.assertIsInstance(recovery, dict)
            self.assertEqual(evidence["binary_captured_record_count"], 0)
            self.assertEqual(recovery["counts"]["current_reference_only_records"], 1)
            self.assertEqual(recovery["counts"]["binary_captured_attempt_rows"], 0)

        invalid_references = (
            {
                "url": "https://cdn.example/not-youtube",
                "candidate_urls": ["https://cdn.example/not-youtube"],
            },
            {"kind": "attachment", "field": "attachment"},
            *(
                {
                    "reference_provenance": {
                        "classification": "forged",
                        "failed_attempt_number": value,
                    }
                }
                for value in (None, True, 1.0, "1")
            ),
        )
        for spec in invalid_references:
            with self.subTest(spec=spec), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                self._build_run_fixture(
                    workspace,
                    media_overrides={
                        1: {
                            "status": "complete_with_warnings",
                            "records": 1,
                            "complete": 0,
                            "reference_only": 1,
                            "binary_captured": 0,
                            "failed": 0,
                        }
                    },
                    asset_record_overrides={1: [spec]},
                )
                audit = self._write_merge(
                    workspace,
                    "audits/invalid-reference.json",
                )
                self.assertEqual(audit["status"], "failed")
                self.assertTrue(
                    any(
                        "reference provenance" in error
                        for error in audit["validation_errors"]
                    )
                )

    def test_youtube_reference_non_tail_history_is_strictly_fail_closed(
        self,
    ) -> None:
        player_url = "https://www.youtube.com/embed/StrictHistory"
        source = {
            "message_id": "30",
            "channel_id": "300",
            "stream": "messages_300",
        }
        unsafe_attempt = {
            "url": player_url,
            "status": "failed",
            "terminal_reason": "unsafe_media_url",
            "http_content_type": None,
            "http_content_length": None,
            "actual_bytes": 0,
            "sha256": None,
            "blob_path": None,
        }
        record = {
            "schema_version": 3,
            "kind": "embed",
            "field": "video",
            "declared_metadata": {"url": player_url, "proxy_url": None},
            "declared_content_type": None,
            "identity_metadata": {},
            "sources": [source],
            "observations": [
                {
                    "source": source,
                    "url": player_url,
                    "proxy_url": None,
                    "metadata": {"url": player_url, "proxy_url": None},
                }
            ],
            "attempt_history": [unsafe_attempt],
        }
        distinct_failure = {
            **unsafe_attempt,
            "url": "https://cdn.example/distinct-failure",
            "terminal_reason": "download_http_404",
        }
        allowed = deepcopy(record)
        allowed["attempt_history"].append(distinct_failure)
        self.assertIsNotNone(
            discord_sharding_module._youtube_player_reference_provenance(
                allowed,
                source_url=player_url,
                failed_attempt_number=1,
            )
        )

        hostile_attempts = (
            {**distinct_failure, "url": player_url},
            {
                **distinct_failure,
                "status": "in_progress",
                "terminal_reason": None,
            },
            {
                **distinct_failure,
                "status": "complete",
                "terminal_reason": "downloaded",
            },
        )
        for later_attempt in hostile_attempts:
            hostile = deepcopy(record)
            hostile["attempt_history"].append(later_attempt)
            with self.subTest(later_attempt=later_attempt):
                self.assertIsNone(
                    discord_sharding_module._youtube_player_reference_provenance(
                        hostile,
                        source_url=player_url,
                        failed_attempt_number=1,
                    )
                )

    def test_exact_youtube_reference_binds_active_ledger_to_observed_failures(
        self,
    ) -> None:
        source_url = "https://www.youtube.com/embed/ExactClosure"
        historical_url = "https://media.discordapp.net/external/historical"
        active_url = "https://media.discordapp.net/external/active"
        source = {
            "message_id": "30",
            "channel_id": "300",
            "stream": "messages_300",
        }
        source_metadata = {"url": source_url, "proxy_url": None}
        unsafe_attempt = {
            "url": source_url,
            "status": "failed",
            "terminal_reason": "unsafe_media_url",
            "http_content_type": None,
            "http_content_length": None,
            "actual_bytes": 0,
            "sha256": None,
            "blob_path": None,
        }

        def failed(url: str) -> dict[str, object]:
            return {
                **unsafe_attempt,
                "url": url,
                "terminal_reason": "download_http_404",
            }

        record = {
            "schema_version": 3,
            "kind": "embed",
            "field": "video",
            "url": source_url,
            "candidate_urls": [source_url, active_url],
            "declared_metadata": source_metadata,
            "declared_content_type": None,
            "identity_metadata": {},
            "sources": [source],
            "observations": [
                {
                    "source": deepcopy(source),
                    "url": source_url,
                    "proxy_url": None,
                    "metadata": deepcopy(source_metadata),
                },
                *(
                    {
                        "source": deepcopy(source),
                        "url": url,
                        "proxy_url": None,
                        "metadata": {"url": url, "proxy_url": None},
                    }
                    for url in (historical_url, active_url)
                ),
            ],
            "attempt_history": [
                unsafe_attempt,
                failed(historical_url),
                failed(active_url),
            ],
            "status": "reference_only",
            "terminal_reason": "youtube_embed_player_reference",
            "http_content_type": None,
            "http_content_length": None,
            "actual_bytes": 0,
            "sha256": None,
            "blob_path": None,
        }
        provenance = (
            discord_sharding_module._youtube_player_reference_provenance(
                record,
                source_url=source_url,
                failed_attempt_number=1,
            )
        )
        self.assertIsNotNone(provenance)
        record["reference_provenance"] = provenance
        self.assertTrue(
            discord_sharding_module._is_exact_youtube_player_reference(record)
        )

        benign_multiple_sources = deepcopy(record)
        benign_multiple_sources["declared_metadata"] = {"url": source_url}
        benign_multiple_sources["observations"][0]["metadata"] = {
            "url": source_url
        }
        benign_multiple_sources["observations"].append(
            {
                "source": deepcopy(source),
                "url": source_url,
                "proxy_url": None,
                "metadata": {
                    "url": source_url,
                    "proxy_url": None,
                },
            }
        )
        self.assertTrue(
            discord_sharding_module._is_exact_youtube_player_reference(
                benign_multiple_sources
            )
        )

        unseen = deepcopy(record)
        unseen_url = "https://media.discordapp.net/external/unseen"
        unseen["candidate_urls"].append(unseen_url)
        unseen["observations"].append(
            {
                "source": deepcopy(source),
                "url": unseen_url,
                "proxy_url": None,
                "metadata": {"url": unseen_url, "proxy_url": None},
            }
        )
        self.assertFalse(
            discord_sharding_module._is_exact_youtube_player_reference(unseen)
        )

        unobserved_history = deepcopy(record)
        unobserved_history["attempt_history"].insert(
            1,
            failed("https://media.discordapp.net/external/unobserved-history"),
        )
        self.assertFalse(
            discord_sharding_module._is_exact_youtube_player_reference(
                unobserved_history
            )
        )

        mismatched_source_metadata = deepcopy(record)
        mismatched_source_metadata["observations"][0]["metadata"]["url"] = (
            "https://media.discordapp.net/external/forged-source"
        )
        self.assertFalse(
            discord_sharding_module._is_exact_youtube_player_reference(
                mismatched_source_metadata
            )
        )

        proxied_source_metadata = deepcopy(record)
        proxied_source_metadata["observations"][0]["metadata"]["proxy_url"] = (
            active_url
        )
        self.assertFalse(
            discord_sharding_module._is_exact_youtube_player_reference(
                proxied_source_metadata
            )
        )

        split_source_authority = deepcopy(record)
        forged_metadata_url = (
            "https://media.discordapp.net/external/forged-declared-source"
        )
        forged_metadata = {
            "url": forged_metadata_url,
            "proxy_url": None,
        }
        split_source_authority["observations"].append(
            {
                "source": deepcopy(source),
                "url": source_url,
                "proxy_url": None,
                "metadata": deepcopy(forged_metadata),
            }
        )
        split_source_authority["declared_metadata"] = forged_metadata
        self.assertFalse(
            discord_sharding_module._is_exact_youtube_player_reference(
                split_source_authority
            )
        )

    def test_covered_404_attempt_does_not_create_a_current_failed_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(
                workspace,
                media_overrides={
                    1: {
                        "status": "complete",
                        "records": 1,
                        "complete": 1,
                        "binary_captured": 1,
                        "failed": 0,
                    }
                },
                asset_record_overrides={
                    1: [
                        {
                            "attempt_history": [
                                {
                                    "url": "https://cdn.example/stale",
                                    "status": "failed",
                                    "terminal_reason": "download_http_404",
                                    "http_content_type": None,
                                    "http_content_length": None,
                                    "actual_bytes": 0,
                                    "sha256": None,
                                    "blob_path": None,
                                },
                                _fixture_binary_attempt(),
                            ]
                        }
                    ]
                },
            )
            audit = self._write_merge(workspace, "audits/covered-404.json")
            recovery = audit["transitive_evidence"]["1"].get(
                "media_recovery_audit"
            )

            self.assertEqual(audit["status"], "complete")
            self.assertIsInstance(recovery, dict)
            counts = recovery["counts"]
            self.assertEqual(counts["candidate_failed_record_covered_attempt_rows"], 1)
            self.assertEqual(counts["current_failed_records"], 0)
            self.assertEqual(counts["unresolved_blockers"], 0)

    def test_synced_404_only_forged_complete_fails_merge_and_closure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _, _, merge_request = self._build_run_fixture(
                workspace,
                media_overrides={
                    1: {
                        "status": "complete",
                        "records": 1,
                        "complete": 1,
                        "binary_captured": 1,
                        "failed": 0,
                    }
                },
                asset_record_overrides={
                    1: [
                        {
                            "attempt_history": [
                                {
                                    "url": "https://cdn.example/stale",
                                    "status": "failed",
                                    "terminal_reason": "download_http_404",
                                    "http_content_type": None,
                                    "http_content_length": None,
                                    "actual_bytes": 0,
                                    "sha256": None,
                                    "blob_path": None,
                                },
                                _fixture_binary_attempt(),
                            ]
                        }
                    ]
                },
            )
            self._write_attacker_synced_success_tail_removal(
                workspace,
                merge_request,
            )

            merge = self._write_merge(
                workspace,
                "audits/synced-404-only-forged-complete.json",
            )
            evidence = merge["transitive_evidence"]["1"]["asset_evidence"]
            recovery = merge["transitive_evidence"]["1"][
                "media_recovery_audit"
            ]

            self.assertEqual(merge["status"], "failed")
            self.assertTrue(
                all(merge["artifact_hash_verification"]["1"].values())
            )
            self.assertIn(
                "asset resolution attempt history is invalid: "
                "3000000:attachment:asset-0",
                evidence["validation_errors"],
            )
            self.assertFalse(recovery["verified"])
            self.assertIn(
                "media recovery audit source evidence is invalid",
                evidence["validation_errors"],
            )

            t_close = "2026-07-19T12:00:00+00:00"
            caught_through = "2026-07-19T12:00:01+00:00"
            target_evidence = {
                target_id: _head_catchup_target(
                    target_id,
                    guild_id=str(merge["guild_id"]),
                    t_close=t_close,
                    caught_through=caught_through,
                )
                for target_id in merge["required_head_catchup_target_ids"]
            }
            catchup = {
                "schema_version": 1,
                "guild_id": merge["guild_id"],
                "t_close": t_close,
                "t_close_source_sha256": _PREFLIGHT_SHA,
                "caught_through": caught_through,
                "high_exclusive": _snowflake_lower_bound(caught_through),
                "required_target_ids": merge["required_head_catchup_target_ids"],
                "targets": [
                    target_evidence[target_id][0]
                    for target_id in merge["required_head_catchup_target_ids"]
                ],
            }
            census = {
                "schema_version": 1,
                "guild_id": merge["guild_id"],
                "t_close": t_close,
                "t_close_source_sha256": _PREFLIGHT_SHA,
                "caught_through": caught_through,
                "high_exclusive": _snowflake_lower_bound(caught_through),
                "threads": [
                    {"id": item["id"], "parent_id": item["parent_id"]}
                    for item in merge["discovered_threads"]
                ],
            }
            closure = audit_closure(
                merge,
                census,
                catchup,
                t_close=t_close,
                verified_head_evidence={
                    target_id: _verified_head_target(*target_evidence[target_id])
                    for target_id in target_evidence
                },
            )

            self.assertFalse(closure["authorized_scope_point_in_time_complete"])

    def test_request_bound_asset_limit_blocks_merge_and_closure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _, _, merge_request = self._build_run_fixture(
                workspace,
                media_overrides={
                    1: {
                        "status": "complete",
                        "records": 1,
                        "complete": 1,
                        "binary_captured": 1,
                        "failed": 0,
                    }
                },
                request_option_overrides={1: {"max_asset_bytes": 4}},
            )

            merge = self._write_merge(
                workspace,
                "audits/request-bound-asset-limit.json",
            )
            evidence = merge["transitive_evidence"]["1"]["asset_evidence"]
            recovery = merge["transitive_evidence"]["1"][
                "media_recovery_audit"
            ]

            self.assertEqual(merge["status"], "failed")
            self.assertTrue(all(merge["artifact_hash_verification"]["1"].values()))
            self.assertTrue(
                any(
                    "request max_asset_bytes" in error
                    for error in evidence["validation_errors"]
                )
            )
            self.assertFalse(recovery["verified"])

            t_close = "2026-07-19T12:00:00+00:00"
            caught_through = "2026-07-19T12:00:01+00:00"
            target_evidence = {
                target_id: _head_catchup_target(
                    target_id,
                    guild_id=str(merge["guild_id"]),
                    t_close=t_close,
                    caught_through=caught_through,
                )
                for target_id in merge["required_head_catchup_target_ids"]
            }
            catchup = {
                "schema_version": 1,
                "guild_id": merge["guild_id"],
                "t_close": t_close,
                "t_close_source_sha256": _PREFLIGHT_SHA,
                "caught_through": caught_through,
                "high_exclusive": _snowflake_lower_bound(caught_through),
                "required_target_ids": merge["required_head_catchup_target_ids"],
                "targets": [
                    target_evidence[target_id][0]
                    for target_id in merge["required_head_catchup_target_ids"]
                ],
            }
            census = {
                "schema_version": 1,
                "guild_id": merge["guild_id"],
                "t_close": t_close,
                "t_close_source_sha256": _PREFLIGHT_SHA,
                "caught_through": caught_through,
                "high_exclusive": _snowflake_lower_bound(caught_through),
                "threads": [
                    {"id": item["id"], "parent_id": item["parent_id"]}
                    for item in merge["discovered_threads"]
                ],
            }
            closure = audit_closure(
                merge,
                census,
                catchup,
                t_close=t_close,
                verified_head_evidence={
                    target_id: _verified_head_target(*target_evidence[target_id])
                    for target_id in target_evidence
                },
            )

            self.assertFalse(closure["authorized_scope_point_in_time_complete"])

    def test_verified_unresolved_media_blocker_is_partial_and_blocks_closure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(
                workspace,
                media_overrides={
                    1: {
                        "status": "partial",
                        "records": 1,
                        "complete": 0,
                        "binary_captured": 0,
                        "failed": 1,
                    }
                },
            )
            merge = self._write_merge(workspace, "audits/blocker-merge.json")
            recovery = merge["transitive_evidence"]["1"].get(
                "media_recovery_audit"
            )

            self.assertEqual(merge["status"], "partial")
            self.assertIsInstance(recovery, dict)
            self.assertTrue(recovery["verified"])
            self.assertEqual(recovery["counts"]["unresolved_blockers"], 1)
            self.assertEqual(merge["media_incomplete_shards"][0]["index"], 1)

            t_close = "2026-07-19T12:00:00+00:00"
            caught_through = "2026-07-19T12:00:01+00:00"
            target_evidence = {
                target_id: _head_catchup_target(
                    target_id,
                    guild_id=str(merge["guild_id"]),
                    t_close=t_close,
                    caught_through=caught_through,
                )
                for target_id in merge["required_head_catchup_target_ids"]
            }
            catchup = {
                "schema_version": 1,
                "guild_id": merge["guild_id"],
                "t_close": t_close,
                "t_close_source_sha256": _PREFLIGHT_SHA,
                "caught_through": caught_through,
                "high_exclusive": _snowflake_lower_bound(caught_through),
                "required_target_ids": merge["required_head_catchup_target_ids"],
                "targets": [
                    target_evidence[target_id][0]
                    for target_id in merge["required_head_catchup_target_ids"]
                ],
            }
            census = {
                "schema_version": 1,
                "guild_id": merge["guild_id"],
                "t_close": t_close,
                "t_close_source_sha256": _PREFLIGHT_SHA,
                "caught_through": caught_through,
                "high_exclusive": _snowflake_lower_bound(caught_through),
                "threads": [
                    {"id": item["id"], "parent_id": item["parent_id"]}
                    for item in merge["discovered_threads"]
                ],
            }
            closure = audit_closure(
                merge,
                census,
                catchup,
                t_close=t_close,
                verified_head_evidence={
                    target_id: _verified_head_target(*target_evidence[target_id])
                    for target_id in target_evidence
                },
            )
            self.assertFalse(closure["authorized_scope_point_in_time_complete"])

    def test_blocker_cannot_hide_behind_complete_media_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _, _, merge_request = self._build_run_fixture(
                workspace,
                media_overrides={
                    1: {
                        "status": "partial",
                        "records": 1,
                        "complete": 0,
                        "binary_captured": 0,
                        "failed": 1,
                    }
                },
            )
            shard = merge_request["shards"][0]
            run_root = workspace / str(shard["run_root"])
            manifest_path = run_root / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["media"]["status"] = "complete"
            manifest["status"] = "complete"
            shard["manifest_sha256"] = _write_json(manifest_path, manifest)
            _write_json(workspace / "inputs/merge-request.json", merge_request)

            audit = self._write_merge(workspace, "audits/hidden-blocker.json")

            self.assertEqual(audit["status"], "failed")
            self.assertTrue(audit["media_incomplete_shards"])
            self.assertEqual(audit["media_incomplete_shards"][0]["index"], 1)
            self.assertIn(
                "incomplete records",
                audit["media_incomplete_shards"][0]["reason"],
            )

    def test_merge_excludes_ten_forum_roots_from_132_target_head_catchup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            snapshot = _snapshot(forum_count=10)
            self._build_run_fixture(workspace, snapshot=snapshot)

            result = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/merge-request.json",
                output_path="audits/forum-scope.json",
            )
            audit = json.loads((workspace / "audits/forum-scope.json").read_text())
            forum_ids = {
                target["id"]
                for target in snapshot["targets"]
                if target["kind"] == "GUILD_FORUM (15)"
            }
            required_ids = set(audit["required_head_catchup_target_ids"])

            self.assertEqual(result["status"], "complete")
            self.assertEqual(len(snapshot["targets"]), 132)
            self.assertEqual(len(forum_ids), 10)
            self.assertTrue(forum_ids.isdisjoint(required_ids))
            self.assertEqual(len(required_ids), 122)

    def test_merge_rejects_planned_target_without_required_message_stream(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _, _, merge_request = self._build_run_fixture(workspace)
            run_root = workspace / "runs/shard-1"
            checkpoint = json.loads((run_root / "checkpoint.json").read_text())
            manifest = json.loads((run_root / "manifest.json").read_text())
            stream_name = next(
                name for name in checkpoint["streams"] if name.startswith("messages_")
            )
            target_id = stream_name.removeprefix("messages_")
            del checkpoint["streams"][stream_name]
            manifest["streams"] = json.loads(json.dumps(checkpoint["streams"]))
            shutil.rmtree(run_root / "pages" / stream_name)
            shutil.rmtree(run_root / "message-evidence" / stream_name)
            manifest["message_evidence"]["pages"] -= 1
            manifest["message_evidence"]["expected_pages"] -= 1
            merge_request["shards"][0]["checkpoint_sha256"] = _write_json(
                run_root / "checkpoint.json",
                checkpoint,
            )
            merge_request["shards"][0]["manifest_sha256"] = _write_json(
                run_root / "manifest.json",
                manifest,
            )
            _write_json(workspace / "inputs/missing-stream-request.json", merge_request)

            result = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/missing-stream-request.json",
                output_path="audits/missing-stream.json",
            )
            audit = json.loads((workspace / "audits/missing-stream.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertTrue(
                any(
                    target_id in error and "required stream" in error
                    for error in audit["validation_errors"]
                )
            )

    def test_merge_rejects_message_bearing_target_without_required_pin_stream(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _, _, merge_request = self._build_run_fixture(workspace)
            run_root = workspace / "runs/shard-1"
            checkpoint = json.loads((run_root / "checkpoint.json").read_text())
            manifest = json.loads((run_root / "manifest.json").read_text())
            stream_name = next(
                name
                for name in checkpoint["streams"]
                if name.startswith("pins_")
                and name.removeprefix("pins_") != _OXSUN_ROOT_ID
            )
            target_id = stream_name.removeprefix("pins_")
            del checkpoint["streams"][stream_name]
            manifest["streams"] = json.loads(json.dumps(checkpoint["streams"]))
            shutil.rmtree(run_root / "pages" / stream_name)
            shutil.rmtree(run_root / "message-evidence" / stream_name)
            manifest["message_evidence"]["pages"] -= 1
            manifest["message_evidence"]["expected_pages"] -= 1
            merge_request["shards"][0]["checkpoint_sha256"] = _write_json(
                run_root / "checkpoint.json",
                checkpoint,
            )
            merge_request["shards"][0]["manifest_sha256"] = _write_json(
                run_root / "manifest.json",
                manifest,
            )
            _write_json(workspace / "inputs/missing-pin-request.json", merge_request)

            result = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/missing-pin-request.json",
                output_path="audits/missing-pin.json",
            )
            audit = json.loads((workspace / "audits/missing-pin.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertTrue(
                any(
                    target_id in error and stream_name in error
                    for error in audit["validation_errors"]
                )
            )

    def test_merge_rejects_inventory_thread_without_required_collection_streams(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _, _, merge_request = self._build_run_fixture(workspace)
            shard = merge_request["shards"][0]
            inventory_path = workspace / str(shard["run_root"]) / "inventory/targets.json"
            inventory = json.loads(inventory_path.read_text())
            parent_id = next(
                row["requested"]["id"]
                for row in inventory["targets"]
                if "THREAD" not in row["requested"]["kind"].upper()
            )
            thread_id = "2999998"
            inventory["threads"].append(
                {
                    "id": thread_id,
                    "name": "dynamic-thread-without-streams",
                    "kind": "GUILD_PUBLIC_THREAD (11)",
                    "parent_id": parent_id,
                    "sources": ["active"],
                }
            )
            shard["targets_inventory_sha256"] = _write_json(inventory_path, inventory)
            _write_json(workspace / "inputs/missing-thread-streams.json", merge_request)

            result = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/missing-thread-streams.json",
                output_path="audits/missing-thread-streams.json",
            )
            audit = json.loads(
                (workspace / "audits/missing-thread-streams.json").read_text()
            )
            self.assertEqual(result["status"], "failed")
            self.assertTrue(
                any(
                    thread_id in error and "required stream" in error
                    for error in audit["validation_errors"]
                )
            )

    def test_merge_rejects_same_shard_explicit_thread_parent_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _, _, merge_request = self._build_run_fixture(workspace)
            changed: tuple[str, str, str] | None = None
            for shard in merge_request["shards"]:
                inventory_path = workspace / str(shard["run_root"]) / "inventory/targets.json"
                inventory = json.loads(inventory_path.read_text())
                if not inventory["threads"]:
                    continue
                thread = inventory["threads"][0]
                planned_parent = thread["parent_id"]
                alternate_parent = next(
                    row["requested"]["id"]
                    for row in inventory["targets"]
                    if row["requested"]["id"] not in {thread["id"], planned_parent}
                    and "THREAD" not in row["requested"]["kind"].upper()
                )
                thread["parent_id"] = alternate_parent
                shard["targets_inventory_sha256"] = _write_json(
                    inventory_path,
                    inventory,
                )
                changed = (thread["id"], planned_parent, alternate_parent)
                break
            self.assertIsNotNone(changed)
            assert changed is not None
            thread_id, planned_parent, alternate_parent = changed
            _write_json(workspace / "inputs/parent-drift-request.json", merge_request)

            result = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/parent-drift-request.json",
                output_path="audits/parent-drift.json",
            )
            audit = json.loads((workspace / "audits/parent-drift.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertTrue(
                any(
                    thread_id in error
                    and planned_parent in error
                    and alternate_parent in error
                    for error in audit["validation_errors"]
                )
            )

    def test_merge_counts_blocked_failed_and_truncated_separately_and_fails_hash_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _, _, merge_request = self._build_run_fixture(
                workspace,
                stream_overrides={
                    1: {
                        f"threads_{_OXSUN_ROOT_ID}_private_archived": {
                            "status": "blocked",
                            "terminal_reason": "forbidden",
                        },
                        "synthetic_failed": {
                            "status": "failed",
                            "terminal_reason": "invalid_items",
                        },
                        "synthetic_truncated": {
                            "status": "truncated_by_limit",
                            "terminal_reason": "truncated_by_limit",
                        },
                    }
                },
                media_overrides={
                    1: {"status": "partial", "records": 1, "complete": 0, "failed": 1}
                },
            )
            partial = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/merge-request.json",
                output_path="audits/partial.json",
            )
            audit = json.loads((workspace / "audits/partial.json").read_text())
            self.assertEqual(partial["status"], "partial")
            self.assertEqual(audit["stream_status_counts"]["blocked"], 1)
            self.assertEqual(audit["stream_status_counts"]["failed"], 1)
            self.assertEqual(audit["stream_status_counts"]["truncated_by_limit"], 1)
            self.assertEqual(len(audit["private_archived_blocked_streams"]), 1)
            self.assertNotEqual(audit["status"], "complete")

            merge_request["shards"][0]["manifest_sha256"] = "0" * 64
            _write_json(workspace / "inputs/tampered-merge-request.json", merge_request)
            tampered = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/tampered-merge-request.json",
                output_path="audits/tampered.json",
            )
            tampered_audit = json.loads((workspace / "audits/tampered.json").read_text())
            self.assertEqual(tampered["status"], "failed")
            self.assertFalse(
                tampered_audit["artifact_hash_verification"]["1"]["manifest"]
            )
            self.assertTrue(
                any("manifest_sha256" in error for error in tampered_audit["validation_errors"])
            )

    def test_merge_rejects_cross_shard_thread_duplicate_and_wrong_parent_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _, _, merge_request = self._build_run_fixture(workspace)
            first_inventory = json.loads(
                (workspace / "runs/shard-1/inventory/targets.json").read_text()
            )
            foreign_thread = {
                "id": "2999999",
                "name": "dynamic-thread",
                "kind": "GUILD_PUBLIC_THREAD (11)",
                "parent_id": first_inventory["targets"][0]["requested"]["id"],
                "sources": ["active"],
            }
            first_inventory["threads"].append(foreign_thread)
            second_inventory = json.loads(
                (workspace / "runs/shard-2/inventory/targets.json").read_text()
            )
            second_inventory["threads"].append(foreign_thread)
            merge_request["shards"][0]["targets_inventory_sha256"] = _write_json(
                workspace / "runs/shard-1/inventory/targets.json", first_inventory
            )
            merge_request["shards"][1]["targets_inventory_sha256"] = _write_json(
                workspace / "runs/shard-2/inventory/targets.json", second_inventory
            )
            _write_json(workspace / "inputs/duplicate-request.json", merge_request)

            result = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/duplicate-request.json",
                output_path="audits/duplicate.json",
            )
            audit = json.loads((workspace / "audits/duplicate.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertIn("2999999", audit["thread_scope"]["duplicate_thread_ids"])
            self.assertIn("2999999", audit["thread_scope"]["wrong_parent_owner_thread_ids"])

    def test_merge_inputs_fail_closed_on_symlink_components(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(workspace)
            (workspace / "linked-plans").symlink_to(
                workspace / "plans",
                target_is_directory=True,
            )
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                write_merged_shard_audit(
                    workspace=workspace,
                    targets_path="inputs/targets.json",
                    plan_path="linked-plans/two/plan.json",
                    merge_request_path="inputs/merge-request.json",
                    output_path="audits/rejected.json",
                )

    def test_merge_fails_when_a_checkpoint_pinned_raw_page_is_tampered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(workspace)
            page = next((workspace / "runs/shard-1/pages").glob("*/000001.json"))
            page.write_bytes(b"{}\n")

            result = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/merge-request.json",
                output_path="audits/page-tamper.json",
            )
            audit = json.loads((workspace / "audits/page-tamper.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertTrue(
                any("raw page hash" in error for error in audit["validation_errors"])
            )

    def test_merge_fails_when_checkpoint_pinned_message_evidence_is_tampered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(workspace)
            evidence = next(
                (workspace / "runs/shard-1/message-evidence").glob("*/*.jsonl")
            )
            evidence.write_bytes(b"{}\n")

            result = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/merge-request.json",
                output_path="audits/message-evidence-tamper.json",
            )
            audit = json.loads(
                (workspace / "audits/message-evidence-tamper.json").read_text()
            )
            self.assertEqual(result["status"], "failed")
            self.assertTrue(
                any(
                    "message evidence hash mismatch" in error
                    for error in audit["validation_errors"]
                )
            )

    def test_merge_rejects_raw_message_missing_from_message_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _, _, merge_request = self._build_run_fixture(workspace)
            run_root = workspace / "runs/shard-1"
            checkpoint = json.loads((run_root / "checkpoint.json").read_text())
            manifest = json.loads((run_root / "manifest.json").read_text())
            stream_name = next(
                name for name in checkpoint["streams"] if name.startswith("messages_")
            )
            target_id = stream_name.removeprefix("messages_")
            raw_hash = _write_json(
                run_root / f"pages/{stream_name}/000001.json",
                {
                    "request": {"path": f"/channels/{target_id}/messages"},
                    "payload": [
                        {
                            "id": "999999",
                            "channel_id": target_id,
                            "content": "present only in raw evidence",
                        }
                    ],
                    "pagination": {
                        "item_count": 1,
                        "next_cursor": None,
                        "terminal_status": "complete",
                    },
                },
            )
            stream = checkpoint["streams"][stream_name]
            stream["page_hashes"] = [raw_hash]
            stream["page_states"][0]["message_evidence"][
                "raw_page_sha256"
            ] = raw_hash
            manifest["streams"] = json.loads(json.dumps(checkpoint["streams"]))
            merge_request["shards"][0]["checkpoint_sha256"] = _write_json(
                run_root / "checkpoint.json",
                checkpoint,
            )
            merge_request["shards"][0]["manifest_sha256"] = _write_json(
                run_root / "manifest.json",
                manifest,
            )
            _write_json(workspace / "inputs/raw-message-gap.json", merge_request)

            result = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/raw-message-gap.json",
                output_path="audits/raw-message-gap.json",
            )
            audit = json.loads((workspace / "audits/raw-message-gap.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertTrue(
                any(
                    "raw message count" in error and stream_name in error
                    for error in audit["validation_errors"]
                )
            )

    def test_merge_rejects_equal_count_message_evidence_for_the_wrong_root_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _, _, merge_request = self._build_run_fixture(workspace)
            run_root = workspace / "runs/shard-1"
            checkpoint = json.loads((run_root / "checkpoint.json").read_text())
            manifest = json.loads((run_root / "manifest.json").read_text())
            stream_name = next(
                name for name in checkpoint["streams"] if name.startswith("messages_")
            )
            target_id = stream_name.removeprefix("messages_")
            raw_message = {"id": "999999", "channel_id": target_id}
            raw_hash = _write_json(
                run_root / f"pages/{stream_name}/000001.json",
                {
                    "request": {"path": f"/channels/{target_id}/messages"},
                    "payload": [raw_message],
                    "pagination": {
                        "item_count": 1,
                        "next_cursor": None,
                        "terminal_status": "complete",
                    },
                },
            )
            wrong_message = {"id": "888888", "channel_id": target_id}
            wrong_evidence = extract_message_evidence(
                wrong_message,
                stream=stream_name,
                evidence_path=f"pages/{stream_name}/000001.json",
                evidence_sha256=raw_hash,
                json_pointer="/payload/0",
            )
            wrong_row = {"schema_version": 1, **asdict(wrong_evidence)}
            evidence_content = _canonical_bytes(wrong_row)
            evidence_path = run_root / f"message-evidence/{stream_name}/000001.jsonl"
            evidence_path.write_bytes(evidence_content)
            stream = checkpoint["streams"][stream_name]
            stream["page_hashes"] = [raw_hash]
            descriptor = stream["page_states"][0]["message_evidence"]
            descriptor.update(
                {
                    "sha256": _sha256_bytes(evidence_content),
                    "raw_page_sha256": raw_hash,
                    "root_messages": 1,
                    "partial_messages": 1,
                    "nodes": len(wrong_row["nodes"]),
                    "media_occurrences": len(wrong_row["media"]),
                    "references": len(wrong_row["references"]),
                    "diagnostics": len(wrong_row["diagnostics"]),
                }
            )
            manifest["streams"] = json.loads(json.dumps(checkpoint["streams"]))
            manifest["message_evidence"].update(
                {
                    "status": "partial",
                    "root_messages": 1,
                    "partial_messages": 1,
                    "nodes": len(wrong_row["nodes"]),
                    "media_occurrences": len(wrong_row["media"]),
                    "references": len(wrong_row["references"]),
                    "diagnostics": len(wrong_row["diagnostics"]),
                }
            )
            merge_request["shards"][0]["checkpoint_sha256"] = _write_json(
                run_root / "checkpoint.json",
                checkpoint,
            )
            merge_request["shards"][0]["manifest_sha256"] = _write_json(
                run_root / "manifest.json",
                manifest,
            )
            _write_json(workspace / "inputs/wrong-root-evidence.json", merge_request)

            result = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/wrong-root-evidence.json",
                output_path="audits/wrong-root-evidence.json",
            )
            audit = json.loads((workspace / "audits/wrong-root-evidence.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertTrue(
                any(
                    "deterministic extraction" in error and stream_name in error
                    for error in audit["validation_errors"]
                )
            )

    def test_merge_rejects_invalid_raw_message_and_pin_items(self) -> None:
        for prefix in ("messages", "pins"):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                _, _, merge_request = self._build_run_fixture(workspace)
                run_root = workspace / "runs/shard-1"
                checkpoint = json.loads((run_root / "checkpoint.json").read_text())
                manifest = json.loads((run_root / "manifest.json").read_text())
                stream_name = next(
                    name
                    for name in checkpoint["streams"]
                    if name.startswith(f"{prefix}_")
                )
                target_id = stream_name.removeprefix(f"{prefix}_")
                payload = (
                    [7]
                    if prefix == "messages"
                    else {
                        "items": [
                            {
                                "pinned_at": 7,
                                "message": {
                                    "id": "999999",
                                    "channel_id": target_id,
                                },
                            }
                        ]
                    }
                )
                raw_hash = _write_json(
                    run_root / f"pages/{stream_name}/000001.json",
                    {
                        "request": {"path": f"/channels/{target_id}/{prefix}"},
                        "payload": payload,
                        "pagination": {
                            "item_count": 1,
                            "next_cursor": None,
                            "terminal_status": "complete",
                        },
                    },
                )
                stream = checkpoint["streams"][stream_name]
                stream["page_hashes"] = [raw_hash]
                stream["page_states"][0]["message_evidence"][
                    "raw_page_sha256"
                ] = raw_hash
                manifest["streams"] = json.loads(json.dumps(checkpoint["streams"]))
                merge_request["shards"][0]["checkpoint_sha256"] = _write_json(
                    run_root / "checkpoint.json",
                    checkpoint,
                )
                merge_request["shards"][0]["manifest_sha256"] = _write_json(
                    run_root / "manifest.json",
                    manifest,
                )
                request_path = f"inputs/invalid-{prefix}-item.json"
                _write_json(workspace / request_path, merge_request)

                result = write_merged_shard_audit(
                    workspace=workspace,
                    targets_path="inputs/targets.json",
                    plan_path="plans/two/plan.json",
                    merge_request_path=request_path,
                    output_path=f"audits/invalid-{prefix}-item.json",
                )
                audit = json.loads(
                    (workspace / f"audits/invalid-{prefix}-item.json").read_text()
                )

                self.assertEqual(result["status"], "failed")
                self.assertTrue(
                    any(
                        "invalid raw" in error and stream_name in error
                        for error in audit["validation_errors"]
                    )
                )

    def test_merge_rejects_media_occurrence_without_asset_ledger_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _, _, merge_request = self._build_run_fixture(workspace)
            run_root = workspace / "runs/shard-1"
            checkpoint = json.loads((run_root / "checkpoint.json").read_text())
            manifest = json.loads((run_root / "manifest.json").read_text())
            stream_name = next(
                name for name in checkpoint["streams"] if name.startswith("messages_")
            )
            target_id = stream_name.removeprefix("messages_")
            logical_key = "999999:attachment:asset-missing"
            raw_hash = _write_json(
                run_root / f"pages/{stream_name}/000001.json",
                {
                    "request": {"path": f"/channels/{target_id}/messages"},
                    "payload": [
                        {
                            "id": "999999",
                            "channel_id": target_id,
                            "attachments": [{"id": "asset-missing"}],
                        }
                    ],
                    "pagination": {
                        "item_count": 1,
                        "next_cursor": None,
                        "terminal_status": "complete",
                    },
                },
            )
            evidence_path = run_root / f"message-evidence/{stream_name}/000001.jsonl"
            evidence_row = {
                "schema_version": 1,
                "status": "complete",
                "nodes": [],
                "media": [
                    {
                        "logical_key": logical_key,
                        "downloadable": True,
                        "source": {
                            "stream": stream_name,
                            "evidence_path": f"pages/{stream_name}/000001.json",
                            "evidence_sha256": raw_hash,
                        },
                    }
                ],
                "references": [],
                "diagnostics": [],
            }
            evidence_content = _canonical_bytes(evidence_row)
            evidence_path.write_bytes(evidence_content)
            stream = checkpoint["streams"][stream_name]
            stream["page_hashes"] = [raw_hash]
            descriptor = stream["page_states"][0]["message_evidence"]
            descriptor.update(
                {
                    "sha256": _sha256_bytes(evidence_content),
                    "raw_page_sha256": raw_hash,
                    "root_messages": 1,
                    "media_occurrences": 1,
                }
            )
            manifest["streams"] = json.loads(json.dumps(checkpoint["streams"]))
            manifest["message_evidence"]["root_messages"] = 1
            manifest["message_evidence"]["media_occurrences"] = 1
            merge_request["shards"][0]["checkpoint_sha256"] = _write_json(
                run_root / "checkpoint.json",
                checkpoint,
            )
            merge_request["shards"][0]["manifest_sha256"] = _write_json(
                run_root / "manifest.json",
                manifest,
            )
            _write_json(workspace / "inputs/missing-asset-key.json", merge_request)

            result = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/missing-asset-key.json",
                output_path="audits/missing-asset-key.json",
            )
            audit = json.loads((workspace / "audits/missing-asset-key.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertTrue(
                any(
                    logical_key in error and "asset ledger" in error
                    for error in audit["validation_errors"]
                )
            )

    def test_merge_transitively_rejects_missing_asset_blob(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(
                workspace,
                media_overrides={
                    1: {"status": "complete", "records": 1, "complete": 1, "failed": 0}
                },
            )
            blob = next((workspace / "runs/shard-1/assets/sha256").glob("*/*"))
            blob.unlink()

            result = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/merge-request.json",
                output_path="audits/missing-blob.json",
            )
            audit = json.loads((workspace / "audits/missing-blob.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertTrue(
                any("asset blob" in error for error in audit["validation_errors"])
            )

    def test_merge_transitively_rejects_missing_asset_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(
                workspace,
                media_overrides={
                    1: {"status": "complete", "records": 1, "complete": 1, "failed": 0}
                },
            )
            record = next((workspace / "runs/shard-1/asset-records").glob("*.json"))
            record.unlink()

            result = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/merge-request.json",
                output_path="audits/missing-record.json",
            )
            audit = json.loads((workspace / "audits/missing-record.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertTrue(
                any("asset record" in error for error in audit["validation_errors"])
            )

    def test_merge_accepts_fully_captured_media_with_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(
                workspace,
                media_overrides={
                    1: {
                        "status": "complete_with_warnings",
                        "records": 2,
                        "complete": 0,
                        "captured_with_warning": 1,
                        "reference_only": 1,
                        "binary_captured": 1,
                        "failed": 0,
                    }
                },
            )

            result = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/merge-request.json",
                output_path="audits/media-warnings.json",
            )
            self.assertEqual(result["status"], "complete")

    def test_merge_rejects_complete_manifest_when_media_has_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(
                workspace,
                media_overrides={
                    1: {
                        "status": "complete_with_warnings",
                        "records": 2,
                        "complete": 0,
                        "captured_with_warning": 1,
                        "reference_only": 1,
                        "binary_captured": 1,
                        "failed": 0,
                    }
                },
                manifest_status_overrides={1: "complete"},
            )

            audit = self._write_merge(
                workspace,
                "audits/media-warning-status-mismatch.json",
            )

            self.assertEqual(audit["status"], "failed")
            self.assertTrue(
                any(
                    "manifest status does not match derived state" in error
                    for error in audit["validation_errors"]
                )
            )

    def test_merge_rejects_warning_manifest_without_verified_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _, _, merge_request = self._build_run_fixture(workspace)
            shard = merge_request["shards"][0]
            assert isinstance(shard, dict)
            manifest_path = workspace / str(shard["run_root"]) / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["status"] = "complete_with_warnings"
            shard["manifest_sha256"] = _write_json(manifest_path, manifest)
            _write_json(workspace / "inputs/merge-request.json", merge_request)

            audit = self._write_merge(
                workspace,
                "audits/spurious-warning-status.json",
            )

            self.assertEqual(audit["status"], "failed")
            self.assertTrue(
                any(
                    "manifest status does not match derived state" in error
                    for error in audit["validation_errors"]
                )
            )

    def test_merge_accepts_finalized_nested_asset_kinds_and_embed_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            specs = [
                {
                    "logical_key": "3000000:attachment:asset-0",
                    "kind": "attachment",
                    "field": "attachment",
                    "declared_metadata": {"id": "asset-0"},
                },
                {
                    "logical_key": "snapshot:/0:embed:0:author_icon",
                    "kind": "embed",
                    "field": "author_icon",
                    "declared_metadata": {"icon_url": "https://cdn.example/1"},
                },
                {
                    "logical_key": "snapshot:/0:embed:0:footer_icon",
                    "kind": "embed",
                    "field": "footer_icon",
                    "declared_metadata": {"icon_url": "https://cdn.example/2"},
                },
                {
                    "logical_key": "3000003:component:/components/0/media",
                    "kind": "component",
                    "field": "media",
                    "declared_metadata": {"url": "https://cdn.example/3"},
                },
                {
                    "logical_key": "sticker:501",
                    "kind": "sticker",
                    "field": "sticker_items",
                    "declared_metadata": {"id": "501"},
                },
                {
                    "logical_key": "emoji:601",
                    "kind": "emoji",
                    "field": "poll_emoji",
                    "declared_metadata": {"id": "601"},
                },
            ]
            self._build_run_fixture(
                workspace,
                media_overrides={
                    1: {
                        "status": "complete",
                        "records": len(specs),
                        "complete": len(specs),
                        "binary_captured": len(specs),
                    }
                },
                asset_record_overrides={1: specs},
            )

            result = write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/merge-request.json",
                output_path="audits/nested-kinds.json",
            )
            self.assertEqual(result["status"], "complete")

    def test_joined_private_block_is_authorized_scope_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._build_run_fixture(
                workspace,
                stream_overrides={
                    1: {
                        f"threads_{_OXSUN_ROOT_ID}_joined_private_archived": {
                            "status": "blocked",
                            "terminal_reason": "forbidden",
                        }
                    }
                },
            )
            write_merged_shard_audit(
                workspace=workspace,
                targets_path="inputs/targets.json",
                plan_path="plans/two/plan.json",
                merge_request_path="inputs/merge-request.json",
                output_path="audits/joined-private.json",
            )
            audit = json.loads((workspace / "audits/joined-private.json").read_text())
            self.assertEqual(audit["private_archived_blocked_streams"], [])
            self.assertTrue(
                any(
                    item["stream"].endswith("_joined_private_archived")
                    for item in audit["non_private_incomplete_streams"]
                )
            )

    def test_closure_distinguishes_authorized_and_full_private_scope(self) -> None:
        t_close = "2026-07-19T12:00:00+00:00"
        merge = {
            "schema_version": 1,
            "audit_kind": "discord-parent-family-merge-v1",
            "status": "partial",
            "guild_id": "777777",
            "static_scope": {"exact_union": True, "pairwise_disjoint": True},
            "static_target_ids": ["100", "200"],
            "message_bearing_static_target_ids": ["100", "200"],
            "required_head_catchup_target_ids": ["100", "200", "300"],
            "discovered_threads": [
                {"id": "300", "parent_id": "100", "owner_index": 1}
            ],
            "private_archived_blocked_streams": [
                {"index": 1, "stream": "threads_100_private_archived"}
            ],
            # A blocked-private verdict must fail closed even if an older audit
            # omitted it from the broader private-incomplete rollup.
            "private_archived_incomplete_streams": [],
            "non_private_incomplete_streams": [],
            "media_incomplete_shards": [],
            "validation_errors": [],
        }
        census = {
            "schema_version": 1,
            "guild_id": "777777",
            "t_close": t_close,
            "t_close_source_sha256": _PREFLIGHT_SHA,
            "caught_through": "2026-07-19T12:00:01+00:00",
            "high_exclusive": _snowflake_lower_bound(
                "2026-07-19T12:00:01+00:00"
            ),
            "threads": [{"id": "300", "parent_id": "100"}],
        }
        target_evidence = {
            target_id: _head_catchup_target(
                target_id,
                guild_id="777777",
                t_close=t_close,
                caught_through="2026-07-19T12:00:01+00:00",
            )
            for target_id in ("100", "200", "300")
        }
        catchup = {
            "schema_version": 1,
            "guild_id": "777777",
            "t_close": t_close,
            "t_close_source_sha256": _PREFLIGHT_SHA,
            "caught_through": "2026-07-19T12:00:01+00:00",
            "high_exclusive": _snowflake_lower_bound(
                "2026-07-19T12:00:01+00:00"
            ),
            "required_target_ids": ["100", "200", "300"],
            "targets": [target_evidence[target_id][0] for target_id in ("100", "200", "300")],
        }
        verified = {
            target_id: _verified_head_target(descriptor, payload, raw_files)
            for target_id, (descriptor, payload, raw_files) in target_evidence.items()
        }

        audit = audit_closure(
            merge,
            census,
            catchup,
            t_close=t_close,
            verified_head_evidence=verified,
        )
        self.assertTrue(audit["authorized_scope_point_in_time_complete"])
        self.assertFalse(audit["full_private_scope_point_in_time_complete"])
        self.assertEqual(audit["t_close_source_sha256"], _PREFLIGHT_SHA)
        self.assertNotIn("unified_point_in_time_complete", audit)
        self.assertFalse(audit["shards_share_single_point_in_time"])
        self.assertEqual(audit["census_delta"], {
            "missing_from_merge": [],
            "missing_from_census": [],
        })
        self.assertEqual(audit["head_catchup_delta"]["missing_target_ids"], [])
        self.assertEqual(audit["head_catchup_delta"]["behind_t_close_target_ids"], [])

        unresolved_reference_merge = json.loads(json.dumps(merge))
        unresolved_reference_merge[
            "message_reference_incomplete_shards"
        ] = [
            {
                "index": 1,
                "status": "partial",
                "reason": "effective message reference errors remain",
            }
        ]
        unresolved_reference_audit = audit_closure(
            unresolved_reference_merge,
            census,
            catchup,
            t_close=t_close,
            verified_head_evidence=verified,
        )
        self.assertFalse(
            unresolved_reference_audit[
                "authorized_scope_point_in_time_complete"
            ]
        )
        self.assertEqual(
            unresolved_reference_audit["unresolved"][
                "message_reference_incomplete_shards"
            ],
            unresolved_reference_merge[
                "message_reference_incomplete_shards"
            ],
        )

        absent_from_current_census = json.loads(json.dumps(census))
        absent_from_current_census["threads"] = []
        absent_audit = audit_closure(
            merge,
            absent_from_current_census,
            catchup,
            t_close=t_close,
            verified_head_evidence=verified,
        )
        self.assertTrue(absent_audit["authorized_scope_point_in_time_complete"])
        self.assertEqual(absent_audit["census_delta"]["missing_from_census"], ["300"])
        self.assertEqual(
            absent_audit["limitations"]["current_census_absent_thread_ids"],
            ["300"],
        )

        for source_value in (None, "f" * 64):
            with self.subTest(t_close_source_sha256=source_value):
                unbound = json.loads(json.dumps(catchup))
                if source_value is None:
                    del unbound["t_close_source_sha256"]
                else:
                    unbound["t_close_source_sha256"] = source_value
                with self.assertRaisesRegex(ValueError, "t_close_source_sha256"):
                    audit_closure(
                        merge,
                        census,
                        unbound,
                        t_close=t_close,
                        verified_head_evidence=verified,
                    )

        mismatched_high = json.loads(json.dumps(catchup))
        mismatched_high["caught_through"] = "2026-07-19T12:00:02+00:00"
        mismatched_high["high_exclusive"] = _snowflake_lower_bound(
            "2026-07-19T12:00:02+00:00"
        )
        with self.assertRaisesRegex(ValueError, "one caught_through boundary"):
            audit_closure(
                merge,
                census,
                mismatched_high,
                t_close=t_close,
                verified_head_evidence=verified,
            )

        behind = json.loads(json.dumps(catchup))
        behind["targets"][0]["caught_through"] = "2026-07-19T11:59:59+00:00"
        behind_audit = audit_closure(
            merge,
            census,
            behind,
            t_close=t_close,
            verified_head_evidence=verified,
        )
        self.assertFalse(behind_audit["authorized_scope_point_in_time_complete"])
        self.assertFalse(behind_audit["full_private_scope_point_in_time_complete"])
        self.assertEqual(behind_audit["head_catchup_delta"]["behind_t_close_target_ids"], ["100"])

        nonzero_descriptor, nonzero_payload, nonzero_raw_files = _head_catchup_target(
            "100",
            guild_id="777777",
            t_close=t_close,
            caught_through="2026-07-19T12:00:01+00:00",
            new_message_ids=(str(int(_snowflake_lower_bound(t_close)) + 1),),
        )
        nonzero = json.loads(json.dumps(catchup))
        nonzero["targets"][0] = nonzero_descriptor
        nonzero_verified = dict(verified)
        nonzero_verified["100"] = _verified_head_target(
            nonzero_descriptor,
            nonzero_payload,
            nonzero_raw_files,
        )
        nonzero_audit = audit_closure(
            merge,
            census,
            nonzero,
            t_close=t_close,
            verified_head_evidence=nonzero_verified,
        )
        self.assertTrue(nonzero_audit["authorized_scope_point_in_time_complete"])
        self.assertEqual(
            nonzero_audit["captured_delta"]["message_target_ids"],
            ["100"],
        )
        self.assertEqual(nonzero_audit["unresolved"]["target_ids"], [])

        missing_explicit_zero = json.loads(json.dumps(catchup))
        del missing_explicit_zero["targets"][0]["new_message_count"]
        missing_audit = audit_closure(
            merge,
            census,
            missing_explicit_zero,
            t_close=t_close,
            verified_head_evidence=verified,
        )
        self.assertFalse(missing_audit["authorized_scope_point_in_time_complete"])
        self.assertEqual(
            missing_audit["head_catchup_delta"]["invalid_zero_delta_target_ids"],
            ["100"],
        )

        unverified_audit = audit_closure(merge, census, catchup, t_close=t_close)
        self.assertFalse(unverified_audit["authorized_scope_point_in_time_complete"])
        self.assertEqual(
            unverified_audit["head_catchup_delta"]["unverified_evidence_target_ids"],
            ["100", "200", "300"],
        )

        summary_only_catchup = json.loads(json.dumps(catchup))
        summary_only_payload = json.loads(json.dumps(target_evidence["100"][1]))
        del summary_only_payload["raw_pages"]
        summary_only_sha = canonical_json_sha256(summary_only_payload)
        summary_only_catchup["targets"][0]["evidence_sha256"] = summary_only_sha
        summary_only_verified = dict(verified)
        summary_only_verified["100"] = {
            "payload": summary_only_payload,
            "file_sha256": summary_only_sha,
        }
        summary_only_audit = audit_closure(
            merge,
            census,
            summary_only_catchup,
            t_close=t_close,
            verified_head_evidence=summary_only_verified,
        )
        self.assertFalse(
            summary_only_audit["authorized_scope_point_in_time_complete"]
        )
        self.assertEqual(
            summary_only_audit["head_catchup_delta"][
                "unverified_evidence_target_ids"
            ],
            ["100"],
        )

    def test_closure_rejects_invalid_reverse_pagination_content(self) -> None:
        t_close = "2026-07-19T12:00:00+00:00"
        caught_through = "2026-07-19T12:00:01+00:00"
        lower_bound = int(_snowflake_lower_bound(t_close))
        merge = {
            "schema_version": 1,
            "audit_kind": "discord-parent-family-merge-v1",
            "status": "complete",
            "guild_id": "1",
            "static_scope": {"exact_union": True, "pairwise_disjoint": True},
            "static_target_ids": ["100"],
            "message_bearing_static_target_ids": ["100"],
            "required_head_catchup_target_ids": ["100"],
            "discovered_threads": [],
            "private_archived_blocked_streams": [],
            "private_archived_incomplete_streams": [],
            "non_private_incomplete_streams": [],
            "media_incomplete_shards": [],
            "validation_errors": [],
        }
        census = {
            "schema_version": 1,
            "guild_id": "1",
            "t_close": t_close,
            "t_close_source_sha256": _PREFLIGHT_SHA,
            "caught_through": caught_through,
            "high_exclusive": _snowflake_lower_bound(caught_through),
            "threads": [],
        }

        def build_evidence():
            return _head_catchup_pages(
                "100",
                guild_id="1",
                t_close=t_close,
                caught_through=caught_through,
                message_pages=(
                    (str(lower_bound + 8), str(lower_bound + 6)),
                    (str(lower_bound + 4), str(lower_bound + 2)),
                    (),
                ),
                page_limit=2,
            )

        descriptor, evidence, raw_files = build_evidence()
        baseline = audit_closure(
            merge,
            census,
            {
                "schema_version": 1,
                "guild_id": "1",
                "t_close": t_close,
                "t_close_source_sha256": _PREFLIGHT_SHA,
                "caught_through": caught_through,
                "high_exclusive": _snowflake_lower_bound(caught_through),
                "required_target_ids": ["100"],
                "targets": [descriptor],
            },
            t_close=t_close,
            verified_head_evidence={
                "100": _verified_head_target(descriptor, evidence, raw_files)
            },
        )
        self.assertEqual(
            baseline["head_catchup_delta"]["unverified_evidence_target_ids"],
            [],
        )

        for case in (
            "duplicate",
            "out_of_order",
            "cursor_gap",
            "forward_after",
            "iso_before",
            "premature_terminal",
            "unaligned_caught",
            "high_bound_mismatch",
            "message_after_caught",
            "thread_after_caught",
        ):
            with self.subTest(case=case):
                descriptor, evidence, raw_files = build_evidence()
                paths = [item["path"] for item in evidence["raw_pages"]]
                first_response = raw_files[paths[0]]["response"]
                second_page = raw_files[paths[1]]
                if case == "duplicate":
                    second_page["response"]["messages"][1]["id"] = (
                        first_response["messages"][0]["id"]
                    )
                elif case == "out_of_order":
                    first_response["messages"].reverse()
                elif case == "cursor_gap":
                    gap_cursor = str(lower_bound + 7)
                    first_response["next_cursor"] = gap_cursor
                    second_page["request"]["params"]["before"] = gap_cursor
                elif case == "forward_after":
                    raw_files[paths[0]]["request"]["params"] = {
                        "after": str(lower_bound),
                        "limit": 2,
                    }
                elif case == "iso_before":
                    raw_files[paths[0]]["request"]["params"]["before"] = t_close
                elif case == "premature_terminal":
                    first_response["terminal"] = True
                    first_response["terminal_reason"] = "short_page"
                    first_response["next_cursor"] = None
                    evidence["raw_pages"] = evidence["raw_pages"][:1]
                    raw_files = {paths[0]: raw_files[paths[0]]}
                elif case == "unaligned_caught":
                    unaligned = "2026-07-19T12:00:01.000500+00:00"
                    descriptor["caught_through"] = unaligned
                    evidence["caught_through"] = unaligned
                    for raw_page in raw_files.values():
                        raw_page["caught_through"] = unaligned
                elif case == "high_bound_mismatch":
                    wrong_high = str(int(str(evidence["high_exclusive"])) + 1)
                    evidence["high_exclusive"] = wrong_high
                    raw_files[paths[0]]["request"]["params"]["before"] = wrong_high
                elif case == "message_after_caught":
                    second_page["response"]["messages"][0]["id"] = str(
                        int(_snowflake_lower_bound("2026-07-19T12:00:02+00:00"))
                        + 1
                    )
                else:
                    raw_files[paths[-1]]["response"]["threads"] = [
                        {
                            "id": str(
                                int(
                                    _snowflake_lower_bound(
                                        "2026-07-19T12:00:02+00:00"
                                    )
                                )
                                + 1
                            ),
                            "parent_id": "100",
                        }
                    ]
                verified = _recommit_head_target(descriptor, evidence, raw_files)
                audit = audit_closure(
                    merge,
                    census,
                    {
                        "schema_version": 1,
                        "guild_id": "1",
                        "t_close": t_close,
                        "t_close_source_sha256": _PREFLIGHT_SHA,
                        "caught_through": caught_through,
                        "high_exclusive": _snowflake_lower_bound(caught_through),
                        "required_target_ids": ["100"],
                        "targets": [descriptor],
                    },
                    t_close=t_close,
                    verified_head_evidence={"100": verified},
                )
                self.assertEqual(
                    audit["head_catchup_delta"][
                        "unverified_evidence_target_ids"
                    ],
                    ["100"],
                )

    def test_closure_captures_late_census_thread_but_blocks_historical_gap(self) -> None:
        t_close = "2026-07-19T12:00:00+00:00"
        caught_through = "2026-07-19T12:00:01+00:00"
        lower_bound = int(_snowflake_lower_bound(t_close))
        late_thread_id = str(lower_bound + 10)
        merge = {
            "schema_version": 1,
            "audit_kind": "discord-parent-family-merge-v1",
            "status": "complete",
            "guild_id": "1",
            "static_scope": {"exact_union": True, "pairwise_disjoint": True},
            "static_target_ids": ["100"],
            "message_bearing_static_target_ids": ["100"],
            "required_head_catchup_target_ids": ["100"],
            "discovered_threads": [],
            "private_archived_blocked_streams": [],
            "private_archived_incomplete_streams": [],
            "non_private_incomplete_streams": [],
            "media_incomplete_shards": [],
            "validation_errors": [],
        }

        def closure_for(thread_id: str, *, report_from_parent: bool):
            census = {
                "schema_version": 1,
                "guild_id": "1",
                "t_close": t_close,
                "t_close_source_sha256": _PREFLIGHT_SHA,
                "caught_through": caught_through,
                "high_exclusive": _snowflake_lower_bound(caught_through),
                "threads": [{"id": thread_id, "parent_id": "100"}],
            }
            parent = _head_catchup_target(
                "100",
                guild_id="1",
                t_close=t_close,
                caught_through=caught_through,
                new_thread_ids=(thread_id,) if report_from_parent else (),
            )
            thread = _head_catchup_target(
                thread_id,
                guild_id="1",
                t_close=t_close,
                caught_through=caught_through,
            )
            targets = {"100": parent, thread_id: thread}
            catchup = {
                "schema_version": 1,
                "guild_id": "1",
                "t_close": t_close,
                "t_close_source_sha256": _PREFLIGHT_SHA,
                "caught_through": caught_through,
                "high_exclusive": _snowflake_lower_bound(caught_through),
                "required_target_ids": sorted(targets, key=int),
                "targets": [targets[target_id][0] for target_id in sorted(targets, key=int)],
            }
            verified = {
                target_id: _verified_head_target(*targets[target_id])
                for target_id in targets
            }
            return audit_closure(
                merge,
                census,
                catchup,
                t_close=t_close,
                verified_head_evidence=verified,
            )

        captured = closure_for(late_thread_id, report_from_parent=True)
        self.assertTrue(captured["authorized_scope_point_in_time_complete"])
        self.assertEqual(captured["captured_delta"]["thread_ids"], [late_thread_id])
        self.assertEqual(
            captured["unresolved"]["historical_thread_ids_missing_from_merge"],
            [],
        )

        historical = closure_for("300", report_from_parent=False)
        self.assertFalse(historical["authorized_scope_point_in_time_complete"])
        self.assertEqual(
            historical["unresolved"]["historical_thread_ids_missing_from_merge"],
            ["300"],
        )

        at_high = closure_for(
            _snowflake_lower_bound(caught_through),
            report_from_parent=False,
        )
        self.assertFalse(at_high["authorized_scope_point_in_time_complete"])
        self.assertEqual(
            at_high["unresolved"]["census_thread_ids_at_or_after_high"],
            [_snowflake_lower_bound(caught_through)],
        )

    def test_closure_reverse_catchup_covers_more_than_one_hundred_messages(self) -> None:
        t_close = "2026-07-19T16:29:06Z"
        caught_through = "2026-07-19T16:29:07Z"
        lower_bound = int(_snowflake_lower_bound(t_close))
        expected_ids = [str(lower_bound + offset) for offset in range(205, 0, -1)]
        descriptor, evidence, raw_files = _head_catchup_pages(
            "100",
            guild_id="1",
            t_close=t_close,
            caught_through=caught_through,
            message_pages=(
                tuple(expected_ids[:100]),
                tuple(expected_ids[100:200]),
                (*expected_ids[200:], str(lower_bound)),
            ),
        )
        merge = {
            "schema_version": 1,
            "audit_kind": "discord-parent-family-merge-v1",
            "status": "complete",
            "guild_id": "1",
            "static_scope": {"exact_union": True, "pairwise_disjoint": True},
            "static_target_ids": ["100"],
            "message_bearing_static_target_ids": ["100"],
            "required_head_catchup_target_ids": ["100"],
            "discovered_threads": [],
            "private_archived_blocked_streams": [],
            "private_archived_incomplete_streams": [],
            "non_private_incomplete_streams": [],
            "media_incomplete_shards": [],
            "validation_errors": [],
        }
        census = {
            "schema_version": 1,
            "guild_id": "1",
            "t_close": t_close,
            "t_close_source_sha256": _PREFLIGHT_SHA,
            "caught_through": caught_through,
            "high_exclusive": _snowflake_lower_bound(caught_through),
            "threads": [],
        }

        audit = audit_closure(
            merge,
            census,
            {
                "schema_version": 1,
                "guild_id": "1",
                "t_close": t_close,
                "t_close_source_sha256": _PREFLIGHT_SHA,
                "caught_through": caught_through,
                "high_exclusive": _snowflake_lower_bound(caught_through),
                "required_target_ids": ["100"],
                "targets": [descriptor],
            },
            t_close=t_close,
            verified_head_evidence={
                "100": _verified_head_target(descriptor, evidence, raw_files)
            },
        )

        self.assertEqual(
            audit["head_catchup_delta"]["unverified_evidence_target_ids"],
            [],
        )
        self.assertTrue(audit["authorized_scope_point_in_time_complete"])
        self.assertEqual(
            audit["captured_delta"]["message_ids"],
            sorted(expected_ids, key=int),
        )
        self.assertEqual(audit["unresolved"]["target_ids"], [])
        self.assertEqual(
            audit["head_catchup_delta"]["new_message_ids"],
            sorted(expected_ids, key=int),
        )
        self.assertEqual(evidence["new_message_ids"], expected_ids)
        paths = [item["path"] for item in evidence["raw_pages"]]
        self.assertEqual(
            raw_files[paths[1]]["request"]["params"]["before"],
            expected_ids[99],
        )
        self.assertEqual(
            raw_files[paths[2]]["request"]["params"]["before"],
            expected_ids[199],
        )
        self.assertEqual(
            raw_files[paths[2]]["response"]["terminal_reason"],
            "crossed_lower_bound",
        )

    def test_closure_writer_is_contained_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            t_close = "2026-07-19T12:00:00+00:00"
            merge = {
                "schema_version": 1,
                "audit_kind": "discord-parent-family-merge-v1",
                "status": "complete",
                "guild_id": "1",
                "static_scope": {"exact_union": True, "pairwise_disjoint": True},
                "static_target_ids": ["100"],
                "message_bearing_static_target_ids": ["100"],
                "thread_parent_static_target_ids": [],
                "required_head_catchup_target_ids": ["100"],
                "discovered_threads": [],
                "private_archived_blocked_streams": [],
                "non_private_incomplete_streams": [],
                "media_incomplete_shards": [],
                "validation_errors": [],
            }
            merge_file_sha = canonical_json_sha256(merge)
            census_request = {
                "method": "GET",
                "path": "/guilds/1/threads/active",
                "params": {},
            }
            census_response = {
                "status_code": 200,
                "payload": {"threads": [], "members": []},
                "next_cursor": None,
                "terminal": True,
                "terminal_reason": "single_response",
            }
            census_raw = {
                "schema_version": 1,
                "audit_kind": "discord-thread-census-raw-page-v1",
                "guild_id": "1",
                "parent_id": None,
                "source": "active",
                "t_close": t_close,
                "t_close_source_sha256": _PREFLIGHT_SHA,
                "caught_through": "2026-07-19T12:00:01+00:00",
                "request": census_request,
                "response": census_response,
            }
            census_raw_path = "closure-evidence/raw/census/guild/active/000001.json"
            census = {
                "schema_version": 1,
                "audit_kind": "discord-thread-census-v1",
                "guild_id": "1",
                "t_close": t_close,
                "t_close_source_sha256": _PREFLIGHT_SHA,
                "merge_audit_file_sha256": merge_file_sha,
                "caught_through": "2026-07-19T12:00:01+00:00",
                "high_exclusive": _snowflake_lower_bound(
                    "2026-07-19T12:00:01+00:00"
                ),
                "family_parent_ids": [],
                "threads": [],
                "thread_sources": {},
                "raw_pages": [
                    {
                        "source": "active",
                        "parent_id": None,
                        "path": census_raw_path,
                        "sha256": canonical_json_sha256(census_raw),
                        "request_sha256": canonical_json_sha256(census_request),
                        "response_sha256": canonical_json_sha256(census_response),
                    }
                ],
                "limitations": {
                    "private_archived_403_parent_ids": [],
                    "full_private_archive_scope_complete": True,
                    "thread_state_observed_after_h_not_as_of_h": True,
                    "thread_id_before_h_only_constrains_creation_time": True,
                    "archive_delete_permission_race_after_h": True,
                    "excluded_thread_ids_at_or_after_h": [],
                    "pins_current_snapshot_non_as_of": True,
                    "pins_included_in_h_claim": False,
                },
            }
            descriptor, evidence, raw_files = _head_catchup_target(
                "100",
                guild_id="1",
                t_close=t_close,
                caught_through="2026-07-19T12:00:01+00:00",
            )
            catchup = {
                "schema_version": 1,
                "guild_id": "1",
                "t_close": t_close,
                "t_close_source_sha256": _PREFLIGHT_SHA,
                "merge_audit_file_sha256": merge_file_sha,
                "caught_through": "2026-07-19T12:00:01+00:00",
                "high_exclusive": _snowflake_lower_bound(
                    "2026-07-19T12:00:01+00:00"
                ),
                "required_target_ids": ["100"],
                "targets": [descriptor],
            }
            _write_json(workspace / "inputs/merge.json", merge)
            _write_json(workspace / "inputs/census.json", census)
            _write_json(workspace / census_raw_path, census_raw)
            _write_json(workspace / "inputs/catchup.json", catchup)
            _write_json(workspace / str(descriptor["evidence_path"]), evidence)
            for raw_path, raw_page in raw_files.items():
                _write_json(workspace / raw_path, raw_page)

            result = write_closure_audit(
                workspace=workspace,
                merge_audit_path="inputs/merge.json",
                census_path="inputs/census.json",
                head_catchup_path="inputs/catchup.json",
                output_path="audits/closure.json",
                t_close=t_close,
            )
            self.assertTrue(result["authorized_scope_point_in_time_complete"])
            self.assertTrue((workspace / "audits/closure.json").read_bytes().endswith(b"\n"))

            evidence_path = workspace / str(descriptor["evidence_path"])
            evidence_path.write_bytes(b"{}\n")
            tampered = write_closure_audit(
                workspace=workspace,
                merge_audit_path="inputs/merge.json",
                census_path="inputs/census.json",
                head_catchup_path="inputs/catchup.json",
                output_path="audits/tampered-closure.json",
                t_close=t_close,
            )
            self.assertFalse(tampered["authorized_scope_point_in_time_complete"])
            tampered_audit = json.loads(
                (workspace / "audits/tampered-closure.json").read_text()
            )
            self.assertEqual(
                tampered_audit["head_catchup_delta"][
                    "unverified_evidence_target_ids"
                ],
                ["100"],
            )

            _write_json(workspace / str(descriptor["evidence_path"]), evidence)
            raw_path = next(iter(raw_files))
            raw_page = json.loads(json.dumps(raw_files[raw_path]))
            raw_page["response"]["messages"] = [{"id": "999"}]
            _write_json(workspace / raw_path, raw_page)
            raw_tampered = write_closure_audit(
                workspace=workspace,
                merge_audit_path="inputs/merge.json",
                census_path="inputs/census.json",
                head_catchup_path="inputs/catchup.json",
                output_path="audits/raw-tampered-closure.json",
                t_close=t_close,
            )
            self.assertFalse(
                raw_tampered["authorized_scope_point_in_time_complete"]
            )
            raw_tampered_audit = json.loads(
                (workspace / "audits/raw-tampered-closure.json").read_text()
            )
            self.assertEqual(
                raw_tampered_audit["head_catchup_delta"][
                    "unverified_evidence_target_ids"
                ],
                ["100"],
            )

            (workspace / "escape").symlink_to(workspace / "inputs", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                write_closure_audit(
                    workspace=workspace,
                    merge_audit_path="escape/merge.json",
                    census_path="inputs/census.json",
                    head_catchup_path="inputs/catchup.json",
                    output_path="audits/rejected.json",
                    t_close=t_close,
                )


class DiscordClosureCaptureTests(unittest.TestCase):
    def _add_empty_archives(
        self,
        transport: _ScriptedClosureTransport,
        parent_id: str,
        *,
        private: object | None = None,
    ) -> None:
        empty = {"threads": [], "members": [], "has_more": False}
        transport.add(
            f"/channels/{parent_id}/threads/archived/public",
            empty,
            {"limit": 100},
        )
        transport.add(
            f"/channels/{parent_id}/threads/archived/private",
            empty if private is None else private,
            {"limit": 100},
        )
        transport.add(
            f"/channels/{parent_id}/users/@me/threads/archived/private",
            empty,
            {"limit": 100},
        )

    def test_active_threads_accept_parent_contiguous_descending_groups(self) -> None:
        t_close = "2026-07-19T12:00:00Z"
        caught_through = "2026-07-19T12:00:01.001Z"
        high_exclusive = _snowflake_lower_bound(caught_through)
        lower_bound = int(_snowflake_lower_bound(t_close))
        active_threads = [
            {"id": str(lower_bound + 3), "parent_id": "100"},
            {"id": str(lower_bound + 2), "parent_id": "100"},
            {"id": str(lower_bound + 4), "parent_id": "200"},
            {"id": str(lower_bound + 1), "parent_id": "200"},
        ]

        def payload(threads: list[dict[str, str]]) -> dict[str, object]:
            return {
                "threads": [
                    {"type": 11, "guild_id": "1", **thread}
                    for thread in threads
                ],
                "members": [],
            }

        snapshot = _closure_snapshot(
            {"id": "100", "name": "text", "kind": "GUILD_TEXT (0)"},
            {"id": "200", "name": "forum", "kind": "GUILD_FORUM (15)"},
        )
        merge = _closure_merge(snapshot, message_bearing_ids=("100",))

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _write_json(workspace / "inputs/targets.json", snapshot)
            _write_json(workspace / "inputs/merge.json", merge)
            transport = _ScriptedClosureTransport()
            transport.add("/guilds/1/threads/active", payload(active_threads))
            for parent_id in ("100", "200"):
                self._add_empty_archives(transport, parent_id)
            for target_id in ("100", *(thread["id"] for thread in active_threads)):
                transport.add(
                    f"/channels/{target_id}/messages",
                    [],
                    {"before": high_exclusive, "limit": 100},
                )

            result = discord_sharding_module.capture_closure_evidence(
                workspace=workspace,
                targets_path="inputs/targets.json",
                merge_audit_path="inputs/merge.json",
                output_dir="closure/accepted",
                t_close=t_close,
                t_close_source_sha256=_PREFLIGHT_SHA,
                transport=transport,
                clock=lambda: datetime(2026, 7, 19, 12, 0, 1, 1, tzinfo=timezone.utc),
            )
            transport.assert_exhausted(self)
            audit_result = write_closure_audit(
                workspace=workspace,
                merge_audit_path="inputs/merge.json",
                census_path=result["census_path"],
                head_catchup_path=result["head_catchup_path"],
                output_path="closure/accepted/audit.json",
                t_close=t_close,
            )
            self.assertTrue(audit_result["authorized_scope_point_in_time_complete"])
            audit = json.loads((workspace / "closure/accepted/audit.json").read_text())
            self.assertTrue(audit["census_evidence_verified"])

            invalid_shapes = {
                "same-parent ascending": [
                    active_threads[1],
                    active_threads[0],
                    active_threads[2],
                    active_threads[3],
                ],
                "non-contiguous parent": [
                    active_threads[2],
                    active_threads[0],
                    active_threads[1],
                    active_threads[3],
                ],
                "duplicate id": [
                    active_threads[0],
                    active_threads[1],
                    active_threads[0],
                ],
            }
            original_census = json.loads(
                (workspace / result["census_path"]).read_text()
            )
            original_descriptor = next(
                item for item in original_census["raw_pages"] if item["source"] == "active"
            )
            original_raw = json.loads(
                (workspace / original_descriptor["path"]).read_text()
            )
            for name, invalid_threads in invalid_shapes.items():
                with self.subTest(name=f"audit {name}"):
                    tampered_census = deepcopy(original_census)
                    descriptor = next(
                        item
                        for item in tampered_census["raw_pages"]
                        if item["source"] == "active"
                    )
                    tampered_raw = deepcopy(original_raw)
                    tampered_raw["response"]["payload"] = payload(invalid_threads)
                    descriptor["response_sha256"] = canonical_json_sha256(
                        tampered_raw["response"]
                    )
                    descriptor["sha256"] = canonical_json_sha256(tampered_raw)
                    _write_json(workspace / descriptor["path"], tampered_raw)
                    _write_json(workspace / result["census_path"], tampered_census)

                    audit_result = write_closure_audit(
                        workspace=workspace,
                        merge_audit_path="inputs/merge.json",
                        census_path=result["census_path"],
                        head_catchup_path=result["head_catchup_path"],
                        output_path=f"closure/accepted/{name}-audit.json",
                        t_close=t_close,
                    )
                    self.assertFalse(
                        audit_result["authorized_scope_point_in_time_complete"]
                    )
                    audit = json.loads(
                        (workspace / f"closure/accepted/{name}-audit.json").read_text()
                    )
                    self.assertFalse(audit["census_evidence_verified"])
                    self.assertIn(
                        "active thread census order is invalid",
                        audit["validation_errors"],
                    )

            for name, invalid_threads in invalid_shapes.items():
                with self.subTest(name=name):
                    invalid_transport = _ScriptedClosureTransport()
                    invalid_transport.add(
                        "/guilds/1/threads/active",
                        payload(invalid_threads),
                    )
                    with self.assertRaisesRegex(ValueError, "active thread census order"):
                        discord_sharding_module.capture_closure_evidence(
                            workspace=workspace,
                            targets_path="inputs/targets.json",
                            merge_audit_path="inputs/merge.json",
                            output_dir=f"closure/{name}",
                            t_close=t_close,
                            t_close_source_sha256=_PREFLIGHT_SHA,
                            transport=invalid_transport,
                            clock=lambda: datetime(
                                2026, 7, 19, 12, 0, 1, 1, tzinfo=timezone.utc
                            ),
                        )
                    invalid_transport.assert_exhausted(self)

    def test_capture_freezes_common_h_hash_pins_census_and_feeds_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            t_close = "2026-07-19T12:00:00Z"
            caught_through = "2026-07-19T12:00:01.001Z"
            high_exclusive = _snowflake_lower_bound(caught_through)
            late_thread_id = str(int(_snowflake_lower_bound(t_close)) + 10)
            joined_thread_id = str(int(_snowflake_lower_bound(t_close)) + 9)
            snapshot = _closure_snapshot(
                {"id": "100", "name": "text", "kind": "GUILD_TEXT (0)"},
                {"id": "200", "name": "forum", "kind": "GUILD_FORUM (15)"},
                {
                    "id": "300",
                    "name": "explicit-thread",
                    "kind": "GUILD_PUBLIC_THREAD (11)",
                    "parent_id": "100",
                },
            )
            merge = _closure_merge(
                snapshot,
                message_bearing_ids=("100", "300"),
                discovered_threads=(("300", "100"),),
            )
            _write_json(workspace / "inputs/targets.json", snapshot)
            merge_file_sha = _write_json(workspace / "inputs/merge.json", merge)

            transport = _ScriptedClosureTransport()
            transport.add(
                "/guilds/1/threads/active",
                {
                    "threads": [
                        {
                            "id": late_thread_id,
                            "type": 11,
                            "guild_id": "1",
                            "parent_id": "200",
                        }
                    ],
                    "members": [],
                },
            )
            transport.add(
                "/channels/100/threads/archived/public",
                {
                    "threads": [
                        {
                            "id": "300",
                            "type": 11,
                            "guild_id": "1",
                            "parent_id": "100",
                            "thread_metadata": {
                                "archive_timestamp": "2026-07-18T00:00:00Z"
                            },
                        }
                    ],
                    "members": [],
                    "has_more": True,
                },
                {"limit": 100},
            )
            transport.add(
                "/channels/100/threads/archived/public",
                {"threads": [], "members": [], "has_more": False},
                {"limit": 100, "before": "2026-07-18T00:00:00Z"},
            )
            transport.add(
                "/channels/100/threads/archived/private",
                DiscordAPIError(
                    "forbidden",
                    status_code=403,
                    path="/channels/100/threads/archived/private",
                ),
                {"limit": 100},
            )
            transport.add(
                "/channels/100/users/@me/threads/archived/private",
                {
                    "threads": [
                        {
                            "id": joined_thread_id,
                            "type": 12,
                            "guild_id": "1",
                            "parent_id": "100",
                        }
                    ],
                    "members": [],
                    "has_more": True,
                },
                {"limit": 100},
            )
            transport.add(
                "/channels/100/users/@me/threads/archived/private",
                {"threads": [], "members": [], "has_more": False},
                {"limit": 100, "before": joined_thread_id},
            )
            self._add_empty_archives(transport, "200")
            for target_id in ("100", "300", joined_thread_id, late_thread_id):
                transport.add(
                    f"/channels/{target_id}/messages",
                    [],
                    {"before": high_exclusive, "limit": 100},
                )

            result = discord_sharding_module.capture_closure_evidence(
                workspace=workspace,
                targets_path="inputs/targets.json",
                merge_audit_path="inputs/merge.json",
                output_dir="closure/run-1",
                t_close=t_close,
                t_close_source_sha256=_PREFLIGHT_SHA,
                transport=transport,
                clock=lambda: datetime(
                    2026,
                    7,
                    19,
                    12,
                    0,
                    1,
                    1,
                    tzinfo=timezone.utc,
                ),
            )

            self.assertEqual(result["caught_through"], caught_through)
            self.assertEqual(result["high_exclusive"], high_exclusive)
            census = json.loads((workspace / result["census_path"]).read_text())
            head = json.loads((workspace / result["head_catchup_path"]).read_text())
            self.assertEqual(census["merge_audit_file_sha256"], merge_file_sha)
            self.assertEqual(head["merge_audit_file_sha256"], merge_file_sha)
            self.assertEqual(
                census["threads"],
                [
                    {"id": "300", "parent_id": "100"},
                    {"id": joined_thread_id, "parent_id": "100"},
                    {"id": late_thread_id, "parent_id": "200"},
                ],
            )
            self.assertEqual(len(census["raw_pages"]), 9)
            self.assertEqual(
                census["limitations"]["private_archived_403_parent_ids"],
                ["100"],
            )
            self.assertTrue(
                census["limitations"]["thread_state_observed_after_h_not_as_of_h"]
            )
            self.assertFalse(census["limitations"]["pins_included_in_h_claim"])
            self.assertEqual(
                head["required_target_ids"],
                sorted(["100", "300", joined_thread_id, late_thread_id], key=int),
            )
            self.assertFalse(head["limitations"]["pins_included_in_h_claim"])
            transport.assert_exhausted(self)

            audit_result = write_closure_audit(
                workspace=workspace,
                merge_audit_path="inputs/merge.json",
                census_path=result["census_path"],
                head_catchup_path=result["head_catchup_path"],
                output_path="closure/run-1/audit.json",
                t_close=t_close,
            )
            self.assertTrue(audit_result["authorized_scope_point_in_time_complete"])
            self.assertFalse(audit_result["full_private_scope_point_in_time_complete"])
            audit = json.loads((workspace / "closure/run-1/audit.json").read_text())
            self.assertTrue(audit["census_evidence_verified"])

            changed_merge = dict(merge)
            changed_merge["post_capture_change"] = True
            _write_json(workspace / "inputs/merge.json", changed_merge)
            changed_merge_result = write_closure_audit(
                workspace=workspace,
                merge_audit_path="inputs/merge.json",
                census_path=result["census_path"],
                head_catchup_path=result["head_catchup_path"],
                output_path="closure/run-1/changed-merge-audit.json",
                t_close=t_close,
            )
            self.assertFalse(
                changed_merge_result["authorized_scope_point_in_time_complete"]
            )
            changed_merge_audit = json.loads(
                (workspace / "closure/run-1/changed-merge-audit.json").read_text()
            )
            self.assertIn(
                "closure evidence does not bind the merge audit file",
                changed_merge_audit["validation_errors"],
            )
            _write_json(workspace / "inputs/merge.json", merge)

            collision_transport = _ScriptedClosureTransport()
            with self.assertRaisesRegex(ValueError, "immutable output.*exists"):
                discord_sharding_module.capture_closure_evidence(
                    workspace=workspace,
                    targets_path="inputs/targets.json",
                    merge_audit_path="inputs/merge.json",
                    output_dir="closure/run-1",
                    t_close=t_close,
                    t_close_source_sha256=_PREFLIGHT_SHA,
                    transport=collision_transport,
                    clock=lambda: datetime(2026, 7, 19, 12, 0, 2, tzinfo=timezone.utc),
                )
            self.assertEqual(collision_transport.calls, [])

            active_descriptor = next(
                item for item in census["raw_pages"] if item["source"] == "active"
            )
            active_path = workspace / active_descriptor["path"]
            active_raw = json.loads(active_path.read_text())
            active_raw["response"]["payload"]["threads"] = [
                {"id": "10", "type": 11, "guild_id": "1", "parent_id": "999"},
                {"id": "20", "type": 11, "guild_id": "1", "parent_id": "999"},
            ]
            active_descriptor["response_sha256"] = canonical_json_sha256(
                active_raw["response"]
            )
            active_descriptor["sha256"] = canonical_json_sha256(active_raw)
            _write_json(active_path, active_raw)
            _write_json(workspace / result["census_path"], census)
            tampered = write_closure_audit(
                workspace=workspace,
                merge_audit_path="inputs/merge.json",
                census_path=result["census_path"],
                head_catchup_path=result["head_catchup_path"],
                output_path="closure/run-1/tampered-audit.json",
                t_close=t_close,
            )
            self.assertFalse(tampered["authorized_scope_point_in_time_complete"])
            tampered_audit = json.loads(
                (workspace / "closure/run-1/tampered-audit.json").read_text()
            )
            self.assertFalse(tampered_audit["census_evidence_verified"])
            self.assertTrue(
                any(
                    "active thread census order" in error
                    for error in tampered_audit["validation_errors"]
                )
            )

    def test_capture_reverse_paginates_more_than_one_hundred_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            t_close = "2026-07-19T12:00:00Z"
            caught_through = "2026-07-19T12:00:01.000Z"
            lower_bound = int(_snowflake_lower_bound(t_close))
            high_exclusive = _snowflake_lower_bound(caught_through)
            expected_ids = [str(lower_bound + offset) for offset in range(205, 0, -1)]
            snapshot = _closure_snapshot(
                {"id": "100", "name": "text", "kind": "GUILD_TEXT (0)"}
            )
            merge = _closure_merge(snapshot, message_bearing_ids=("100",))
            _write_json(workspace / "targets.json", snapshot)
            _write_json(workspace / "merge.json", merge)

            transport = _ScriptedClosureTransport()
            transport.add(
                "/guilds/1/threads/active",
                {"threads": [], "members": []},
            )
            self._add_empty_archives(transport, "100")
            pages = (
                expected_ids[:100],
                expected_ids[100:200],
                [*expected_ids[200:], str(lower_bound)],
            )
            before = high_exclusive
            for page in pages:
                transport.add(
                    "/channels/100/messages",
                    [{"id": message_id, "channel_id": "100"} for message_id in page],
                    {"before": before, "limit": 100},
                )
                before = min(page, key=int)

            result = discord_sharding_module.capture_closure_evidence(
                workspace=workspace,
                targets_path="targets.json",
                merge_audit_path="merge.json",
                output_dir="closure/many",
                t_close=t_close,
                t_close_source_sha256=_PREFLIGHT_SHA,
                transport=transport,
                clock=lambda: datetime(2026, 7, 19, 12, 0, 1, tzinfo=timezone.utc),
            )

            head = json.loads((workspace / result["head_catchup_path"]).read_text())
            descriptor = head["targets"][0]
            evidence = json.loads((workspace / descriptor["evidence_path"]).read_text())
            self.assertEqual(evidence["new_message_count"], 205)
            self.assertEqual(evidence["new_message_ids"], expected_ids)
            self.assertEqual(len(evidence["raw_pages"]), 3)
            raw_last = json.loads(
                (workspace / evidence["raw_pages"][-1]["path"]).read_text()
            )
            self.assertEqual(
                raw_last["response"]["terminal_reason"],
                "crossed_lower_bound",
            )
            transport.assert_exhausted(self)

    def test_capture_fails_closed_on_non_private_error_and_message_disorder(self) -> None:
        for case in ("public_403", "message_disorder"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                t_close = "2026-07-19T12:00:00Z"
                caught_through = "2026-07-19T12:00:01.000Z"
                high_exclusive = _snowflake_lower_bound(caught_through)
                lower_bound = int(_snowflake_lower_bound(t_close))
                snapshot = _closure_snapshot(
                    {"id": "100", "name": "text", "kind": "GUILD_TEXT (0)"}
                )
                merge = _closure_merge(snapshot, message_bearing_ids=("100",))
                _write_json(workspace / "targets.json", snapshot)
                _write_json(workspace / "merge.json", merge)
                transport = _ScriptedClosureTransport()
                transport.add(
                    "/guilds/1/threads/active",
                    {"threads": [], "members": []},
                )
                if case == "public_403":
                    transport.add(
                        "/channels/100/threads/archived/public",
                        DiscordAPIError("forbidden", status_code=403),
                        {"limit": 100},
                    )
                else:
                    self._add_empty_archives(transport, "100")
                    transport.add(
                        "/channels/100/messages",
                        [
                            {"id": str(lower_bound + 1), "channel_id": "100"},
                            {"id": str(lower_bound + 2), "channel_id": "100"},
                        ],
                        {"before": high_exclusive, "limit": 100},
                    )

                with self.assertRaises((DiscordAPIError, ValueError)):
                    discord_sharding_module.capture_closure_evidence(
                        workspace=workspace,
                        targets_path="targets.json",
                        merge_audit_path="merge.json",
                        output_dir=f"closure/{case}",
                        t_close=t_close,
                        t_close_source_sha256=_PREFLIGHT_SHA,
                        transport=transport,
                        clock=lambda: datetime(
                            2026,
                            7,
                            19,
                            12,
                            0,
                            1,
                            tzinfo=timezone.utc,
                        ),
                    )
                self.assertFalse((workspace / f"closure/{case}").exists())

    def test_capture_requires_merge_bound_thread_parent_scope_before_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            snapshot = _closure_snapshot(
                {"id": "100", "name": "text", "kind": "GUILD_TEXT (0)"}
            )
            merge = _closure_merge(snapshot, message_bearing_ids=("100",))
            del merge["thread_parent_static_target_ids"]
            _write_json(workspace / "targets.json", snapshot)
            _write_json(workspace / "merge.json", merge)
            transport = _ScriptedClosureTransport()

            with self.assertRaisesRegex(ValueError, "thread-parent scope"):
                discord_sharding_module.capture_closure_evidence(
                    workspace=workspace,
                    targets_path="targets.json",
                    merge_audit_path="merge.json",
                    output_dir="closure/missing-scope",
                    t_close="2026-07-19T12:00:00Z",
                    t_close_source_sha256=_PREFLIGHT_SHA,
                    transport=transport,
                    clock=lambda: datetime(2026, 7, 19, 12, 0, 1, tzinfo=timezone.utc),
                )
            self.assertEqual(transport.calls, [])

    def test_capture_rejects_underdeclared_message_scope_before_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            snapshot = _closure_snapshot(
                {"id": "100", "name": "first", "kind": "GUILD_TEXT (0)"},
                {"id": "200", "name": "second", "kind": "GUILD_TEXT (0)"},
            )
            merge = _closure_merge(snapshot, message_bearing_ids=("100",))
            _write_json(workspace / "targets.json", snapshot)
            _write_json(workspace / "merge.json", merge)
            transport = _ScriptedClosureTransport()

            with self.assertRaisesRegex(ValueError, "message-bearing scope"):
                discord_sharding_module.capture_closure_evidence(
                    workspace=workspace,
                    targets_path="targets.json",
                    merge_audit_path="merge.json",
                    output_dir="closure/underdeclared",
                    t_close="2026-07-19T12:00:00Z",
                    t_close_source_sha256=_PREFLIGHT_SHA,
                    transport=transport,
                    clock=lambda: datetime(2026, 7, 19, 12, 0, 1, tzinfo=timezone.utc),
                )

            self.assertEqual(transport.calls, [])
            self.assertFalse((workspace / "closure/underdeclared").exists())

    def test_directory_publication_is_atomic_and_no_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / ".capture-staging"
            destination = root / "capture"
            staging.mkdir()
            (staging / "evidence.json").write_text("{}\n")
            destination.mkdir()

            with self.assertRaises(FileExistsError):
                discord_sharding_module._rename_directory_noreplace(
                    staging,
                    destination,
                )

            self.assertTrue(staging.is_dir())
            self.assertTrue(destination.is_dir())
            self.assertEqual(list(destination.iterdir()), [])


class DiscordShardingOperationTests(unittest.TestCase):
    def test_cli_registers_closure_capture_as_local_write_with_token_path_only(self) -> None:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command", required=True)
        discord.register(subparsers)
        args = parser.parse_args(
            [
                "discord-shard-closure-capture",
                "--targets",
                "targets.json",
                "--merge-audit",
                "merge.json",
                "--output-dir",
                "closure/run-1",
                "--t-close",
                "2026-07-19T12:00:00Z",
                "--t-close-source-sha256",
                _PREFLIGHT_SHA,
                "--token-file",
                "/safe/token-file",
            ]
        )
        runner = _CaptureRunner()

        with contextlib.redirect_stdout(io.StringIO()):
            return_code = discord.COMMANDS[args.command](
                args,
                runner=runner,
                workspace=Path("."),
            )

        self.assertEqual(return_code, 0)
        self.assertEqual(len(runner.specs), 1)
        spec = runner.specs[0]
        self.assertEqual(spec.name, "discord_shard_closure_capture")
        self.assertIs(spec.risk_level, RiskLevel.LOCAL_WRITE)
        self.assertEqual(spec.payload["token_file"], "/safe/token-file")
        self.assertNotIn("token", spec.payload)
        self.assertEqual(spec.payload["t_close_source_sha256"], _PREFLIGHT_SHA)

    def test_closure_capture_handler_reads_token_inside_process_and_redacts_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "targets.json").write_text("{}\n")
            (workspace / "merge.json").write_text("{}\n")
            secret = "top-secret-discord-token"
            transport = object()
            captured: dict[str, object] = {}

            def fake_capture(**kwargs):
                captured.update(kwargs)
                return {
                    "status": "captured",
                    "output_dir": "closure/run-1",
                    "census_path": "closure/run-1/census.json",
                    "head_catchup_path": "closure/run-1/head-catchup.json",
                }

            with (
                mock.patch(
                    "omni_hub.connectors.discord.read_bot_token",
                    return_value=secret,
                ) as token_reader,
                mock.patch(
                    "omni_hub.connectors.discord.DiscordHTTPTransport",
                    return_value=transport,
                ) as transport_factory,
                mock.patch(
                    "omni_hub.discord_sharding.capture_closure_evidence",
                    side_effect=fake_capture,
                ),
            ):
                handler = builtins_module.make_discord_shard_closure_capture(workspace)
                result = handler(
                    OperationSpec(
                        name="discord_shard_closure_capture",
                        action="capture_t_close_closure",
                        connector="discord",
                        payload={
                            "targets": "targets.json",
                            "merge_audit": "merge.json",
                            "output_dir": "closure/run-1",
                            "t_close": "2026-07-19T12:00:00Z",
                            "t_close_source_sha256": _PREFLIGHT_SHA,
                            "token_file": "/safe/token-file",
                        },
                        risk_level=RiskLevel.LOCAL_WRITE,
                    )
                )

            token_reader.assert_called_once_with(Path("/safe/token-file"))
            transport_factory.assert_called_once_with(secret)
            self.assertIs(captured["transport"], transport)
            self.assertEqual(captured["workspace"], workspace.resolve())
            self.assertNotIn(secret, repr(result))
            self.assertNotIn(secret, repr(captured))

    def test_cli_registers_three_local_write_operations_without_token_arguments(self) -> None:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command", required=True)
        discord.register(subparsers)

        runner = _CaptureRunner()
        cases = [
            [
                "discord-shard-plan",
                "--targets",
                "targets.json",
                "--output-dir",
                "plans/four",
            ],
            [
                "discord-shard-merge-audit",
                "--targets",
                "targets.json",
                "--plan",
                "plans/four/plan.json",
                "--merge-request",
                "merge-request.json",
                "--output",
                "audits/merge.json",
            ],
            [
                "discord-shard-closure-audit",
                "--merge-audit",
                "audits/merge.json",
                "--census",
                "census.json",
                "--head-catchup",
                "head.json",
                "--t-close",
                "2026-07-19T12:00:00+00:00",
                "--output",
                "audits/closure.json",
            ],
        ]
        with contextlib.redirect_stdout(io.StringIO()):
            for argv in cases:
                args = parser.parse_args(argv)
                self.assertFalse(hasattr(args, "token_file"))
                discord.COMMANDS[args.command](
                    args,
                    runner=runner,
                    workspace=Path("."),
                )

        self.assertEqual(
            [spec.name for spec in runner.specs],
            [
                "discord_shard_plan",
                "discord_shard_merge_audit",
                "discord_shard_closure_audit",
            ],
        )
        self.assertTrue(all(spec.risk_level is RiskLevel.LOCAL_WRITE for spec in runner.specs))
        self.assertTrue(all("token" not in spec.payload for spec in runner.specs))

    def test_default_registry_runs_plan_through_operation_runner_without_token_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _write_json(
                workspace / "targets.json",
                _snapshot(root_count=4, thread_count=1),
            )
            runner = OperationRunner(build_default_registry(workspace))
            self.assertTrue(
                {
                    "discord_shard_plan",
                    "discord_shard_merge_audit",
                    "discord_shard_closure_audit",
                    "discord_shard_closure_capture",
                }
                <= set(runner.registry.list_names())
            )
            with mock.patch(
                "omni_hub.connectors.discord.read_bot_token",
                side_effect=AssertionError("token must not be read"),
            ):
                result = runner.run(
                    OperationSpec(
                        name="discord_shard_plan",
                        action="plan",
                        connector="discord",
                        payload={
                            "targets": "targets.json",
                            "output_dir": "plans/two",
                            "shard_count": 2,
                            "weights": None,
                        },
                        risk_level=RiskLevel.LOCAL_WRITE,
                    )
                )
            self.assertEqual(result.status, OperationStatus.SUCCEEDED)
            self.assertEqual(result.output["shard_count"], 2)
            self.assertTrue((workspace / "plans/two/plan.json").is_file())


if __name__ == "__main__":
    unittest.main()
