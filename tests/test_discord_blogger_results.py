from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import inspect
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from omni_hub.discord_blogger_corpus import BloggerMessage
from omni_hub.discord_blogger_results import (
    build_latest_calls_report,
    publish_blogger_event_artifacts,
)
from omni_hub.discord_trade_events import (
    PARSER_IMPLEMENTATION_SHA256,
    PROFILE_CHANNELS,
    PROFILE_CONFIG_SHA256,
    link_trade_lifecycles,
    parse_message,
)


def _decision() -> object:
    return parse_message(
        "coin-chief-v1",
        BloggerMessage(
            message_id="70", channel_id=PROFILE_CHANNELS["coin-chief-v1"], author_id="1",
            timestamp="2026-07-20T10:00:00+00:00", edited_timestamp=None,
            content="BTC 做多 进场 100000，止盈 101000，止损 99000", reply_message_id=None,
            snapshot_ref="evidence/page.json#/payload/0", snapshot_sha256="b" * 64,
            media_occurrence_refs=(),
        ),
    )


def _source_manifest(decision: object) -> dict[str, object]:
    assert hasattr(decision, "to_dict")
    row = decision.to_dict()
    commitment = hashlib.sha256(
        json.dumps(
            [(row["message_id"], row["channel_id"], row["author_id"], row["snapshot_sha256"], row["decision_id"])],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "provenance": {
            "closure_audit": {
                "path": "exports/closure-audit.json",
                "sha256": hashlib.sha256(_closure_audit_bytes()).hexdigest(),
                "input_file_sha256": {
                    "census": "f" * 64,
                    "head_catchup": "e" * 64,
                    "merge_audit": "d" * 64,
                },
            },
            "asof": "2026-07-21T00:00:00+00:00",
            "parser_implementation_sha256": PARSER_IMPLEMENTATION_SHA256,
            "profiles": [{
                "profile": "coin-chief-v1",
                "version": "v1",
                "channel_id": PROFILE_CHANNELS["coin-chief-v1"],
                "config_sha256": PROFILE_CONFIG_SHA256["coin-chief-v1"],
            }],
            "corpus_message_count": 1,
            "corpus_commitment": commitment,
        },
        "decisions": [row],
        "lifecycles": [lifecycle.to_dict() for lifecycle in link_trade_lifecycles((decision,))],
        "latest_calls": build_latest_calls_report(decisions=(decision,), asof=datetime(2026, 7, 21, tzinfo=UTC)),
    }


def _closure_audit_bytes() -> bytes:
    return json.dumps(
        {
            "audit_kind": "discord-parent-family-closure-v1",
            "input_file_sha256": {
                "census": "f" * 64,
                "merge_audit": "d" * 64,
                "head_catchup": "e" * 64,
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _publish(
    *, workspace: Path, output_dir: Path, source_manifest: dict[str, object]
) -> dict[str, object]:
    return publish_blogger_event_artifacts(
        workspace=workspace,
        output_dir=output_dir,
        source_manifest=source_manifest,
        closure_audit_path=Path("exports/closure-audit.json"),
        closure_audit_bytes=_closure_audit_bytes(),
    )


class BloggerResultsTests(unittest.TestCase):
    def test_publication_boundary_requires_frozen_closure_inputs(self) -> None:
        parameters = inspect.signature(publish_blogger_event_artifacts).parameters
        self.assertIn("closure_audit_path", parameters)
        self.assertIn("closure_audit_bytes", parameters)
        self.assertIs(parameters["closure_audit_path"].default, inspect.Parameter.empty)
        self.assertIs(parameters["closure_audit_bytes"].default, inspect.Parameter.empty)

    def test_future_events_do_not_change_an_earlier_asof_report(self) -> None:
        profile = "coin-chief-v1"
        opening = parse_message(profile, BloggerMessage(
            message_id="68", channel_id=PROFILE_CHANNELS[profile], author_id="author-1",
            timestamp="2026-07-20T10:00:00+00:00", edited_timestamp=None,
            content="BTC 做多 入场 100000", reply_message_id=None,
            snapshot_ref="evidence/page.json#/68", snapshot_sha256="1" * 64, media_occurrence_refs=(),
        ))
        future_tp = parse_message(profile, BloggerMessage(
            message_id="69", channel_id=PROFILE_CHANNELS[profile], author_id="author-1",
            timestamp="2026-07-22T10:00:00+00:00", edited_timestamp=None,
            content="止盈", reply_message_id="68",
            snapshot_ref="evidence/page.json#/69", snapshot_sha256="2" * 64, media_occurrence_refs=(),
        ))
        report = build_latest_calls_report(decisions=(opening, future_tp), asof=datetime(2026, 7, 21, tzinfo=UTC))
        self.assertEqual([call["message_id"] for call in report["calls"]], ["68"])

    def test_latest_calls_has_locatable_redacted_evidence_and_current_fields(self) -> None:
        report = build_latest_calls_report(
            decisions=(_decision(),), asof=datetime(2026, 7, 21, tzinfo=UTC)
        )
        call = report["calls"][0]
        self.assertEqual(call["blogger"], "币圈所长")
        self.assertEqual(call["symbol"], "BTCUSDT")
        self.assertEqual(call["direction"], "long")
        self.assertEqual(call["entry"], 100000.0)
        self.assertEqual(call["entry_low"], 100000.0)
        self.assertEqual(call["entry_high"], 100000.0)
        self.assertEqual(call["tp"], 101000.0)
        self.assertEqual(call["tps"], [101000.0])
        self.assertEqual(call["sl"], 99000.0)
        self.assertEqual(call["message_id"], "70")
        self.assertEqual(call["evidence_ref"], "evidence/page.json#/payload/0")
        self.assertEqual(call["author_id"], "1")
        self.assertNotIn("content", call)

    def test_unresolved_conflict_is_not_reported_as_a_current_closed_call(self) -> None:
        profile = "coin-chief-v1"
        open_decision = parse_message(profile, BloggerMessage(
            message_id="71", channel_id=PROFILE_CHANNELS[profile], author_id="1",
            timestamp="2026-07-20T10:00:00+00:00", edited_timestamp=None,
            content="BTC 做多 入场 100000", reply_message_id=None,
            snapshot_ref="evidence/page.json#/payload/1", snapshot_sha256="d" * 64, media_occurrence_refs=(),
        ))
        cancel_decision = parse_message(profile, BloggerMessage(
            message_id="72", channel_id=PROFILE_CHANNELS[profile], author_id="1",
            timestamp="2026-07-20T10:01:00+00:00", edited_timestamp=None,
            content="BTC 做多 撤单", reply_message_id="71",
            snapshot_ref="evidence/page.json#/payload/2", snapshot_sha256="e" * 64, media_occurrence_refs=(),
        ))
        report = build_latest_calls_report(
            decisions=(open_decision, cancel_decision), asof=datetime(2026, 7, 21, tzinfo=UTC)
        )
        self.assertEqual(report["calls"], [])
        self.assertEqual(report["unresolved_lifecycle_count"], 0)

    def test_publish_is_atomic_no_clobber_and_never_writes_message_body(self) -> None:
        decision = _decision()
        source_manifest = _source_manifest(decision)
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            output = Path("published")
            result = _publish(workspace=workspace, output_dir=output, source_manifest=source_manifest)
            self.assertEqual(result["output_dir"], "published")
            self.assertEqual(
                sorted(path.name for path in (workspace / output).iterdir()),
                [
                    "event-manifest.json",
                    "latest-calls.json",
                    "latest-calls.md",
                    "message-decisions.jsonl",
                    "trade-events.jsonl",
                    "trade-lifecycles.jsonl",
                ],
            )
            serialized = "".join(path.read_text() for path in (workspace / output).iterdir())
            self.assertNotIn("BTC 做多", serialized)
            self.assertNotIn("logical_key", serialized)
            manifest = json.loads((workspace / output / "event-manifest.json").read_text())
            self.assertEqual(manifest["artifact_kind"], "discord-blogger-events-v1")
            self.assertEqual(manifest["provenance"], source_manifest["provenance"])
            self.assertEqual(manifest["lifecycle_count"], 1)
            lifecycle_bytes = (workspace / output / "trade-lifecycles.jsonl").read_bytes()
            self.assertEqual(
                manifest["files"]["trade-lifecycles.jsonl"],
                hashlib.sha256(lifecycle_bytes).hexdigest(),
            )
            event = json.loads((workspace / output / "trade-events.jsonl").read_text())
            self.assertIsNotNone(event["lifecycle_id"])
            self.assertEqual(event["link_status"], "resolved")
            lifecycle = json.loads((workspace / output / "trade-lifecycles.jsonl").read_text())
            self.assertEqual(lifecycle, source_manifest["lifecycles"][0])
            self.assertIn("unresolved_event_ids", lifecycle)
            self.assertRaises(FileExistsError, _publish, workspace=workspace, output_dir=output, source_manifest=source_manifest)

    def test_publish_reserves_the_output_name_before_staging_files(self) -> None:
        decision = _decision()
        source_manifest = _source_manifest(decision)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "contended"
            with patch(
                "omni_hub.discord_blogger_results._rename_directory_noreplace_at",
                side_effect=FileExistsError,
            ):
                with self.assertRaises(FileExistsError):
                    _publish(workspace=Path(directory), output_dir=Path("contended"), source_manifest=source_manifest)
            self.assertFalse(output.exists())

    def test_publish_failure_cleans_sibling_stage_and_never_creates_final(self) -> None:
        decision = _decision()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with patch("omni_hub.discord_blogger_results._write_at", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    _publish(workspace=workspace, output_dir=Path("published"), source_manifest=_source_manifest(decision))
            self.assertFalse((workspace / "published").exists())
            self.assertEqual(list(workspace.glob(".published.stage-*")), [])

    def test_publish_rejects_symlinked_output_parent(self) -> None:
        decision = _decision()
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            workspace = Path(directory)
            (workspace / "safe").mkdir()
            (workspace / "safe" / "escape").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                _publish(workspace=workspace, output_dir=Path("safe/escape/published"), source_manifest=_source_manifest(decision))
            self.assertFalse((Path(outside) / "published").exists())

    def test_publish_rejects_tampered_provenance_before_creating_a_stage(self) -> None:
        decision = _decision()
        source_manifest = _source_manifest(decision)
        provenance = source_manifest["provenance"]
        assert isinstance(provenance, dict)
        provenance["corpus_commitment"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with self.assertRaisesRegex(ValueError, "corpus commitment"):
                _publish(
                    workspace=workspace,
                    output_dir=Path("published"),
                    source_manifest=source_manifest,
                )
            self.assertFalse((workspace / "published").exists())
            self.assertEqual(list(workspace.glob(".published.stage-*")), [])

    def test_closure_provenance_must_match_the_frozen_bytes_and_path(self) -> None:
        decision = _decision()
        for field in ("sha256", "path", "input_file_sha256"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                source_manifest = _source_manifest(decision)
                provenance = source_manifest["provenance"]
                assert isinstance(provenance, dict)
                closure = provenance["closure_audit"]
                assert isinstance(closure, dict)
                if field == "sha256":
                    closure[field] = "0" * 64
                elif field == "path":
                    closure[field] = "exports/forged.json"
                else:
                    closure[field] = {"merge_audit": "0" * 64, "head_catchup": "e" * 64}
                workspace = Path(directory)
                with self.assertRaisesRegex(ValueError, "closure provenance"):
                    publish_blogger_event_artifacts(
                        workspace=workspace,
                        output_dir=Path("published"),
                        source_manifest=source_manifest,
                        closure_audit_path=Path("exports/closure-audit.json"),
                        closure_audit_bytes=_closure_audit_bytes(),
                    )
                self.assertFalse((workspace / "published").exists())

    def test_publish_rejects_forged_lifecycle_before_creating_a_stage(self) -> None:
        decision = _decision()
        source_manifest = _source_manifest(decision)
        lifecycles = source_manifest["lifecycles"]
        assert isinstance(lifecycles, list) and isinstance(lifecycles[0], dict)
        lifecycles[0]["status"] = "closed"
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with self.assertRaisesRegex(ValueError, "lifecycles"):
                _publish(
                    workspace=workspace,
                    output_dir=Path("published"),
                    source_manifest=source_manifest,
                )
            self.assertFalse((workspace / "published").exists())
            self.assertEqual(list(workspace.glob(".published.stage-*")), [])

    def test_publish_rejects_forged_latest_calls_before_creating_a_stage(self) -> None:
        decision = _decision()
        source_manifest = _source_manifest(decision)
        report = source_manifest["latest_calls"]
        assert isinstance(report, dict) and isinstance(report["calls"], list)
        report["calls"][0]["entry"] = 1.0
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with self.assertRaisesRegex(ValueError, "latest calls"):
                _publish(
                    workspace=workspace,
                    output_dir=Path("published"),
                    source_manifest=source_manifest,
                )
            self.assertFalse((workspace / "published").exists())
            self.assertEqual(list(workspace.glob(".published.stage-*")), [])

    def test_parent_symlink_swap_after_staging_fails_closed_and_cleans_stage(self) -> None:
        decision = _decision()
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            workspace = Path(directory)
            parent = workspace / "safe"
            parent.mkdir()
            original_fsync = __import__("omni_hub.discord_blogger_results", fromlist=["_fsync_stage"])._fsync_stage
            swapped = False

            def swap_after_stage(stage_fd: int) -> None:
                nonlocal swapped
                original_fsync(stage_fd)
                if not swapped:
                    parent.rename(workspace / "safe-held")
                    parent.symlink_to(outside, target_is_directory=True)
                    swapped = True

            with patch("omni_hub.discord_blogger_results._fsync_stage", side_effect=swap_after_stage):
                with self.assertRaises(ValueError):
                    _publish(workspace=workspace, output_dir=Path("safe/published"), source_manifest=_source_manifest(decision))
            self.assertFalse((Path(outside) / "published").exists())
            self.assertFalse((workspace / "safe-held").exists() and any((workspace / "safe-held").iterdir()))

    def test_parent_swap_before_staging_never_creates_an_outside_stage_or_final(self) -> None:
        decision = _decision()
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            workspace = Path(directory)
            parent = workspace / "safe"
            parent.mkdir()
            import omni_hub.discord_blogger_results as results_module

            original_open = results_module.os.open
            swapped = False

            def swap_after_anchor(path, flags, *args, **kwargs):
                nonlocal swapped
                descriptor = original_open(path, flags, *args, **kwargs)
                if not swapped and str(path) == "safe" and kwargs.get("dir_fd") is not None:
                    parent.rename(workspace / "safe-held")
                    parent.symlink_to(outside, target_is_directory=True)
                    swapped = True
                return descriptor

            with patch("omni_hub.discord_blogger_results.os.open", side_effect=swap_after_anchor):
                with self.assertRaises(ValueError):
                    _publish(workspace=workspace, output_dir=Path("safe/published"), source_manifest=_source_manifest(decision))
            self.assertEqual(list(Path(outside).iterdir()), [])
            self.assertFalse((Path(outside) / "published").exists())

    def test_cleanup_refuses_a_replaced_stage_name_without_touching_outside_files(self) -> None:
        decision = _decision()
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            workspace = Path(directory)
            parent = workspace / "safe"
            parent.mkdir()
            sentinel = Path(outside) / "sentinel.txt"
            sentinel.write_text("keep")
            import omni_hub.discord_blogger_results as results_module

            original_create = results_module._create_stage
            stage_name: str | None = None
            swapped = False

            def record_stage(parent_fd: int, output_name: str):
                nonlocal stage_name
                created = original_create(parent_fd, output_name)
                stage_name = created[0]
                return created

            def replace_stage_then_fail(*_args: object, **_kwargs: object) -> None:
                nonlocal swapped
                if not swapped:
                    assert stage_name is not None
                    (parent / stage_name).rename(Path(outside) / "moved-stage")
                    (parent / stage_name).symlink_to(outside, target_is_directory=True)
                    swapped = True
                raise OSError("disk full")

            with patch("omni_hub.discord_blogger_results._create_stage", side_effect=record_stage), patch(
                "omni_hub.discord_blogger_results._write_at", side_effect=replace_stage_then_fail
            ):
                with self.assertRaises(ValueError):
                    _publish(
                        workspace=workspace,
                        output_dir=Path("safe/published"),
                        source_manifest=_source_manifest(decision),
                    )
            self.assertEqual(sentinel.read_text(), "keep")
            self.assertFalse((Path(outside) / "published").exists())

    def test_stage_name_replacement_before_rename_never_publishes_replacement(self) -> None:
        decision = _decision()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            import omni_hub.discord_blogger_results as results_module

            original_create = results_module._create_stage
            original_fsync = results_module._fsync_stage
            stage_name: str | None = None

            def record_stage(parent_fd: int, output_name: str):
                nonlocal stage_name
                created = original_create(parent_fd, output_name)
                stage_name = created[0]
                return created

            def replace_stage(stage_fd: int) -> None:
                original_fsync(stage_fd)
                assert stage_name is not None
                (workspace / stage_name).rename(workspace / "held-stage")
                (workspace / stage_name).mkdir()

            with patch("omni_hub.discord_blogger_results._create_stage", side_effect=record_stage), patch(
                "omni_hub.discord_blogger_results._fsync_stage", side_effect=replace_stage
            ):
                with self.assertRaisesRegex(ValueError, "staging directory identity"):
                    _publish(
                        workspace=workspace,
                        output_dir=Path("published"),
                        source_manifest=_source_manifest(decision),
                    )
            self.assertFalse((workspace / "published").exists())

    def test_unexpected_stage_file_fails_exact_inventory_before_publish(self) -> None:
        decision = _decision()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            import omni_hub.discord_blogger_results as results_module

            original_fsync = results_module._fsync_stage

            def inject_extra_file(stage_fd: int) -> None:
                descriptor = os.open(
                    "unexpected.txt",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=stage_fd,
                )
                try:
                    os.write(descriptor, b"unexpected")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                original_fsync(stage_fd)

            with patch("omni_hub.discord_blogger_results._fsync_stage", side_effect=inject_extra_file):
                with self.assertRaisesRegex(ValueError, "staging inventory"):
                    _publish(
                        workspace=workspace,
                        output_dir=Path("published"),
                        source_manifest=_source_manifest(decision),
                    )
            self.assertFalse((workspace / "published").exists())

    def test_content_changed_at_rename_boundary_is_removed_instead_of_published(self) -> None:
        decision = _decision()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            import omni_hub.discord_blogger_results as results_module

            original_rename = results_module._rename_directory_noreplace_at

            def tamper_then_rename(source_name: str, destination_name: str, parent_fd: int) -> None:
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
                stage_fd = os.open(source_name, flags, dir_fd=parent_fd)
                try:
                    file_fd = os.open(
                        "latest-calls.json",
                        os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=stage_fd,
                    )
                    try:
                        os.write(file_fd, b"!")
                        os.fsync(file_fd)
                    finally:
                        os.close(file_fd)
                finally:
                    os.close(stage_fd)
                original_rename(source_name, destination_name, parent_fd)

            with patch(
                "omni_hub.discord_blogger_results._rename_directory_noreplace_at",
                side_effect=tamper_then_rename,
            ):
                with self.assertRaisesRegex(ValueError, "staging inventory"):
                    _publish(
                        workspace=workspace,
                        output_dir=Path("published"),
                        source_manifest=_source_manifest(decision),
                    )
            self.assertFalse((workspace / "published").exists())

    def test_source_name_swap_at_rename_is_quarantined_and_releases_canonical(self) -> None:
        decision = _decision()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            import omni_hub.discord_blogger_results as results_module

            original_rename = results_module._rename_directory_noreplace_at

            def swap_source_then_rename(
                source_name: str, destination_name: str, parent_fd: int
            ) -> None:
                if source_name.startswith(".published.stage-"):
                    (workspace / source_name).rename(workspace / "held-original-stage")
                    replacement = workspace / source_name
                    replacement.mkdir(mode=0o700)
                    (replacement / "attacker-evidence.txt").write_bytes(b"preserve-B")
                original_rename(source_name, destination_name, parent_fd)

            with patch(
                "omni_hub.discord_blogger_results._rename_directory_noreplace_at",
                side_effect=swap_source_then_rename,
            ):
                with self.assertRaisesRegex(ValueError, "staging directory identity"):
                    _publish(
                        workspace=workspace,
                        output_dir=Path("published"),
                        source_manifest=_source_manifest(decision),
                    )
            self.assertFalse((workspace / "published").exists())
            quarantines = list(workspace.glob(".published.quarantine-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(
                (quarantines[0] / "attacker-evidence.txt").read_bytes(),
                b"preserve-B",
            )

    def test_first_nofollow_stat_failure_leaves_no_stage_or_canonical_name(self) -> None:
        decision = _decision()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            import omni_hub.discord_blogger_results as results_module

            original_stat = results_module.os.stat
            failed = False

            def fail_first_stage_stat(path, *args, **kwargs):
                nonlocal failed
                if (
                    not failed
                    and str(path).startswith(".published.stage-")
                    and kwargs.get("dir_fd") is not None
                    and kwargs.get("follow_symlinks") is False
                ):
                    failed = True
                    raise OSError("stage stat failed")
                return original_stat(path, *args, **kwargs)

            with patch("omni_hub.discord_blogger_results.os.stat", side_effect=fail_first_stage_stat):
                with self.assertRaises(OSError):
                    _publish(
                        workspace=workspace,
                        output_dir=Path("published"),
                        source_manifest=_source_manifest(decision),
                    )
            self.assertFalse((workspace / "published").exists())
            self.assertEqual(list(workspace.glob(".published.stage-*")), [])

    def test_directory_mode_change_before_rename_fails_closed(self) -> None:
        decision = _decision()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            import omni_hub.discord_blogger_results as results_module

            original_fsync = results_module._fsync_stage

            def relax_directory_mode(stage_fd: int) -> None:
                original_fsync(stage_fd)
                os.fchmod(stage_fd, 0o755)

            with patch(
                "omni_hub.discord_blogger_results._fsync_stage",
                side_effect=relax_directory_mode,
            ):
                with self.assertRaisesRegex(ValueError, "staging directory mode"):
                    _publish(
                        workspace=workspace,
                        output_dir=Path("published"),
                        source_manifest=_source_manifest(decision),
                    )
            self.assertFalse((workspace / "published").exists())

    def test_file_mode_change_before_rename_fails_closed(self) -> None:
        decision = _decision()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            import omni_hub.discord_blogger_results as results_module

            original_fsync = results_module._fsync_stage

            def relax_file_mode(stage_fd: int) -> None:
                original_fsync(stage_fd)
                descriptor = os.open(
                    "latest-calls.json",
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=stage_fd,
                )
                try:
                    os.fchmod(descriptor, 0o644)
                finally:
                    os.close(descriptor)

            with patch(
                "omni_hub.discord_blogger_results._fsync_stage",
                side_effect=relax_file_mode,
            ):
                with self.assertRaisesRegex(ValueError, "staging file mode"):
                    _publish(
                        workspace=workspace,
                        output_dir=Path("published"),
                        source_manifest=_source_manifest(decision),
                    )
            self.assertFalse((workspace / "published").exists())

    def test_file_mode_change_after_rename_quarantines_canonical(self) -> None:
        decision = _decision()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            import omni_hub.discord_blogger_results as results_module

            original_rename = results_module._rename_directory_noreplace_at

            def rename_then_relax_file(
                source_name: str, destination_name: str, parent_fd: int
            ) -> None:
                original_rename(source_name, destination_name, parent_fd)
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
                published_fd = os.open(destination_name, flags, dir_fd=parent_fd)
                try:
                    descriptor = os.open(
                        "latest-calls.json",
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=published_fd,
                    )
                    try:
                        os.fchmod(descriptor, 0o644)
                    finally:
                        os.close(descriptor)
                finally:
                    os.close(published_fd)

            with patch(
                "omni_hub.discord_blogger_results._rename_directory_noreplace_at",
                side_effect=rename_then_relax_file,
            ):
                with self.assertRaisesRegex(ValueError, "staging file mode"):
                    _publish(
                        workspace=workspace,
                        output_dir=Path("published"),
                        source_manifest=_source_manifest(decision),
                    )
            self.assertFalse((workspace / "published").exists())
            quarantines = list(workspace.glob(".published.quarantine-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(
                stat.S_IMODE((quarantines[0] / "latest-calls.json").stat().st_mode),
                0o644,
            )

    def test_stage_open_failure_cleans_the_owned_empty_stage(self) -> None:
        decision = _decision()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with patch(
                "omni_hub.discord_blogger_results._open_stage",
                side_effect=OSError("open failed"),
            ):
                with self.assertRaises(OSError):
                    _publish(
                        workspace=workspace,
                        output_dir=Path("published"),
                        source_manifest=_source_manifest(decision),
                    )
            self.assertEqual(list(workspace.glob(".published.stage-*")), [])

    def test_stage_parent_fsync_failure_closes_and_cleans_the_owned_stage(self) -> None:
        decision = _decision()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            import omni_hub.discord_blogger_results as results_module

            original_fsync = results_module.os.fsync
            failed = False

            def fail_once(descriptor: int) -> None:
                nonlocal failed
                if not failed:
                    failed = True
                    raise OSError("fsync failed")
                original_fsync(descriptor)

            with patch("omni_hub.discord_blogger_results.os.fsync", side_effect=fail_once):
                with self.assertRaises(OSError):
                    _publish(
                        workspace=workspace,
                        output_dir=Path("published"),
                        source_manifest=_source_manifest(decision),
                    )
            self.assertEqual(list(workspace.glob(".published.stage-*")), [])

    def test_persistent_fsync_failure_still_removes_the_owned_empty_stage(self) -> None:
        decision = _decision()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with patch(
                "omni_hub.discord_blogger_results.os.fsync",
                side_effect=OSError("fsync unavailable"),
            ):
                with self.assertRaises(OSError):
                    _publish(
                        workspace=workspace,
                        output_dir=Path("published"),
                        source_manifest=_source_manifest(decision),
                    )
            self.assertEqual(list(workspace.glob(".published.stage-*")), [])


if __name__ == "__main__":
    unittest.main()
