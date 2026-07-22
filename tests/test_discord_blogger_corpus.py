from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from omni_hub.discord_blogger_corpus import (
    authorized_blogger_message_target_ids,
    iter_verified_blogger_messages,
)
from omni_hub.discord_message_evidence import extract_message_evidence
from omni_hub.discord_sharding import canonical_json_bytes, canonical_json_sha256


def _write_json(path: Path, value: object) -> str:
    content = canonical_json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _message(
    message_id: str, *, content: str, timestamp: str, reply_to: str | None = None
) -> dict[str, object]:
    value: dict[str, object] = {
        "id": message_id,
        "channel_id": "10",
        "timestamp": timestamp,
        "edited_timestamp": None,
        "author": {"id": "900"},
        "content": content,
        "attachments": [{"id": f"{message_id}1", "filename": "sample.png", "url": "https://media.invalid/x"}],
        "embeds": [],
        "components": [],
    }
    if reply_to is not None:
        value["message_reference"] = {"message_id": reply_to, "channel_id": "10"}
    return value


def _baseline_page(
    root: Path, messages: list[dict[str, object]], run_root: Path
) -> tuple[dict[str, object], str]:
    stream = "messages_10"
    raw_path = root / run_root / "pages/messages_10/000001.json"
    raw = {
        "request": {"path": "/channels/10/messages"},
        "payload": messages,
        "acquisition": {"fetched_at": "2026-07-03T00:00:00+00:00", "source": "collector_local_clock_after_response"},
        "pagination": {"item_count": len(messages), "next_cursor": None, "terminal_status": "complete"},
    }
    raw_sha = _write_json(raw_path, raw)
    rows: list[dict[str, object]] = []
    for index, message in enumerate(messages):
        evidence = asdict(extract_message_evidence(
            message, stream=stream, evidence_path="pages/messages_10/000001.json",
            evidence_sha256=raw_sha, json_pointer=f"/payload/{index}",
        ))
        rows.append({
            "schema_version": 2, "stream": stream, "channel_id": "10", "page_number": 1,
            "message_json_pointer": f"/payload/{index}", **evidence,
        })
    evidence_bytes = b"".join(canonical_json_bytes(row) for row in rows)
    evidence_relative = "message-evidence/messages_10/000001.jsonl"
    evidence_path = root / run_root / evidence_relative
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_bytes(evidence_bytes)
    diagnostics = {level: sum(
        diagnostic["severity"] == level
        for row in rows for diagnostic in row["diagnostics"]
    ) for level in ("error", "warning", "info")}
    descriptor = {
        "schema_version": 2, "path": evidence_relative,
        "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "raw_page_path": "pages/messages_10/000001.json", "raw_page_sha256": raw_sha,
        "root_messages": len(rows), "partial_messages": sum(row["status"] == "partial" for row in rows),
        "nodes": sum(len(row["nodes"]) for row in rows),
        "media_occurrences": sum(len(row["media"]) for row in rows),
        "references": sum(len(row["references"]) for row in rows),
        "diagnostics": sum(len(row["diagnostics"]) for row in rows),
        "stream": stream, "channel_id": "10", "page_number": 1,
        "fetched_at": raw["acquisition"]["fetched_at"], "diagnostics_by_severity": diagnostics,
        "pin_events": 0,
    }
    return descriptor, raw_sha


def _closure_target(
    root: Path, *, target_id: str, pages: list[list[dict[str, object]]], capture_dir: Path
) -> dict[str, object]:
    raw_descriptors: list[dict[str, object]] = []
    message_ids: list[str] = []
    for number, messages in enumerate(pages, start=1):
        raw = {
            "schema_version": 1, "audit_kind": "discord-head-catchup-raw-page-v1",
            "guild_id": "1", "target_id": target_id, "t_close": "2026-07-01T00:00:00+00:00",
            "t_close_source_sha256": "a" * 64, "caught_through": "2026-07-03T00:00:00+00:00",
            "request": {"method": "GET", "path": f"/channels/{target_id}/messages", "params": {}},
            "response": {"status_code": 200, "messages": messages, "threads": [], "next_cursor": None, "terminal": True, "terminal_reason": "short_page"},
        }
        relative = (capture_dir / "raw/head" / target_id / f"{number:06d}.json").as_posix()
        raw_sha = _write_json(root / relative, raw)
        raw_descriptors.append({"path": relative, "sha256": raw_sha,
                                "request_sha256": canonical_json_sha256(raw["request"]),
                                "response_sha256": canonical_json_sha256(raw["response"])})
        message_ids.extend(str(message["id"]) for message in messages if str(message["id"]) != "104")
    evidence = {
        "schema_version": 1, "audit_kind": "discord-head-catchup-target-v1", "guild_id": "1",
        "target_id": target_id, "t_close": "2026-07-01T00:00:00+00:00",
        "t_close_source_sha256": "a" * 64, "caught_through": "2026-07-03T00:00:00+00:00",
        "high_exclusive": "999999999999999999", "new_message_count": len(message_ids),
        "new_message_ids": message_ids, "new_thread_count": 0, "new_thread_ids": [], "raw_pages": raw_descriptors,
    }
    evidence_relative = (capture_dir / "evidence/head" / f"{target_id}.json").as_posix()
    evidence_sha = _write_json(root / evidence_relative, evidence)
    return {"id": target_id, "caught_through": evidence["caught_through"],
            "evidence_path": evidence_relative, "evidence_sha256": evidence_sha,
            "new_message_count": len(message_ids), "new_message_ids": message_ids,
            "new_thread_count": 0, "new_thread_ids": []}


def _fixture(root: Path) -> Path:
    namespace = Path("closure/full-pinned-example")
    capture = namespace / "capture"
    run_root = namespace / "runs/shard-1"
    baseline = [_message("101", content="baseline", timestamp="2026-07-01T01:00:00+00:00"),
                _message("102", content="old", timestamp="2026-07-01T02:00:00+00:00")]
    descriptor, raw_sha = _baseline_page(root, baseline, run_root)
    request = {"run_id": "run-1", "target_snapshot": {}, "target_sha256": "b" * 64}
    manifest = {"run_id": "run-1"}
    checkpoint = {"run_id": "run-1", "streams": {"messages_10": {
        "status": "complete", "page_hashes": [raw_sha], "page_states": [{"message_evidence": descriptor}],
    }}}
    inventory = {"targets": []}
    request_sha = _write_json(root / run_root / "request.json", request)
    manifest_sha = _write_json(root / run_root / "manifest.json", manifest)
    checkpoint_sha = _write_json(root / run_root / "checkpoint.json", checkpoint)
    inventory_sha = _write_json(root / run_root / "inventory/targets.json", inventory)
    merge_request = {"shards": [{"index": 1, "run_root": run_root.as_posix(),
                                  "request_sha256": request_sha, "manifest_sha256": manifest_sha,
                                  "checkpoint_sha256": checkpoint_sha, "targets_inventory_sha256": inventory_sha}]}
    merge_request_sha = _write_json(root / namespace / "merge-request.json", merge_request)
    artifacts = {key: {"expected": value, "actual": value, "verified": True} for key, value in {
        "request": request_sha, "manifest": manifest_sha, "checkpoint": checkpoint_sha,
        "targets_inventory": inventory_sha}.items()}
    merge = {
        "audit_kind": "discord-parent-family-merge-v1", "status": "partial",
        "merge_request_sha256": merge_request_sha, "validation_errors": [],
        "static_scope": {"exact_union": True, "pairwise_disjoint": True}, "static_target_ids": ["10"],
        "message_bearing_static_target_ids": ["10"], "required_head_catchup_target_ids": ["10"],
        "non_private_incomplete_streams": [], "failed_streams": [], "truncated_streams": [],
        "message_reference_incomplete_shards": [], "media_incomplete_shards": [{"index": 1}],
        "artifact_hashes": {"1": artifacts},
        "artifact_hash_verification": {"1": {key: True for key in artifacts}},
    }
    merge_sha = _write_json(root / namespace / "merge-audit.json", merge)
    closure_target = _closure_target(root, target_id="10", capture_dir=capture, pages=[
        [_message("104", content="boundary", timestamp="2026-07-01T03:00:00+00:00"),
         _message("102", content="current", timestamp="2026-07-02T01:00:00+00:00", reply_to="101")],
        [_message("105", content="new", timestamp="2026-07-02T02:00:00+00:00")],
    ])
    head = {"targets": [closure_target]}
    head_sha = _write_json(root / capture / "head-catchup.json", head)
    closure = {
        "audit_kind": "discord-parent-family-closure-v1", "status": "incomplete",
        "input_file_sha256": {"merge_audit": merge_sha, "head_catchup": head_sha},
        "input_canonical_sha256": {"merge_audit": canonical_json_sha256(merge), "head_catchup": canonical_json_sha256(head)},
        "validation_errors": [], "captured_delta": {"message_ids": ["102", "105"]},
        "unresolved": {"target_ids": [], "missing_target_ids": [], "unexpected_target_ids": [],
                       "invalid_delta_target_ids": [], "unverified_evidence_target_ids": [],
                       "non_private_incomplete_streams": [], "message_reference_incomplete_shards": [],
                       "media_incomplete_shards": [{"index": 1}]},
    }
    closure_path = root / capture / "closure-audit.json"
    _write_json(closure_path, closure)
    _write_json(root / capture / "old-audit.json", {"audit_kind": "discord-parent-family-closure-v1"})
    return closure_path


def _iter(root: Path, closure: Path) -> list[object]:
    return list(iter_verified_blogger_messages(
        export_root=root, closure_audit_path=closure.relative_to(root), target_ids=["10"],
    ))


def _iter_with_commitment(root: Path, closure: Path, expected_sha256: str) -> list[object]:
    return list(iter_verified_blogger_messages(
        export_root=root,
        closure_audit_path=closure.relative_to(root),
        target_ids=["10"],
        expected_closure_sha256=expected_sha256,
    ))


class BloggerCorpusTests(unittest.TestCase):
    def test_authorized_scope_keeps_explicit_and_dynamic_threads(self) -> None:
        forum_id = "1514503993567744030"
        explicit_thread_id = "1516770209279512618"
        dynamic_thread_id = "1526994361362157672"
        merge = {
            "static_target_ids": [forum_id, explicit_thread_id],
            "message_bearing_static_target_ids": [explicit_thread_id],
            "required_head_catchup_target_ids": [
                explicit_thread_id,
                dynamic_thread_id,
            ],
            "discovered_threads": [
                {"id": explicit_thread_id, "parent_id": forum_id, "owner_index": 1},
                {"id": dynamic_thread_id, "parent_id": forum_id, "owner_index": 1},
            ],
        }

        self.assertEqual(
            authorized_blogger_message_target_ids(merge),
            (explicit_thread_id, dynamic_thread_id),
        )
        closure = {
            "census_delta": {"missing_from_merge": [], "missing_from_census": []},
            "head_catchup_delta": {
                "new_thread_ids": [],
                "new_thread_target_ids": [],
            },
        }
        head = {
            "targets": [
                {"id": explicit_thread_id},
                {"id": dynamic_thread_id},
            ]
        }
        self.assertEqual(
            authorized_blogger_message_target_ids(
                merge, closure=closure, head=head
            ),
            (explicit_thread_id, dynamic_thread_id),
        )

        closure["census_delta"]["missing_from_merge"] = [
            "1530000000000000001"
        ]
        with self.assertRaisesRegex(ValueError, "new Threads"):
            authorized_blogger_message_target_ids(
                merge, closure=closure, head=head
            )

        closure["census_delta"]["missing_from_merge"] = []
        closure["head_catchup_delta"]["new_thread_ids"] = [
            "1530000000000000002"
        ]
        with self.assertRaisesRegex(ValueError, "new Threads"):
            authorized_blogger_message_target_ids(
                merge, closure=closure, head=head
            )

        closure["head_catchup_delta"]["new_thread_ids"] = []
        head["targets"] = [{"id": explicit_thread_id}]
        with self.assertRaisesRegex(ValueError, "head catch-up targets"):
            authorized_blogger_message_target_ids(
                merge, closure=closure, head=head
            )

    def test_expected_closure_commitment_rejects_replacement_before_consumption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            closure = _fixture(root)
            frozen_sha = hashlib.sha256(closure.read_bytes()).hexdigest()
            replacement = json.loads(closure.read_text())
            replacement["status"] = "replaced"
            _write_json(closure, replacement)
            with self.assertRaisesRegex(ValueError, "closure audit hash commitment"):
                _iter_with_commitment(root, closure, frozen_sha)

    def test_uses_formal_namespace_paths_anchored_at_closure_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            closure = _fixture(root)
            self.assertEqual(
                [message.message_id for message in _iter(root, closure)],
                ["101", "102", "105"],
            )

    def test_does_not_load_non_requested_baseline_or_closure_streams(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            closure = _fixture(root)
            namespace = root / "closure/full-pinned-example"
            capture = namespace / "capture"
            checkpoint_path = namespace / "runs/shard-1/checkpoint.json"
            checkpoint = json.loads(checkpoint_path.read_text())
            checkpoint["streams"]["messages_20"] = {
                "status": "complete", "page_hashes": ["c" * 64],
                "page_states": [{"message_evidence": {"schema_version": 2, "path": "message-evidence/messages_20/000001.jsonl", "sha256": "d" * 64}}],
            }
            checkpoint_sha = _write_json(checkpoint_path, checkpoint)
            request_path = namespace / "merge-request.json"
            request = json.loads(request_path.read_text())
            request["shards"][0]["checkpoint_sha256"] = checkpoint_sha
            request_sha = _write_json(request_path, request)
            merge_path = namespace / "merge-audit.json"
            merge = json.loads(merge_path.read_text())
            merge["static_target_ids"] = ["10", "20"]
            merge["message_bearing_static_target_ids"] = ["10", "20"]
            merge["required_head_catchup_target_ids"] = ["10", "20"]
            merge["merge_request_sha256"] = request_sha
            merge["artifact_hashes"]["1"]["checkpoint"] = {
                "expected": checkpoint_sha, "actual": checkpoint_sha, "verified": True,
            }
            merge_sha = _write_json(merge_path, merge)
            head_path = capture / "head-catchup.json"
            head = json.loads(head_path.read_text())
            head["targets"].append({
                "id": "20", "caught_through": "2026-07-03T00:00:00+00:00",
                "evidence_path": "closure/full-pinned-example/capture/evidence/head/20.json", "evidence_sha256": "e" * 64,
                "new_message_count": 1, "new_message_ids": ["201"], "new_thread_count": 0, "new_thread_ids": [],
            })
            head_sha = _write_json(head_path, head)
            audit = json.loads(closure.read_text())
            audit["captured_delta"]["message_ids"] = ["102", "105", "201"]
            audit["input_file_sha256"] = {"merge_audit": merge_sha, "head_catchup": head_sha}
            audit["input_canonical_sha256"] = {"merge_audit": canonical_json_sha256(merge), "head_catchup": canonical_json_sha256(head)}
            _write_json(closure, audit)

            self.assertEqual(
                [message.message_id for message in _iter(root, closure)],
                ["101", "102", "105"],
            )
    def test_uses_formal_closure_evidence_and_explicit_ids_across_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            closure = _fixture(root)
            messages = _iter(root, closure)
        self.assertEqual([message.message_id for message in messages], ["101", "102", "105"])
        self.assertEqual(messages[1].content, "current")
        self.assertEqual(messages[1].reply_message_id, "101")

    def test_reads_production_evidence_root_rows_without_leaking_logical_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            closure = _fixture(root)
            message = _iter(root, closure)[0]
        self.assertEqual(message.author_id, "900")
        self.assertEqual(message.snapshot_ref, "closure/full-pinned-example/runs/shard-1/pages/messages_10/000001.json#/payload/0")
        self.assertEqual(message.media_occurrence_refs, ("closure/full-pinned-example/runs/shard-1/message-evidence/messages_10/000001.jsonl#/1/media/0",))
        self.assertNotIn("attachment", message.media_occurrence_refs[0])

    def test_filters_the_deduplicated_corpus_by_target_and_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            closure = _fixture(root)
            messages = list(iter_verified_blogger_messages(
                export_root=root, closure_audit_path=closure.relative_to(root), target_ids=["10"],
                start=datetime(2026, 7, 2, 1, tzinfo=UTC), end=datetime(2026, 7, 2, 2, tzinfo=UTC),
            ))
        self.assertEqual([message.message_id for message in messages], ["102"])

    def test_rejects_checkpoint_recommit_when_merge_artifact_hash_stays_fixed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            closure = _fixture(root)
            checkpoint_path = root / "closure/full-pinned-example/runs/shard-1/checkpoint.json"
            checkpoint = json.loads(checkpoint_path.read_text())
            checkpoint["streams"]["messages_10"]["page_hashes"] = ["0" * 64]
            _write_json(checkpoint_path, checkpoint)
            with self.assertRaises(ValueError):
                _iter(root, closure)

    def test_fails_closed_on_raw_evidence_merge_or_closure_tamper(self) -> None:
        for kind in ("raw", "evidence", "merge", "closure"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                closure = _fixture(root)
                if kind == "raw":
                    (root / "closure/full-pinned-example/runs/shard-1/pages/messages_10/000001.json").write_bytes(b"{}\n")
                elif kind == "evidence":
                    (root / "closure/full-pinned-example/runs/shard-1/message-evidence/messages_10/000001.jsonl").write_bytes(b"{}\n")
                elif kind == "merge":
                    (root / "closure/full-pinned-example/merge-audit.json").write_bytes(b"{}\n")
                else:
                    audit = json.loads(closure.read_text())
                    audit["captured_delta"]["message_ids"] = ["105"]
                    _write_json(closure, audit)
                with self.assertRaises(ValueError):
                    _iter(root, closure)

    def test_requires_message_scope_gates_but_allows_media_private_partial_verdicts(self) -> None:
        for field, bad in (("validation_errors", ["bad"]), ("non_private_incomplete_streams", [{"stream": "messages_10"}]),
                           ("message_reference_incomplete_shards", [{"index": 1}])):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                closure = _fixture(root)
                merge_path = root / "closure/full-pinned-example/merge-audit.json"
                merge = json.loads(merge_path.read_text())
                merge[field] = bad
                merge_sha = _write_json(merge_path, merge)
                audit = json.loads(closure.read_text())
                audit["input_file_sha256"]["merge_audit"] = merge_sha
                audit["input_canonical_sha256"]["merge_audit"] = canonical_json_sha256(merge)
                _write_json(closure, audit)
                with self.assertRaises(ValueError):
                    _iter(root, closure)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            closure = _fixture(root)
            self.assertEqual([message.message_id for message in _iter(root, closure)], ["101", "102", "105"])

    def test_requires_explicit_safe_closure_audit_path_and_ignores_other_audits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            closure = _fixture(root)
            with self.assertRaises(TypeError):
                list(iter_verified_blogger_messages(export_root=root, target_ids=["10"]))
            self.assertEqual([message.message_id for message in _iter(root, closure)], ["101", "102", "105"])


if __name__ == "__main__":
    unittest.main()
