from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from omni_hub.discord_candidate_media import (
    build_candidate_media_manifest,
    iter_ocr_input_rows,
)
from omni_hub.discord_message_evidence import extract_message_evidence
from omni_hub.discord_sharding import canonical_json_bytes, canonical_json_sha256


PNG = b"\x89PNG\r\n\x1a\n" + b"fixture-image"
MP4 = b"\x00\x00\x00\x18ftypisom" + b"fixture-video"


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_json(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = canonical_json_bytes(value)
    path.write_bytes(content)
    return _sha(content)


def _write_jsonl(path: Path, values: list[dict[str, object]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = b"".join(canonical_json_bytes(value) for value in values)
    path.write_bytes(content)
    return _sha(content)


def _attachment_message(
    message_id: str,
    *,
    content_type: str,
    filename: str,
) -> dict[str, object]:
    attachment_id = str(int(message_id) + 10_000)
    return {
        "id": message_id,
        "channel_id": "10",
        "timestamp": "2026-07-20T00:00:00+00:00",
        "edited_timestamp": None,
        "content": f"PRIVATE-BODY-{message_id}",
        "author": {"id": "900", "username": "private-author"},
        "attachments": [
            {
                "id": attachment_id,
                "filename": filename,
                "size": len(PNG if content_type.startswith("image/") else MP4),
                "content_type": content_type,
                "url": f"https://cdn.example/{message_id}?signed=secret",
                "proxy_url": f"https://proxy.example/{message_id}?signed=secret",
                "width": 64,
                "height": 64,
            }
        ],
        "embeds": [],
    }


def _reference_message(message_id: str) -> dict[str, object]:
    return {
        "id": message_id,
        "channel_id": "10",
        "timestamp": "2026-07-20T00:00:00+00:00",
        "edited_timestamp": None,
        "content": f"PRIVATE-BODY-{message_id}",
        "author": {"id": "900", "username": "private-author"},
        "attachments": [],
        "embeds": [
            {
                "type": "video",
                "video": {
                    "url": "https://www.youtube.com/embed/abcdefghijk",
                    "width": 640,
                    "height": 360,
                },
            }
        ],
    }


def _source(
    *, message_id: str, raw_relative: str, raw_sha256: str, pointer: str
) -> dict[str, object]:
    return {
        "stream": "messages_10",
        "evidence_path": raw_relative,
        "evidence_sha256": raw_sha256,
        "root_message_id": message_id,
        "root_channel_id": "10",
        "node_key": f"message:10:{message_id}",
        "json_pointer": pointer,
    }


def _record(
    *,
    message: dict[str, object],
    raw_relative: str,
    raw_sha256: str,
    index: int,
    run_root: Path,
    status: str,
    blob: bytes | None = None,
    http_content_type: str | None = None,
) -> dict[str, object]:
    message_id = str(message["id"])
    if message.get("attachments"):
        metadata = deepcopy(message["attachments"][0])  # type: ignore[index]
        attachment_id = str(metadata["id"])
        logical_key = f"{message_id}:attachment:{attachment_id}"
        kind, field = "attachment", "attachment"
        pointer = f"/response/messages/{index}/attachments/0"
        declared = str(metadata["content_type"])
        identity = {
            "id": attachment_id,
            "size": metadata["size"],
            "content_type": declared,
        }
        url = str(metadata["url"])
        proxy_url = str(metadata["proxy_url"])
    else:
        metadata = deepcopy(message["embeds"][0]["video"])  # type: ignore[index]
        logical_key = f"{message_id}:embed:0:video"
        kind, field = "embed", "video"
        pointer = f"/response/messages/{index}/embeds/0/video"
        declared = None
        identity = {"width": 640, "height": 360}
        url = str(metadata["url"])
        proxy_url = None
    source = _source(
        message_id=message_id,
        raw_relative=raw_relative,
        raw_sha256=raw_sha256,
        pointer=pointer,
    )
    actual_bytes = len(blob) if blob is not None else 0
    digest = _sha(blob) if blob is not None else None
    blob_path = None
    if blob is not None:
        extension = {
            "image/png": "png",
            "video/mp4": "mp4",
        }.get(str(http_content_type), "png")
        blob_path = f"assets/sha256/{digest[:2]}/{digest}.{extension}"
        destination = run_root / blob_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(blob)
    if status == "complete":
        terminal_reason = "downloaded"
    elif status == "failed":
        terminal_reason = "download_http_404"
    elif status == "reference_only":
        terminal_reason = "youtube_embed_player_reference"
    else:
        raise AssertionError(status)
    outcome = {
        "url": url,
        "status": status,
        "terminal_reason": terminal_reason,
        "failure_detail": None,
        "http_content_type": http_content_type,
        "http_content_length": actual_bytes if blob is not None else None,
        "actual_bytes": actual_bytes,
        "sha256": digest,
        "blob_path": blob_path,
    }
    return {
        "schema_version": 4,
        "logical_key": logical_key,
        "kind": kind,
        "field": field,
        "url": url,
        "candidate_urls": [url] + ([proxy_url] if proxy_url else []),
        "observed_urls": [url] + ([proxy_url] if proxy_url else []),
        "sources": [source],
        "observations": [
            {
                "url": url,
                "proxy_url": proxy_url,
                "source": source,
                "metadata": metadata,
            }
        ],
        "declared_metadata": metadata,
        "declared_content_type": declared,
        "identity_metadata": identity,
        "identity_conflicts": [],
        **outcome,
        "attempt_history": [outcome],
    }


@dataclass
class _Fixture:
    root: Path
    closure_relative: Path
    closure_sha256: str
    candidate_ids: list[str]
    run_relative: Path
    captured_blob_relative: Path

    @property
    def source_hashes(self) -> dict[str, str]:
        return {self.closure_relative.as_posix(): self.closure_sha256}


def _fixture(
    root: Path,
    *,
    duplicate_first_snapshot: bool = False,
    captured_http_mime: str = "image/png",
    captured_blob: bytes = PNG,
    captured_declared_size: int | None = None,
) -> _Fixture:
    namespace = Path("closure/full-pinned-media-example")
    capture = namespace / "capture"
    run_relative = Path("runs/media-shard-1")
    run_root = root / run_relative
    raw_relative = capture / "raw/head/10-000001.json"
    messages = [
        _attachment_message("101", content_type="image/png", filename="secret.png"),
        _attachment_message("102", content_type="video/mp4", filename="secret.mp4"),
        _attachment_message("103", content_type="image/png", filename="secret.png"),
        _reference_message("104"),
        _attachment_message("105", content_type="image/png", filename="secret.png"),
    ]
    if captured_declared_size is not None:
        messages[0]["attachments"][0]["size"] = captured_declared_size  # type: ignore[index]
    raw_sha = _write_json(root / raw_relative, {"response": {"messages": messages}})
    raw_pages = [{"path": raw_relative.as_posix(), "sha256": raw_sha}]
    if duplicate_first_snapshot:
        duplicate_relative = capture / "raw/head/10-000002.json"
        duplicate_sha = _write_json(
            root / duplicate_relative,
            {"response": {"messages": [messages[0]]}},
        )
        raw_pages.append(
            {"path": duplicate_relative.as_posix(), "sha256": duplicate_sha}
        )

    candidate_ids = [str(message["id"]) for message in messages]
    target_evidence_relative = capture / "evidence/head/10.json"
    target_evidence = {
        "audit_kind": "discord-head-catchup-target-v1",
        "schema_version": 1,
        "target_id": "10",
        "new_message_count": len(candidate_ids),
        "new_message_ids": candidate_ids,
        "raw_pages": raw_pages,
    }
    target_evidence_sha = _write_json(
        root / target_evidence_relative, target_evidence
    )
    head = {
        "schema_version": 1,
        "targets": [
            {
                "id": "10",
                "evidence_path": target_evidence_relative.as_posix(),
                "evidence_sha256": target_evidence_sha,
                "new_message_count": len(candidate_ids),
                "new_message_ids": candidate_ids,
                "new_thread_count": 0,
                "new_thread_ids": [],
            }
        ],
    }
    head_sha = _write_json(root / capture / "head-catchup.json", head)

    records = [
        _record(
            message=messages[0],
            raw_relative=raw_relative.as_posix(),
            raw_sha256=raw_sha,
            index=0,
            run_root=run_root,
            status="complete",
            blob=captured_blob,
            http_content_type=captured_http_mime,
        ),
        _record(
            message=messages[1],
            raw_relative=raw_relative.as_posix(),
            raw_sha256=raw_sha,
            index=1,
            run_root=run_root,
            status="complete",
            blob=MP4,
            http_content_type="video/mp4",
        ),
        _record(
            message=messages[2],
            raw_relative=raw_relative.as_posix(),
            raw_sha256=raw_sha,
            index=2,
            run_root=run_root,
            status="failed",
        ),
        _record(
            message=messages[3],
            raw_relative=raw_relative.as_posix(),
            raw_sha256=raw_sha,
            index=3,
            run_root=run_root,
            status="reference_only",
        ),
        # Irrelevant ledger row: a valid relation, but not a candidate message.
        _record(
            message=_attachment_message(
                "999", content_type="image/png", filename="irrelevant.png"
            ),
            raw_relative="unrelated/page.json",
            raw_sha256="f" * 64,
            index=0,
            run_root=run_root,
            status="failed",
        ),
    ]
    asset_index_sha = _write_jsonl(run_root / "asset-index.jsonl", records)
    request_sha = _write_json(run_root / "request.json", {"run_id": "media-shard-1"})
    manifest_sha = _write_json(run_root / "manifest.json", {"run_id": "media-shard-1"})
    checkpoint_sha = _write_json(
        run_root / "checkpoint.json", {"run_id": "media-shard-1", "streams": {}}
    )
    inventory_sha = _write_json(run_root / "inventory/targets.json", {"targets": []})
    artifact_values = {
        "request": request_sha,
        "manifest": manifest_sha,
        "checkpoint": checkpoint_sha,
        "targets_inventory": inventory_sha,
    }
    artifacts = {
        key: {"expected": value, "actual": value, "verified": True}
        for key, value in artifact_values.items()
    }
    merge_request = {
        "schema_version": 1,
        "shards": [
            {
                "index": 1,
                "run_root": run_relative.as_posix(),
                **{f"{key}_sha256": value for key, value in artifact_values.items()},
            }
        ],
    }
    merge_request_sha = _write_json(root / namespace / "merge-request.json", merge_request)
    merge = {
        "audit_kind": "discord-parent-family-merge-v1",
        "schema_version": 1,
        "merge_request_sha256": merge_request_sha,
        "validation_errors": [],
        "static_target_ids": ["10"],
        "static_scope": {"exact_union": True, "pairwise_disjoint": True},
        "non_private_incomplete_streams": [],
        "failed_streams": [],
        "truncated_streams": [],
        "message_reference_incomplete_shards": [],
        "artifact_hashes": {"1": artifacts},
        "artifact_hash_verification": {
            "1": {key: True for key in artifacts}
        },
        "transitive_evidence": {
            "1": {
                "validation_errors": [],
                "asset_evidence": {
                    "asset_index_sha256": asset_index_sha,
                    "validation_errors": [],
                },
            }
        },
    }
    merge_sha = _write_json(root / namespace / "merge-audit.json", merge)
    closure = {
        "audit_kind": "discord-parent-family-closure-v1",
        "schema_version": 1,
        "validation_errors": [],
        "captured_delta": {"message_ids": candidate_ids},
        "unresolved": {
            "target_ids": [],
            "missing_target_ids": [],
            "unexpected_target_ids": [],
            "invalid_delta_target_ids": [],
            "unverified_evidence_target_ids": [],
            "non_private_incomplete_streams": [],
            "message_reference_incomplete_shards": [],
        },
        "input_file_sha256": {
            "merge_audit": merge_sha,
            "head_catchup": head_sha,
        },
        "input_canonical_sha256": {
            "merge_audit": canonical_json_sha256(merge),
            "head_catchup": canonical_json_sha256(head),
        },
    }
    closure_relative = capture / "closure-audit.json"
    closure_sha = _write_json(root / closure_relative, closure)
    captured_path = Path(str(records[0]["blob_path"]))
    return _Fixture(
        root,
        closure_relative,
        closure_sha,
        candidate_ids,
        run_relative,
        run_relative / captured_path,
    )


def _add_baseline_page(
    fixture: _Fixture,
    messages: list[dict[str, object]],
) -> tuple[str, str]:
    """Add one fully bound baseline page and return its evidence path/hash."""

    stream = "messages_10"
    raw_run_relative = Path("pages/messages_10/000001.json")
    raw_root_relative = fixture.run_relative / raw_run_relative
    raw = {"payload": messages}
    raw_sha = _write_json(fixture.root / raw_root_relative, raw)
    evidence_rows: list[dict[str, object]] = []
    for index, message in enumerate(messages):
        extracted = asdict(
            extract_message_evidence(
                message,
                stream=stream,
                evidence_path=raw_run_relative.as_posix(),
                evidence_sha256=raw_sha,
                json_pointer=f"/payload/{index}",
            )
        )
        evidence_rows.append(
            {
                "schema_version": 2,
                "stream": stream,
                "channel_id": "10",
                "page_number": 1,
                "message_json_pointer": f"/payload/{index}",
                **extracted,
            }
        )
    evidence_run_relative = Path("message-evidence/messages_10/000001.jsonl")
    evidence_sha = _write_jsonl(
        fixture.root / fixture.run_relative / evidence_run_relative,
        evidence_rows,
    )
    descriptor = {
        "schema_version": 2,
        "path": evidence_run_relative.as_posix(),
        "sha256": evidence_sha,
        "raw_page_path": raw_run_relative.as_posix(),
        "raw_page_sha256": raw_sha,
        "stream": stream,
        "channel_id": "10",
        "page_number": 1,
        "root_messages": len(messages),
        "partial_messages": 0,
        "nodes": sum(len(row["nodes"]) for row in evidence_rows),
        "media_occurrences": sum(len(row["media"]) for row in evidence_rows),
        "references": sum(len(row["references"]) for row in evidence_rows),
        "diagnostics": sum(len(row["diagnostics"]) for row in evidence_rows),
        "diagnostics_by_severity": {"error": 0, "warning": 0, "info": 0},
        "pin_events": 0,
        "fetched_at": "2026-07-20T00:00:01+00:00",
    }
    checkpoint_path = fixture.root / fixture.run_relative / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["streams"] = {
        stream: {
            "status": "complete",
            "page_hashes": [raw_sha],
            "page_states": [{"message_evidence": descriptor}],
        }
    }
    checkpoint_sha = _write_json(checkpoint_path, checkpoint)

    namespace = fixture.closure_relative.parents[1]
    request_path = fixture.root / namespace / "merge-request.json"
    request = json.loads(request_path.read_text())
    request["shards"][0]["checkpoint_sha256"] = checkpoint_sha
    request_sha = _write_json(request_path, request)
    merge_path = fixture.root / namespace / "merge-audit.json"
    merge = json.loads(merge_path.read_text())
    merge["merge_request_sha256"] = request_sha
    merge["artifact_hashes"]["1"]["checkpoint"] = {
        "expected": checkpoint_sha,
        "actual": checkpoint_sha,
        "verified": True,
    }
    merge_sha = _write_json(merge_path, merge)
    closure_path = fixture.root / fixture.closure_relative
    closure = json.loads(closure_path.read_text())
    closure["input_file_sha256"]["merge_audit"] = merge_sha
    closure["input_canonical_sha256"]["merge_audit"] = canonical_json_sha256(merge)
    fixture.closure_sha256 = _write_json(closure_path, closure)
    return raw_run_relative.as_posix(), raw_sha


def _typed_pending_record(
    *,
    message: dict[str, object],
    raw_relative: str,
    raw_sha256: str,
    index: int,
    run_root: Path,
    interrupted: bool,
) -> dict[str, object]:
    record = _record(
        message=message,
        raw_relative=raw_relative,
        raw_sha256=raw_sha256,
        index=index,
        run_root=run_root,
        status="failed",
    )
    attempt_status = "interrupted" if interrupted else "in_progress"
    reason = "interrupted" if interrupted else None
    attempt = {
        "url": record["url"],
        "status": attempt_status,
        "terminal_reason": reason,
        "failure_detail": None,
        "http_content_type": None,
        "http_content_length": None,
        "actual_bytes": 0,
        "sha256": None,
        "blob_path": None,
        "policy_inputs_sha256": "a" * 64,
        "resolution_retry_sequence": 1,
    }
    record.update(attempt)
    record["status"] = "in_progress"
    record["attempt_history"] = [attempt]
    return record


def _build(fixture: _Fixture, ids: list[str] | None = None) -> dict[str, object]:
    return build_candidate_media_manifest(
        export_root=fixture.root,
        candidate_message_refs=fixture.candidate_ids if ids is None else ids,
        source_hashes=fixture.source_hashes,
    )


class CandidateMediaManifestTests(unittest.TestCase):
    def test_accepts_authorized_baseline_candidates_and_preserves_same_snapshot_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory))
            baseline_only = _attachment_message(
                "106", content_type="image/png", filename="baseline.png"
            )
            closure_duplicate = _attachment_message(
                "101", content_type="image/png", filename="secret.png"
            )
            raw_relative, raw_sha = _add_baseline_page(
                fixture, [baseline_only, closure_duplicate]
            )
            index_path = fixture.root / fixture.run_relative / "asset-index.jsonl"
            records = [json.loads(line) for line in index_path.read_text().splitlines()]
            baseline_record = _record(
                message=baseline_only,
                raw_relative=raw_relative,
                raw_sha256=raw_sha,
                index=0,
                run_root=fixture.root / fixture.run_relative,
                status="complete",
                blob=PNG,
                http_content_type="image/png",
            )
            baseline_only_source = _source(
                message_id="106",
                raw_relative=raw_relative,
                raw_sha256=raw_sha,
                pointer="/payload/0/attachments/0",
            )
            baseline_record["sources"] = [baseline_only_source]
            baseline_record["observations"][0]["source"] = baseline_only_source
            records.append(baseline_record)
            baseline_source = _source(
                message_id="101",
                raw_relative=raw_relative,
                raw_sha256=raw_sha,
                pointer="/payload/1/attachments/0",
            )
            records[0]["sources"] = [baseline_source]
            records[0]["observations"][0]["source"] = baseline_source
            self._rebind_asset_index(fixture, records)

            baseline_manifest = _build(fixture, ["106"])
            duplicate_manifest = _build(fixture, ["101"])

        self.assertEqual(baseline_manifest["counts"]["captured"], 1)  # type: ignore[index]
        self.assertEqual(duplicate_manifest["counts"]["captured"], 1)  # type: ignore[index]
        self.assertEqual(duplicate_manifest["counts"]["occurrences"], 1)  # type: ignore[index]

    def test_partitions_exact_message_source_relations_and_redacts_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory))
            manifest = _build(fixture)

        self.assertEqual(
            manifest["counts"],
            {
                "candidate_messages": 5,
                "candidate_messages_with_media": 5,
                "occurrences": 5,
                "captured": 2,
                "failed": 1,
                "reference_only": 1,
                "pending": 1,
                "ocr_eligible_images": 1,
            },
        )
        items = manifest["items"]
        self.assertEqual(
            [item["status"] for item in items],  # type: ignore[index]
            ["captured", "captured", "failed", "reference_only", "pending"],
        )
        serialized = json.dumps(manifest, sort_keys=True)
        for forbidden in (
            "https://",
            "signed=secret",
            "PRIVATE-BODY",
            "private-author",
            "logical_key",
            "candidate_urls",
            "observed_urls",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_uses_exact_source_not_logical_identity_alone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory))
            index_path = fixture.root / fixture.run_relative / "asset-index.jsonl"
            records = [json.loads(line) for line in index_path.read_text().splitlines()]
            records[0]["sources"][0]["root_message_id"] = "777"
            records[0]["observations"][0]["source"]["root_message_id"] = "777"
            self._rebind_asset_index(fixture, records)
            manifest = _build(fixture, ["101"])
        self.assertEqual(manifest["counts"]["pending"], 1)  # type: ignore[index]
        self.assertEqual(manifest["counts"]["captured"], 0)  # type: ignore[index]

    def test_deduplicates_identical_closure_overlap_and_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory), duplicate_first_snapshot=True)
            first = _build(fixture, list(reversed(fixture.candidate_ids)))
            second = _build(fixture, list(fixture.candidate_ids))
        self.assertEqual(first, second)
        self.assertEqual(first["counts"]["occurrences"], 5)  # type: ignore[index]
        self.assertEqual(len({item["occurrence_id"] for item in first["items"]}), 5)  # type: ignore[index]

    def test_rejects_uncommitted_or_unlocatable_candidate_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory))
            for values in (["999999"], ["101", "101"], []):
                with self.subTest(values=values), self.assertRaises(ValueError):
                    _build(fixture, values)
            with self.assertRaises(ValueError):
                build_candidate_media_manifest(
                    export_root=fixture.root,
                    candidate_message_refs=["101"],
                    source_hashes={"../closure-audit.json": fixture.closure_sha256},
                )

    def test_fails_closed_on_source_or_asset_commitment_tamper(self) -> None:
        for kind in ("closure", "head", "target", "raw", "merge", "asset-index"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                fixture = _fixture(Path(directory))
                namespace = fixture.closure_relative.parents[1]
                if kind == "closure":
                    path = fixture.root / fixture.closure_relative
                elif kind == "head":
                    path = fixture.root / namespace / "capture/head-catchup.json"
                elif kind == "target":
                    path = fixture.root / namespace / "capture/evidence/head/10.json"
                elif kind == "raw":
                    path = fixture.root / namespace / "capture/raw/head/10-000001.json"
                elif kind == "merge":
                    path = fixture.root / namespace / "merge-audit.json"
                else:
                    path = fixture.root / fixture.run_relative / "asset-index.jsonl"
                path.write_bytes(path.read_bytes() + b" ")
                with self.assertRaises(ValueError):
                    _build(fixture)

    def test_validates_captured_blob_mime_before_classifying_it(self) -> None:
        for label, mime, blob in (
            ("wrong-family", "text/html", PNG),
            ("wrong-magic", "image/png", b"not-a-png"),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                fixture = _fixture(
                    Path(directory),
                    captured_http_mime=mime,
                    captured_blob=blob,
                )
                with self.assertRaises(ValueError):
                    _build(fixture)

    def test_binds_full_declared_metadata_and_rejects_complete_size_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory))
            index_path = fixture.root / fixture.run_relative / "asset-index.jsonl"
            records = [json.loads(line) for line in index_path.read_text().splitlines()]
            records[0]["declared_metadata"]["filename"] = "rebound.png"
            self._rebind_asset_index(fixture, records)
            with self.assertRaises(ValueError):
                _build(fixture, ["101"])

        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(
                Path(directory), captured_declared_size=len(PNG) + 1
            )
            with self.assertRaises(ValueError):
                _build(fixture, ["101"])

    def test_rejects_self_consistent_asset_record_kind_field_rebinding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory))
            index_path = fixture.root / fixture.run_relative / "asset-index.jsonl"
            records = [json.loads(line) for line in index_path.read_text().splitlines()]
            record = records[0]
            record["kind"] = "embed"
            record["field"] = "image"
            record["identity_metadata"] = {
                key: deepcopy(value)
                for key, value in record["declared_metadata"].items()
                if key not in {"url", "proxy_url"}
            }
            self._rebind_asset_index(fixture, records)
            with self.assertRaises(ValueError):
                _build(fixture, ["101"])

    def test_classifies_typed_in_progress_and_interrupted_attempts_as_pending(self) -> None:
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted), tempfile.TemporaryDirectory() as directory:
                fixture = _fixture(Path(directory))
                namespace = fixture.closure_relative.parents[1]
                raw_relative = namespace / "capture/raw/head/10-000001.json"
                raw_sha = _sha((fixture.root / raw_relative).read_bytes())
                raw = json.loads((fixture.root / raw_relative).read_text())
                message = raw["response"]["messages"][4]
                index_path = fixture.root / fixture.run_relative / "asset-index.jsonl"
                records = [json.loads(line) for line in index_path.read_text().splitlines()]
                records.append(
                    _typed_pending_record(
                        message=message,
                        raw_relative=raw_relative.as_posix(),
                        raw_sha256=raw_sha,
                        index=4,
                        run_root=fixture.root / fixture.run_relative,
                        interrupted=interrupted,
                    )
                )
                self._rebind_asset_index(fixture, records)
                manifest = _build(fixture, ["105"])
            self.assertEqual(manifest["counts"]["pending"], 1)  # type: ignore[index]
            self.assertEqual(manifest["counts"]["failed"], 0)  # type: ignore[index]
            self.assertIsNone(manifest["items"][0]["blob"])  # type: ignore[index]

    def test_ocr_iterator_yields_only_verified_captured_images_and_rechecks_blob(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _fixture(Path(directory))
            manifest = _build(fixture)
            rows = list(iter_ocr_input_rows(export_root=fixture.root, manifest=manifest))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["message_id"], "101")
            self.assertEqual(rows[0]["mime_type"], "image/png")
            self.assertEqual(rows[0]["content"], PNG)
            serialized = json.dumps(
                {key: value for key, value in rows[0].items() if key != "content"},
                sort_keys=True,
            )
            self.assertNotIn("logical_key", serialized)
            self.assertNotIn("url", serialized)

            blob_path = fixture.root / fixture.captured_blob_relative
            blob_path.write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                list(iter_ocr_input_rows(export_root=fixture.root, manifest=manifest))

    def test_ocr_iterator_rejects_symlink_containment_size_and_mime_forgery(self) -> None:
        for kind in ("symlink", "containment", "size", "mime"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                fixture = _fixture(Path(directory))
                manifest = _build(fixture)
                image = next(
                    item
                    for item in manifest["items"]  # type: ignore[index]
                    if item.get("ocr_eligible") is True
                )
                if kind == "symlink":
                    blob_path = fixture.root / fixture.captured_blob_relative
                    outside = fixture.root / "outside.png"
                    outside.write_bytes(PNG)
                    blob_path.unlink()
                    os.symlink(outside, blob_path)
                else:
                    manifest = deepcopy(manifest)
                    image = next(
                        item
                        for item in manifest["items"]  # type: ignore[index]
                        if item.get("ocr_eligible") is True
                    )
                    if kind == "containment":
                        image["blob"]["path"] = "../outside.png"
                    elif kind == "size":
                        image["blob"]["bytes"] += 1
                    else:
                        image["blob"]["mime_type"] = "image/jpeg"
                with self.assertRaises(ValueError):
                    list(iter_ocr_input_rows(export_root=fixture.root, manifest=manifest))

    def test_ocr_iterator_rebuilds_full_provenance_and_rejects_manifest_forgery(self) -> None:
        for kind in ("unrelated-blob", "occurrence-id", "source-hash", "item-schema"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                fixture = _fixture(Path(directory))
                manifest = deepcopy(_build(fixture))
                image = next(
                    item
                    for item in manifest["items"]  # type: ignore[index]
                    if item.get("ocr_eligible") is True
                )
                if kind == "unrelated-blob":
                    forged = PNG + b"-unrelated"
                    digest = _sha(forged)
                    relative = (
                        fixture.run_relative
                        / "assets/sha256"
                        / digest[:2]
                        / f"{digest}.png"
                    )
                    destination = fixture.root / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(forged)
                    image["blob"] = {
                        "path": relative.as_posix(),
                        "sha256": digest,
                        "bytes": len(forged),
                        "mime_type": "image/png",
                    }
                elif kind == "occurrence-id":
                    image["occurrence_id"] = "f" * 64
                elif kind == "source-hash":
                    manifest["sources"]["input_commitments"][0]["sha256"] = "f" * 64  # type: ignore[index]
                else:
                    image["benign_extra"] = "forged"
                with self.assertRaises(ValueError):
                    list(
                        iter_ocr_input_rows(
                            export_root=fixture.root,
                            manifest=manifest,
                        )
                    )

    def test_ocr_iterator_rejects_nested_secret_fields_and_embedded_raw_urls(self) -> None:
        for key, value in (
            ("bot_token", "private-secret"),
            ("note", "failure at https://cdn.example/private?signed=secret"),
        ):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                fixture = _fixture(Path(directory))
                manifest = deepcopy(_build(fixture))
                manifest["items"][0][key] = value  # type: ignore[index]
                with self.assertRaises(ValueError):
                    list(
                        iter_ocr_input_rows(
                            export_root=fixture.root,
                            manifest=manifest,
                        )
                    )

    @staticmethod
    def _rebind_asset_index(
        fixture: _Fixture, records: list[dict[str, object]]
    ) -> None:
        index_sha = _write_jsonl(
            fixture.root / fixture.run_relative / "asset-index.jsonl", records
        )
        namespace = fixture.closure_relative.parents[1]
        merge_path = fixture.root / namespace / "merge-audit.json"
        merge = json.loads(merge_path.read_text())
        merge["transitive_evidence"]["1"]["asset_evidence"][  # type: ignore[index]
            "asset_index_sha256"
        ] = index_sha
        merge_sha = _write_json(merge_path, merge)
        closure_path = fixture.root / fixture.closure_relative
        closure = json.loads(closure_path.read_text())
        closure["input_file_sha256"]["merge_audit"] = merge_sha  # type: ignore[index]
        closure["input_canonical_sha256"]["merge_audit"] = (  # type: ignore[index]
            canonical_json_sha256(merge)
        )
        fixture.closure_sha256 = _write_json(closure_path, closure)


if __name__ == "__main__":
    unittest.main()
