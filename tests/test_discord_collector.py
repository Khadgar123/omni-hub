from __future__ import annotations

import contextlib
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import unittest
import weakref
from typing import Any, Callable, Mapping
from unittest.mock import patch

import omni_hub.discord_collector as discord_collector_module
import omni_hub.discord_reference_sidecar as discord_reference_sidecar_module
from omni_hub.connectors.discord import (
    DiscordAPIError,
    DiscordMediaResolutionError,
    DiscordMediaResolutionInvalidAnswer,
    DiscordMediaResolutionReason,
    DiscordMediaSecurityError,
    rfc2544_fake_ip_media_policy_descriptor,
)
from omni_hub.discord_collector import (
    DiscordEvidenceCollector,
    resolve_output_root,
    validate_target_snapshot,
)
from omni_hub.discord_media_recovery import (
    LEGACY_RETRY_TRIGGER,
    RESOLUTION_RETRY_TRIGGER,
    media_resolution_context,
    validate_resolution_attempt_history,
)
from omni_hub.discord_media_audit import (
    MEDIA_RECOVERY_AUDIT_FILENAME,
    MEDIA_RECOVERY_AUDIT_VERSION,
)
from omni_hub.discord_reference_sidecar import (
    build_message_reference_resolution_audit,
)


def _route_key(
    path: str,
    params: Mapping[str, object] | None = None,
) -> tuple[str, str]:
    return path, json.dumps(dict(params or {}), sort_keys=True)


def _snowflake_at(timestamp: str, increment: int = 0) -> str:
    instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    milliseconds = int(instant.astimezone(UTC).timestamp() * 1000)
    return str(((milliseconds - 1_420_070_400_000) << 22) | increment)


def _complete_fixture_message(message: object, channel_id: str) -> object:
    if not isinstance(message, dict):
        return message
    message_id = message.get("id")
    if not isinstance(message_id, str) or not message_id.isdigit():
        return message
    milliseconds = (int(message_id) >> 22) + 1_420_070_400_000
    timestamp = datetime.fromtimestamp(milliseconds / 1000, UTC).isoformat()
    message.setdefault("channel_id", channel_id)
    message.setdefault("timestamp", timestamp)
    message.setdefault("edited_timestamp", None)
    message.setdefault("author", {"id": "1", "username": "fixture-author"})
    message.setdefault("content", "")
    message.setdefault("attachments", [])
    message.setdefault("embeds", [])
    message.setdefault("components", [])
    return message


class _ByteStream:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        content_type: str | None = "application/octet-stream",
        content_length: int | None = None,
    ) -> None:
        self._chunks = chunks
        self.content_type = content_type
        self.content_length = content_length
        self.closed = False

    def __enter__(self) -> "_ByteStream":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __iter__(self):  # type: ignore[no-untyped-def]
        yield from self._chunks

    def close(self) -> None:
        self.closed = True


class _InterruptingByteStream(_ByteStream):
    def __iter__(self):  # type: ignore[no-untyped-def]
        yield b"partial"
        raise KeyboardInterrupt


class _FixtureTransport:
    def __init__(
        self,
        *,
        bot_id: str = "9",
        base_url: str = "https://discord.com/api/v10",
        allow_rfc2544_fake_ip: bool = False,
    ) -> None:
        self.routes: dict[tuple[str, str], list[object]] = {}
        self.media: dict[str, list[object]] = {}
        self.json_calls: list[tuple[str, dict[str, object]]] = []
        self.media_calls: list[str] = []
        self.opened_streams: list[_ByteStream] = []
        self.bot_id = bot_id
        self.base_url = base_url
        self.allow_rfc2544_fake_ip = allow_rfc2544_fake_ip
        self.rfc2544_fake_ip_policy = (
            rfc2544_fake_ip_media_policy_descriptor()
            if allow_rfc2544_fake_ip
            else None
        )

    def add_json(
        self,
        path: str,
        payload: object,
        params: Mapping[str, object] | None = None,
    ) -> None:
        fixture = payload if isinstance(payload, BaseException) else deepcopy(payload)
        match = re.fullmatch(r"/channels/([0-9]+)/messages", path)
        if match is not None and isinstance(fixture, list):
            fixture = [
                _complete_fixture_message(message, match.group(1))
                for message in fixture
            ]
        pin_match = re.fullmatch(r"/channels/([0-9]+)/messages/pins", path)
        if pin_match is not None and isinstance(fixture, dict):
            items = fixture.get("items")
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and "message" in item:
                        item["message"] = _complete_fixture_message(
                            item["message"],
                            pin_match.group(1),
                        )
        self.routes.setdefault(_route_key(path, params), []).append(fixture)

    def add_media(self, url: str, outcome: object) -> None:
        self.media.setdefault(url, []).append(outcome)

    def get_json(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
    ) -> object:
        normalized = dict(params or {})
        self.json_calls.append((path, normalized))
        key = _route_key(path, normalized)
        outcomes = self.routes.get(key)
        if not outcomes:
            if path == "/users/@me" and normalized == {}:
                return {"id": self.bot_id, "fixture_default": True}
            raise AssertionError(f"unexpected JSON request: {path} {normalized!r}")
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def open_byte_stream(
        self,
        url: str,
        params: Mapping[str, object] | None = None,
        *,
        chunk_size: int = 64 * 1024,
    ) -> _ByteStream:
        del params, chunk_size
        self.media_calls.append(url)
        outcomes = self.media.get(url)
        if not outcomes:
            raise AssertionError(f"unexpected media request: {url}")
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if not isinstance(outcome, _ByteStream):
            raise AssertionError(f"invalid media fixture for {url}")
        self.opened_streams.append(outcome)
        return outcome

    def assert_exhausted(self, testcase: unittest.TestCase) -> None:
        unused_json = {key: values for key, values in self.routes.items() if values}
        unused_media = {key: values for key, values in self.media.items() if values}
        testcase.assertEqual(unused_json, {})
        testcase.assertEqual(unused_media, {})
        testcase.assertTrue(all(stream.closed for stream in self.opened_streams))


def _target(
    target_id: str,
    *,
    kind: str = "GUILD_TEXT (0)",
    name: str = "target",
    parent_id: str | None = None,
) -> dict[str, object]:
    return {
        "id": target_id,
        "kind": kind,
        "name": name,
        "parent_id": parent_id,
        "source_labels": [name],
        "future_target_field": {"retained": True},
    }


def _snapshot(*targets: dict[str, object]) -> dict[str, object]:
    return {
        "guild_id": "1",
        "targets": list(targets),
        "audit_notes": {"unknown": "retained"},
        "source": "unit-test",
    }


def _add_inventory(
    transport: _FixtureTransport,
    channels: list[dict[str, object]],
    *,
    active_threads: list[dict[str, object]] | None = None,
) -> None:
    transport.add_json("/users/@me", {"id": "9", "future": "bot"})
    transport.add_json("/guilds/1", {"id": "1", "name": "guild", "future": 7})
    transport.add_json("/guilds/1/channels", channels)
    transport.add_json(
        "/guilds/1/threads/active",
        {
            "threads": active_threads or [],
            "members": [],
            "future_active_field": True,
        },
    )


def _add_archive_triplet(
    transport: _FixtureTransport,
    parent_id: str,
    *,
    public: list[dict[str, object]] | None = None,
    private: object | None = None,
    joined: list[dict[str, object]] | None = None,
) -> None:
    transport.add_json(
        f"/channels/{parent_id}/threads/archived/public",
        {"threads": public or [], "members": [], "has_more": False},
        {"limit": 100},
    )
    transport.add_json(
        f"/channels/{parent_id}/threads/archived/private",
        private
        if private is not None
        else {"threads": [], "members": [], "has_more": False},
        {"limit": 100},
    )
    transport.add_json(
        f"/channels/{parent_id}/users/@me/threads/archived/private",
        {"threads": joined or [], "members": [], "has_more": False},
        {"limit": 100},
    )


def _add_empty_messages_and_pins(
    transport: _FixtureTransport,
    channel_id: str,
) -> None:
    transport.add_json(
        f"/channels/{channel_id}/messages",
        [],
        {"limit": 100},
    )
    transport.add_json(
        f"/channels/{channel_id}/messages/pins",
        {"items": [], "has_more": False},
        {"limit": 50},
    )


class DiscordCollectorValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_directory.name)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_target_snapshot_keeps_audit_kind_and_unknown_fields(self) -> None:
        snapshot = _snapshot(
            _target(
                "100",
                kind="legacy object; type absent from retained channel graph",
            )
        )

        validated = validate_target_snapshot(snapshot)

        self.assertEqual(validated, snapshot)

    def test_max_asset_bytes_is_an_exact_positive_integer(self) -> None:
        for value in (True, 1.5, 0, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DiscordEvidenceCollector(_FixtureTransport(), max_asset_bytes=value)  # type: ignore[arg-type]

        collector = DiscordEvidenceCollector(_FixtureTransport(), max_asset_bytes=1)
        self.assertEqual(collector._max_asset_bytes, 1)

    def test_target_snapshot_rejects_invalid_fields_and_duplicate_ids(self) -> None:
        invalid_snapshots = [
            {"guild_id": "", "targets": [_target("100")]},
            {"guild_id": "not-a-snowflake", "targets": [_target("100")]},
            {"guild_id": "1", "targets": "not-a-list"},
            _snapshot(_target("")),
            _snapshot(_target("not-a-snowflake")),
            _snapshot(_target("100", kind="  ")),
            _snapshot(_target("100", name="")),
            _snapshot(_target("100"), _target("100")),
            _snapshot({**_target("100"), "parent_id": "../escape"}),
            _snapshot({**_target("100"), "source_labels": ["ok", 7]}),
        ]
        for snapshot in invalid_snapshots:
            with self.subTest(snapshot=snapshot):
                with self.assertRaises(ValueError):
                    validate_target_snapshot(snapshot)

    def test_output_root_rejects_absolute_traversal_and_symlink_escape(self) -> None:
        outside = self.workspace.parent / f"{self.workspace.name}-outside"
        outside.mkdir(exist_ok=True)
        link = self.workspace / "linked"
        link.symlink_to(outside, target_is_directory=True)

        for output_dir in ("../escape", "/absolute", "linked/escape"):
            with self.subTest(output_dir=output_dir):
                with self.assertRaises(ValueError):
                    resolve_output_root(self.workspace, output_dir)

        self.assertEqual(
            resolve_output_root(self.workspace, "evidence"),
            self.workspace.resolve() / "evidence",
        )


class DiscordCollectorEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_directory.name)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def _collect_empty_voice_run(
        self,
        run_id: str,
        *,
        transport: _FixtureTransport | None = None,
        chunk_size: int = 64 * 1024,
        max_asset_bytes: int = 512 * 1024 * 1024,
        allow_rfc2544_fake_ip: bool = False,
    ) -> object:
        fixture = transport or _FixtureTransport()
        _add_inventory(fixture, [{"id": "300", "type": 2, "name": "voice"}])
        _add_empty_messages_and_pins(fixture, "300")
        result = DiscordEvidenceCollector(
            fixture,
            chunk_size=chunk_size,
            max_asset_bytes=max_asset_bytes,
            allow_rfc2544_fake_ip=allow_rfc2544_fake_ip,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id=run_id,
            download_assets=False,
        )
        fixture.assert_exhausted(self)
        return result

    def _configure_single_attachment_run(
        self,
        transport: _FixtureTransport,
        *,
        url: str,
        attachment_id: str = "400",
        size: int = 4,
        content_type: str = "image/png",
    ) -> dict[str, object]:
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        attachment: dict[str, object] = {
            "id": attachment_id,
            "filename": f"{attachment_id}.png",
            "url": url,
            "size": size,
            "content_type": content_type,
        }
        transport.add_json(
            "/channels/300/messages",
            [{"id": "30", "attachments": [attachment]}],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        return attachment

    def test_new_asset_without_byte_transport_is_durably_failed(self) -> None:
        transport = _FixtureTransport()
        url = "https://cdn.example/no-byte-transport.png"
        self._configure_single_attachment_run(transport, url=url)

        result = DiscordEvidenceCollector(
            transport,
            byte_transport=None,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="new-asset-no-byte-transport",
            download_assets=True,
        )

        transport.assert_exhausted(self)
        record = json.loads(
            next((result.run_root / "asset-records").glob("*.json")).read_text()
        )
        self.assertEqual(transport.media_calls, [])
        self.assertEqual(record["status"], "failed")
        self.assertEqual(
            record["terminal_reason"],
            "byte_transport_unavailable",
        )
        self.assertEqual(record["attempt_history"], [])
        self.assertEqual(result.manifest["media"]["failed"], 1)

    def _replace_request_with_legacy_v1(
        self,
        run_root: Path,
        *,
        minimal_options: bool,
    ) -> bytes:
        request_path = run_root / "request.json"
        request = json.loads(request_path.read_text())
        request["version"] = 1
        request.pop("identity", None)
        schema = request.pop("schema", {})
        telemetry = request.pop("telemetry", {})
        options = request["options"]
        options.pop("asset_chunk_size", None)
        if minimal_options:
            options.pop("max_asset_bytes", None)
            options.pop("message_evidence_schema_version", None)
        else:
            options["asset_chunk_size"] = telemetry["initial_asset_chunk_size"]
            options["message_evidence_schema_version"] = schema[
                "message_evidence_version"
            ]
        content = (
            json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        request_path.write_bytes(content)
        amendment_path = run_root / "request-v2-amendment.json"
        if amendment_path.exists():
            amendment_path.unlink()
        checkpoint_path = run_root / "checkpoint.json"
        checkpoint = json.loads(checkpoint_path.read_text())
        checkpoint.pop("request_sha256", None)
        checkpoint.pop("request_amendment_sha256", None)
        checkpoint.pop("request_migration_marker_sha256", None)
        checkpoint.pop("request_telemetry", None)
        checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
        return content

    def test_checkpoint_reports_busy_wal_reader(self) -> None:
        run_root = self.workspace / "busy-checkpoint"
        run_root.mkdir()
        ledger = discord_collector_module._AssetLedger(
            run_root,
            create_if_missing=True,
        )
        reader = sqlite3.connect(run_root / "asset-ledger.sqlite3")
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM asset_metadata").fetchall()
            ledger.register_existing("asset", "asset.json", "a" * 64)
            ledger.connection.execute("PRAGMA busy_timeout = 1")
            with self.assertRaisesRegex(RuntimeError, "checkpoint did not complete"):
                ledger.checkpoint()
        finally:
            reader.rollback()
            reader.close()
            ledger.close()

    def test_interrupted_collection_preserves_primary_exception_when_finalization_is_busy(
        self,
    ) -> None:
        collector = DiscordEvidenceCollector(_FixtureTransport())
        primary = KeyboardInterrupt("primary collection interruption")
        with (
            patch.object(collector, "_collect_all", side_effect=primary),
            patch.object(
                discord_collector_module._AssetLedger,
                "checkpoint",
                side_effect=RuntimeError("simulated WAL busy"),
            ),
        ):
            with self.assertRaises(KeyboardInterrupt) as caught:
                collector.collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                    run_id="primary-exception",
                    download_assets=False,
                )

        self.assertIs(caught.exception, primary)
        notes = getattr(caught.exception, "__notes__", [])
        self.assertTrue(
            any("interrupted finalization failed" in note for note in notes),
            notes,
        )
        self.assertTrue(
            any("asset ledger close failed" in note for note in notes),
            notes,
        )

    def test_transient_endpoint_errors_remain_resumable(self) -> None:
        cases = (
            (429, "http_429"),
            (500, "http_5xx"),
            (None, "network_error"),
        )
        snapshot = _snapshot(_target("300", kind="GUILD_VOICE (2)"))
        for index, (status_code, reason) in enumerate(cases):
            with self.subTest(status_code=status_code):
                run_id = f"transient-endpoint-{index}"
                first = _FixtureTransport()
                _add_inventory(
                    first,
                    [{"id": "300", "type": 2, "name": "voice"}],
                )
                first.add_json(
                    "/channels/300/messages",
                    [{"id": "30", "content": "landed"}],
                    {"limit": 100},
                )
                first.add_json(
                    "/channels/300/messages",
                    DiscordAPIError(
                        "transient",
                        status_code=status_code,
                        path="/channels/300/messages",
                    ),
                    {"limit": 100, "before": "30"},
                )
                first_result = DiscordEvidenceCollector(first).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=snapshot,
                    run_id=run_id,
                    download_assets=False,
                )
                first.assert_exhausted(self)
                first_state = first_result.manifest["streams"]["messages_300"]
                self.assertEqual(first_state["status"], "in_progress")
                self.assertEqual(first_state["terminal_reason"], reason)
                self.assertEqual(first_state["pages"], 1)
                self.assertEqual(first_state["next_cursor"], "30")

                resumed = _FixtureTransport()
                resumed.add_json(
                    "/channels/300/messages",
                    [],
                    {"limit": 100, "before": "30"},
                )
                resumed.add_json(
                    "/channels/300/messages/pins",
                    {"items": [], "has_more": False},
                    {"limit": 50},
                )
                resumed_result = DiscordEvidenceCollector(resumed).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=snapshot,
                    run_id=run_id,
                    download_assets=False,
                )
                resumed.assert_exhausted(self)
                self.assertEqual(resumed_result.manifest["status"], "complete")
                self.assertEqual(
                    resumed_result.manifest["streams"]["messages_300"]["pages"],
                    2,
                )

    def test_permanent_endpoint_error_is_not_retried(self) -> None:
        first = _FixtureTransport()
        _add_inventory(first, [{"id": "300", "type": 2, "name": "voice"}])
        first.add_json(
            "/channels/300/messages",
            DiscordAPIError(
                "bad request",
                status_code=400,
                path="/channels/300/messages",
            ),
            {"limit": 100},
        )
        snapshot = _snapshot(_target("300", kind="GUILD_VOICE (2)"))
        first_result = DiscordEvidenceCollector(first).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=snapshot,
            run_id="permanent-endpoint",
            download_assets=False,
        )
        first.assert_exhausted(self)
        self.assertEqual(
            first_result.manifest["streams"]["messages_300"]["status"],
            "failed",
        )
        self.assertEqual(
            first_result.manifest["streams"]["messages_300"]["terminal_reason"],
            "http_400",
        )

        resumed = _FixtureTransport()
        resumed_result = DiscordEvidenceCollector(resumed).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=snapshot,
            run_id="permanent-endpoint",
            download_assets=False,
        )
        resumed.assert_exhausted(self)
        self.assertEqual(resumed_result.manifest["status"], "partial")

    def test_request_v2_migrates_legacy_v1_and_binds_principal_and_origin(
        self,
    ) -> None:
        initial = self._collect_empty_voice_run("request-v1-migration")
        legacy_bytes = self._replace_request_with_legacy_v1(
            initial.run_root,
            minimal_options=True,
        )

        resumed = DiscordEvidenceCollector(
            _FixtureTransport(),
            chunk_size=32 * 1024,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="request-v1-migration",
            download_assets=False,
        )

        self.assertEqual((resumed.run_root / "request.json").read_bytes(), legacy_bytes)
        amendment_path = resumed.run_root / "request-v2-amendment.json"
        amendment = json.loads(amendment_path.read_text())
        self.assertEqual(amendment["version"], 2)
        self.assertEqual(amendment["kind"], "discord_request_v1_amendment")
        self.assertEqual(
            amendment["base_request_sha256"],
            hashlib.sha256(legacy_bytes).hexdigest(),
        )
        self.assertEqual(amendment["identity"]["bot_principal_id"], "9")
        self.assertEqual(
            amendment["identity"]["api_origin"],
            "https://discord.com",
        )
        self.assertEqual(amendment["schema"]["message_evidence_version"], 2)
        self.assertEqual(
            amendment["legacy_origin_status"],
            "legacy_origin_unproven",
        )
        self.assertRegex(amendment["effective_identity_sha256"], r"^[0-9a-f]{64}$")
        checkpoint = json.loads((resumed.run_root / "checkpoint.json").read_text())
        self.assertEqual(
            checkpoint["request_amendment_sha256"],
            hashlib.sha256(amendment_path.read_bytes()).hexdigest(),
        )

        same_identity = DiscordEvidenceCollector(
            _FixtureTransport(),
            chunk_size=16 * 1024,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="request-v1-migration",
            download_assets=False,
        )
        self.assertEqual(same_identity.manifest["status"], "complete")

    def test_legacy_v1_migration_cannot_rebind_recorded_bot_principal(self) -> None:
        initial = self._collect_empty_voice_run("legacy-principal-rebind")
        self._replace_request_with_legacy_v1(
            initial.run_root,
            minimal_options=True,
        )

        with self.assertRaisesRegex(ValueError, "legacy bot principal"):
            DiscordEvidenceCollector(_FixtureTransport(bot_id="10")).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="legacy-principal-rebind",
                download_assets=False,
            )
        self.assertFalse(
            (initial.run_root / "request-v2-amendment.json").exists()
        )

    def test_legacy_v1_unproven_origin_requires_canonical_discord_origin(self) -> None:
        initial = self._collect_empty_voice_run("legacy-origin-rebind")
        self._replace_request_with_legacy_v1(
            initial.run_root,
            minimal_options=True,
        )

        with self.assertRaisesRegex(ValueError, "legacy API origin is unproven"):
            DiscordEvidenceCollector(
                _FixtureTransport(
                    base_url="https://gateway.example/api/v10",
                )
            ).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="legacy-origin-rebind",
                download_assets=False,
            )
        self.assertFalse(
            (initial.run_root / "request-v2-amendment.json").exists()
        )

    def test_request_v2_rejects_missing_or_tampered_amendment_after_migration(
        self,
    ) -> None:
        for mode in ("missing", "tampered"):
            with self.subTest(mode=mode):
                run_id = f"request-amendment-{mode}"
                initial = self._collect_empty_voice_run(run_id)
                self._replace_request_with_legacy_v1(
                    initial.run_root,
                    minimal_options=False,
                )
                DiscordEvidenceCollector(_FixtureTransport()).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                    run_id=run_id,
                    download_assets=False,
                )
                amendment_path = initial.run_root / "request-v2-amendment.json"
                if mode == "missing":
                    amendment_path.unlink()
                else:
                    amendment_path.write_bytes(amendment_path.read_bytes() + b" ")
                with self.assertRaisesRegex(ValueError, "request amendment"):
                    DiscordEvidenceCollector(_FixtureTransport()).collect(
                        workspace=self.workspace,
                        output_dir="evidence",
                        targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                        run_id=run_id,
                        download_assets=False,
                    )

    def test_legacy_migration_marker_rejects_checkpoint_field_rollback(self) -> None:
        initial = self._collect_empty_voice_run("migration-marker-rollback")
        self._replace_request_with_legacy_v1(
            initial.run_root,
            minimal_options=True,
        )
        DiscordEvidenceCollector(_FixtureTransport()).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="migration-marker-rollback",
            download_assets=False,
        )
        amendment_path = initial.run_root / "request-v2-amendment.json"
        marker_path = initial.run_root / "request-v2-migration-marker.json"
        self.assertTrue(marker_path.is_file())
        amendment_path.unlink()
        checkpoint_path = initial.run_root / "checkpoint.json"
        checkpoint = json.loads(checkpoint_path.read_text())
        checkpoint.pop("request_amendment_sha256")
        checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "request amendment is missing"):
            DiscordEvidenceCollector(_FixtureTransport()).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="migration-marker-rollback",
                download_assets=False,
            )

    def test_exact_legacy_amendment_can_recover_pre_checkpoint_crash_window(
        self,
    ) -> None:
        for marker_durable in (False, True):
            with self.subTest(marker_durable=marker_durable):
                suffix = "marker" if marker_durable else "amendment"
                run_id = f"migration-crash-window-{suffix}"
                initial = self._collect_empty_voice_run(run_id)
                self._replace_request_with_legacy_v1(
                    initial.run_root,
                    minimal_options=True,
                )
                DiscordEvidenceCollector(_FixtureTransport()).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                    run_id=run_id,
                    download_assets=False,
                )
                marker_path = (
                    initial.run_root / "request-v2-migration-marker.json"
                )
                if not marker_durable:
                    marker_path.unlink()
                checkpoint_path = initial.run_root / "checkpoint.json"
                checkpoint = json.loads(checkpoint_path.read_text())
                for field in (
                    "request_sha256",
                    "request_amendment_sha256",
                    "request_migration_marker_sha256",
                    "request_telemetry",
                ):
                    checkpoint.pop(field, None)
                checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

                resumed = DiscordEvidenceCollector(_FixtureTransport()).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                    run_id=run_id,
                    download_assets=False,
                )
                self.assertEqual(resumed.manifest["status"], "complete")
                self.assertTrue(marker_path.is_file())

    def test_request_v2_rejects_principal_and_api_origin_changes(self) -> None:
        cases = (
            ("principal", _FixtureTransport(bot_id="10"), "bot principal"),
            (
                "origin",
                _FixtureTransport(base_url="https://gateway.example/api/v10"),
                "API origin",
            ),
        )
        for suffix, changed_transport, message in cases:
            with self.subTest(change=suffix):
                run_id = f"request-identity-{suffix}"
                self._collect_empty_voice_run(run_id)
                with self.assertRaisesRegex(ValueError, message):
                    DiscordEvidenceCollector(changed_transport).collect(
                        workspace=self.workspace,
                        output_dir="evidence",
                        targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                        run_id=run_id,
                        download_assets=False,
                    )

    def test_request_chunk_size_is_telemetry_but_size_policy_is_identity(self) -> None:
        initial = self._collect_empty_voice_run(
            "request-policy",
            chunk_size=1024,
            max_asset_bytes=4096,
        )
        request = json.loads((initial.run_root / "request.json").read_text())
        self.assertEqual(request["version"], 2)
        self.assertNotIn("asset_chunk_size", request["options"])
        self.assertEqual(request["telemetry"]["initial_asset_chunk_size"], 1024)

        resumed = DiscordEvidenceCollector(
            _FixtureTransport(),
            chunk_size=2048,
            max_asset_bytes=4096,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="request-policy",
            download_assets=False,
        )
        self.assertEqual(resumed.manifest["status"], "complete")
        checkpoint = json.loads((resumed.run_root / "checkpoint.json").read_text())
        self.assertEqual(
            checkpoint["request_telemetry"]["asset_chunk_sizes_observed"],
            [1024, 2048],
        )

        with self.assertRaisesRegex(ValueError, "request identity"):
            DiscordEvidenceCollector(
                _FixtureTransport(),
                chunk_size=2048,
                max_asset_bytes=8192,
            ).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="request-policy",
                download_assets=False,
            )

    def test_rfc2544_fake_ip_opt_in_is_bound_into_request_identity(self) -> None:
        initial = self._collect_empty_voice_run(
            "request-fake-ip-policy",
            allow_rfc2544_fake_ip=True,
        )
        request = json.loads((initial.run_root / "request.json").read_text())
        self.assertIs(request["options"]["allow_rfc2544_fake_ip"], True)
        policy = request["options"]["rfc2544_fake_ip_policy"]
        canonical_hosts = json.dumps(
            policy["hosts"],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        self.assertEqual(
            policy["hosts_sha256"],
            hashlib.sha256(canonical_hosts).hexdigest(),
        )
        self.assertEqual(policy["version"], "rfc2544_discord_media_v1")
        self.assertEqual(policy["network"], "198.18.0.0/15")
        self.assertEqual(policy["port"], 443)
        policy_inputs = {
            key: policy[key]
            for key in ("version", "network", "port", "hosts", "hosts_sha256")
        }
        self.assertEqual(
            policy["inputs_sha256"],
            hashlib.sha256(
                json.dumps(
                    policy_inputs,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("ascii")
            ).hexdigest(),
        )

        same_policy = DiscordEvidenceCollector(
            _FixtureTransport(),
            allow_rfc2544_fake_ip=True,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="request-fake-ip-policy",
            download_assets=False,
        )
        self.assertEqual(same_policy.manifest["status"], "complete")

        with self.assertRaisesRegex(ValueError, "request identity"):
            DiscordEvidenceCollector(_FixtureTransport()).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="request-fake-ip-policy",
                download_assets=False,
            )

    def test_request_policy_must_match_byte_transport_policy(self) -> None:
        transport = _FixtureTransport()
        transport.allow_rfc2544_fake_ip = True
        transport.rfc2544_fake_ip_policy = {"version": "tampered"}
        with self.assertRaisesRegex(ValueError, "byte transport policy"):
            DiscordEvidenceCollector(
                transport,
                byte_transport=transport,
                allow_rfc2544_fake_ip=False,
            )
        with self.assertRaisesRegex(ValueError, "byte transport policy"):
            DiscordEvidenceCollector(
                transport,
                byte_transport=transport,
                allow_rfc2544_fake_ip=True,
            )

    def test_byte_transport_policy_descriptor_is_mandatory_before_media_io(self) -> None:
        class DescriptorlessTransport:
            media_calls = 0

            def open_byte_stream(self, *_args: object, **_kwargs: object) -> object:
                self.media_calls += 1
                raise AssertionError("media I/O must not start")

        transport = DescriptorlessTransport()
        with self.assertRaisesRegex(ValueError, "byte transport policy"):
            DiscordEvidenceCollector(
                _FixtureTransport(),
                byte_transport=transport,
            )
        self.assertEqual(transport.media_calls, 0)

    def test_request_v2_missing_identity_is_not_silently_recreated(self) -> None:
        initial = self._collect_empty_voice_run("request-missing")
        request_path = initial.run_root / "request.json"
        request_path.unlink()
        with self.assertRaisesRegex(ValueError, "request identity is missing"):
            DiscordEvidenceCollector(_FixtureTransport()).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="request-missing",
                download_assets=False,
            )

    def test_collector_requires_explicit_transport_api_origin(self) -> None:
        transport = _FixtureTransport()
        del transport.base_url
        with self.assertRaisesRegex(ValueError, "API origin is unavailable"):
            DiscordEvidenceCollector(transport).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="missing-api-origin",
                download_assets=False,
            )

    def test_terminal_inventory_errors_do_not_issue_resume_requests(self) -> None:
        snapshot = _snapshot(_target("300", kind="GUILD_VOICE (2)"))
        for status_code in (400, 403, 404):
            with self.subTest(endpoint="guild", status_code=status_code):
                run_id = f"terminal-guild-{status_code}"
                first = _FixtureTransport()
                first.add_json("/users/@me", {"id": "9"})
                first.add_json(
                    "/guilds/1",
                    DiscordAPIError("terminal", status_code=status_code),
                )
                with self.assertRaises(DiscordAPIError):
                    DiscordEvidenceCollector(first).collect(
                        workspace=self.workspace,
                        output_dir="evidence",
                        targets=snapshot,
                        run_id=run_id,
                        download_assets=False,
                    )
                first.assert_exhausted(self)

                resumed = _FixtureTransport()
                with self.assertRaisesRegex(ValueError, "terminal inventory endpoint"):
                    DiscordEvidenceCollector(resumed).collect(
                        workspace=self.workspace,
                        output_dir="evidence",
                        targets=snapshot,
                        run_id=run_id,
                        download_assets=False,
                    )
                self.assertFalse(
                    any(path == "/guilds/1" for path, _params in resumed.json_calls)
                )

            with self.subTest(endpoint="active", status_code=status_code):
                run_id = f"terminal-active-{status_code}"
                first = _FixtureTransport()
                first.add_json("/users/@me", {"id": "9"})
                first.add_json("/guilds/1", {"id": "1"})
                first.add_json(
                    "/guilds/1/channels",
                    [{"id": "300", "type": 2, "name": "voice"}],
                )
                first.add_json(
                    "/guilds/1/threads/active",
                    DiscordAPIError("terminal", status_code=status_code),
                )
                _add_empty_messages_and_pins(first, "300")
                first_result = DiscordEvidenceCollector(first).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=snapshot,
                    run_id=run_id,
                    download_assets=False,
                )
                first.assert_exhausted(self)
                self.assertEqual(first_result.manifest["status"], "partial")

                resumed = _FixtureTransport()
                resumed_result = DiscordEvidenceCollector(resumed).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=snapshot,
                    run_id=run_id,
                    download_assets=False,
                )
                resumed.assert_exhausted(self)
                self.assertEqual(resumed_result.manifest["status"], "partial")
                self.assertFalse(
                    any(
                        path == "/guilds/1/threads/active"
                        for path, _params in resumed.json_calls
                    )
                )

    def test_sqlite_connect_rejects_inode_swap_before_mutating_replacement(self) -> None:
        run_root = self.workspace / "inode-swap"
        run_root.mkdir()
        ledger = discord_collector_module._AssetLedger(
            run_root,
            create_if_missing=True,
        )
        ledger.close()

        ledger_path = run_root / "asset-ledger.sqlite3"
        original_path = run_root / "original-ledger.sqlite3"
        replacement_path = run_root / "replacement.sqlite3"
        replacement_path.write_bytes(b"")
        real_connect = sqlite3.connect
        swapped = False

        def swap_before_connect(database: object, *args: object, **kwargs: object):
            nonlocal swapped
            if not swapped:
                swapped = True
                os.replace(ledger_path, original_path)
                os.replace(replacement_path, ledger_path)
            return real_connect(database, *args, **kwargs)

        with patch.object(
            discord_collector_module.sqlite3,
            "connect",
            side_effect=swap_before_connect,
        ):
            with self.assertRaisesRegex(ValueError, "changed during SQLite open"):
                discord_collector_module._AssetLedger(
                    run_root,
                    create_if_missing=False,
                )

        self.assertTrue(swapped)
        self.assertEqual(ledger_path.read_bytes(), b"")

    def test_blob_validation_cache_rehashes_same_path_same_size_after_mutation(self) -> None:
        run_root = self.workspace / "blob-cache"
        blob_bytes = b"good"
        digest = hashlib.sha256(blob_bytes).hexdigest()
        relative = Path("assets") / "sha256" / digest[:2] / f"{digest}.bin"
        blob = run_root / relative
        blob.parent.mkdir(parents=True)
        blob.write_bytes(blob_bytes)

        collector = object.__new__(DiscordEvidenceCollector)
        collector._run_root = run_root
        collector._blob_validation_cache = {}
        collector._validate_blob_reference(
            digest,
            relative.as_posix(),
            len(blob_bytes),
            label="cache fixture",
        )

        original_stat = blob.stat()
        blob.write_bytes(b"evil")
        os.utime(
            blob,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        with self.assertRaisesRegex(ValueError, "blob content mismatch"):
            collector._validate_blob_reference(
                digest,
                relative.as_posix(),
                len(blob_bytes),
                label="cache fixture",
            )

    def test_asset_index_is_streamed_without_buffered_atomic_write(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "attachments": [
                        {
                            "id": "asset",
                            "filename": "asset.bin",
                            "url": "https://cdn.example/streamed-index",
                        }
                    ],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        original_write = discord_collector_module._atomic_write_bytes

        def reject_buffered_index(path: Path, content: bytes) -> None:
            if path.name == "asset-index.jsonl":
                raise AssertionError("asset index must be streamed")
            original_write(path, content)

        with patch.object(
            discord_collector_module,
            "_atomic_write_bytes",
            side_effect=reject_buffered_index,
        ):
            result = DiscordEvidenceCollector(transport).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="streamed-index",
                download_assets=False,
            )

        self.assertEqual(
            len((result.run_root / "asset-index.jsonl").read_text().splitlines()),
            1,
        )
        transport.assert_exhausted(self)

    def test_asset_index_retries_when_generation_changes_before_mark(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "attachments": [
                        {
                            "id": "first",
                            "filename": "first.bin",
                            "url": "https://cdn.example/index-race-first",
                        }
                    ],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        collector = DiscordEvidenceCollector(transport)
        real_mark = discord_collector_module._AssetLedger.mark_index
        injected = False

        def race_before_mark(
            ledger: object,
            digest: str,
            *args: object,
            **kwargs: object,
        ) -> object:
            nonlocal injected
            if not injected:
                injected = True
                second = deepcopy(next(iter(collector._asset_records.values())))
                second_source = deepcopy(second["sources"][0])
                second_source["message_id"] = "31"
                second_metadata = deepcopy(second["declared_metadata"])
                second_metadata.update({"id": "second", "filename": "second.bin"})
                second.update(
                    {
                        "logical_key": "31:attachment:second",
                        "url": "https://cdn.example/index-race-second",
                        "declared_metadata": second_metadata,
                        "identity_metadata": {
                            **second["identity_metadata"],
                            "id": "second",
                        },
                        "sources": [second_source],
                        "observations": [
                            {
                                **deepcopy(second["observations"][0]),
                                "source": second_source,
                                "metadata": second_metadata,
                                "url": "https://cdn.example/index-race-second",
                            }
                        ],
                        "observed_urls": [
                            "https://cdn.example/index-race-second"
                        ],
                        "candidate_urls": [
                            "https://cdn.example/index-race-second"
                        ],
                    }
                )
                collector._asset_records[second["logical_key"]] = second
                collector._commit_asset_record(second)
            return real_mark(ledger, digest, *args, **kwargs)

        with patch.object(
            discord_collector_module._AssetLedger,
            "mark_index",
            new=race_before_mark,
        ):
            result = collector.collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="index-generation-race",
                download_assets=False,
            )

        self.assertTrue(injected)
        index_path = result.run_root / "asset-index.jsonl"
        self.assertEqual(len(index_path.read_text().splitlines()), 2)
        with closing(sqlite3.connect(result.run_root / "asset-ledger.sqlite3")) as connection:
            metadata = dict(connection.execute("SELECT key, value FROM asset_metadata"))
        self.assertEqual(metadata["records_generation"], metadata["index_generation"])
        self.assertEqual(
            metadata["asset_index_sha256"],
            hashlib.sha256(index_path.read_bytes()).hexdigest(),
        )

    def test_durable_publication_fsyncs_parent_after_replace_create_and_link(self) -> None:
        real_fsync = os.fsync
        real_replace = os.replace
        real_link = os.link

        def recording_fsync(events: list[str], descriptor: int) -> None:
            mode = os.fstat(descriptor).st_mode
            events.append("dir_fsync" if stat.S_ISDIR(mode) else "file_fsync")
            real_fsync(descriptor)

        atomic_events: list[str] = []

        def recording_replace(source: object, destination: object) -> None:
            atomic_events.append("replace")
            real_replace(source, destination)

        atomic_path = self.workspace / "durable" / "atomic.json"
        with patch.object(
            discord_collector_module.os,
            "fsync",
            side_effect=lambda descriptor: recording_fsync(
                atomic_events,
                descriptor,
            ),
        ), patch.object(
            discord_collector_module.os,
            "replace",
            side_effect=recording_replace,
        ):
            discord_collector_module._atomic_write_bytes(atomic_path, b"atomic")
        self.assertLess(atomic_events.index("file_fsync"), atomic_events.index("replace"))
        self.assertTrue(
            any(
                event == "dir_fsync"
                for event in atomic_events[atomic_events.index("replace") + 1 :]
            )
        )

        exclusive_events: list[str] = []
        exclusive_path = self.workspace / "durable" / "exclusive.json"
        with patch.object(
            discord_collector_module.os,
            "fsync",
            side_effect=lambda descriptor: recording_fsync(
                exclusive_events,
                descriptor,
            ),
        ):
            discord_collector_module._write_exclusive_bytes_or_same(
                exclusive_path,
                b"exclusive",
            )
        self.assertEqual(exclusive_events[0], "file_fsync")
        self.assertIn("dir_fsync", exclusive_events[1:])

        blob_events: list[str] = []

        def recording_link(source: object, destination: object) -> None:
            blob_events.append("link")
            real_link(source, destination)

        run_root = self.workspace / "durable-blob"
        (run_root / "assets" / "sha256").mkdir(parents=True)
        temporary = run_root / "assets" / ".asset-fixture"
        blob_bytes = b"blob"
        temporary.write_bytes(blob_bytes)
        collector = object.__new__(DiscordEvidenceCollector)
        collector._run_root = run_root
        collector._blob_validation_cache = {}
        digest = hashlib.sha256(blob_bytes).hexdigest()
        with patch.object(
            discord_collector_module.os,
            "fsync",
            side_effect=lambda descriptor: recording_fsync(
                blob_events,
                descriptor,
            ),
        ), patch.object(
            discord_collector_module.os,
            "link",
            side_effect=recording_link,
        ):
            collector._promote_blob(temporary, digest, "application/octet-stream")
        self.assertLess(blob_events.index("link"), len(blob_events) - 1)
        self.assertIn("dir_fsync", blob_events[blob_events.index("link") + 1 :])

    def test_exclusive_publication_never_leaves_a_partial_final_identity(self) -> None:
        destination = self.workspace / "exclusive-crash" / "evidence.jsonl"
        destination.parent.mkdir()
        with patch.object(
            discord_collector_module.os,
            "link",
            side_effect=KeyboardInterrupt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                discord_collector_module._write_exclusive_bytes_or_same(
                    destination,
                    b"complete evidence\n",
                )
        self.assertFalse(destination.exists())

        orphan = destination.parent / f".{destination.name}.power-loss"
        orphan.write_bytes(b"partial")
        digest = discord_collector_module._write_exclusive_bytes_or_same(
            destination,
            b"complete evidence\n",
        )
        self.assertEqual(destination.read_bytes(), b"complete evidence\n")
        self.assertEqual(
            digest,
            hashlib.sha256(b"complete evidence\n").hexdigest(),
        )

    def _collect_asset_batch_with_write_counts(
        self,
        *,
        asset_count: int,
        run_id: str,
    ) -> dict[str, int]:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        attachments = [
            {
                "id": str(1000 + index),
                "filename": f"asset-{index}.bin",
                "url": f"https://cdn.example/scale-{run_id}-{index}",
            }
            for index in range(asset_count)
        ]
        transport.add_json(
            "/channels/300/messages",
            [{"id": "30", "attachments": attachments}],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )

        writes: list[tuple[Path, int]] = []
        original = discord_collector_module._atomic_write_bytes
        original_chunks = discord_collector_module._atomic_write_chunks

        def record_write(path: Path, content: bytes) -> None:
            writes.append((path, len(content)))
            original(path, content)

        def record_chunk_write(path: Path, chunks: object) -> str:
            digest = original_chunks(path, chunks)
            writes.append((path, path.stat().st_size))
            return digest

        with patch.object(
            discord_collector_module,
            "_atomic_write_bytes",
            side_effect=record_write,
        ), patch.object(
            discord_collector_module,
            "_atomic_write_chunks",
            side_effect=record_chunk_write,
        ):
            DiscordEvidenceCollector(transport).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id=run_id,
                download_assets=False,
            )

        transport.assert_exhausted(self)
        return {
            "checkpoint": sum(path.name == "checkpoint.json" for path, _ in writes),
            "index": sum(path.name == "asset-index.jsonl" for path, _ in writes),
            "records": sum(
                path.parent.name == "asset-records" for path, _ in writes
            ),
            "global_bytes": sum(
                size
                for path, size in writes
                if path.name in {"checkpoint.json", "asset-index.jsonl"}
            ),
        }

    def test_asset_metadata_write_counts_are_linear_not_quadratic(self) -> None:
        small = self._collect_asset_batch_with_write_counts(
            asset_count=4,
            run_id="scale-small",
        )
        large = self._collect_asset_batch_with_write_counts(
            asset_count=8,
            run_id="scale-large",
        )

        self.assertEqual(small["index"], 1)
        self.assertEqual(large["index"], 1)
        self.assertEqual(large["checkpoint"], small["checkpoint"])
        self.assertEqual(large["records"], 2 * small["records"])
        self.assertLess(large["global_bytes"], 2.5 * small["global_bytes"])

    def test_nested_snapshot_media_and_delivery_evidence_are_durable(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        root_timestamp = "2026-07-19T00:00:00.123000+00:00"
        snapshot_timestamp = "2026-07-18T23:59:00.123000+00:00"
        root_id = _snowflake_at(root_timestamp, 1)
        referenced_id = _snowflake_at(snapshot_timestamp, 2)
        nested_url = "https://cdn.example/nested-snapshot.jpg"
        message = {
            "id": root_id,
            "channel_id": "300",
            "type": 0,
            "timestamp": root_timestamp,
            "edited_timestamp": None,
            "author": {"id": "700", "username": "delivery-hook"},
            "webhook_id": "700",
            "content": "outer delivery",
            "attachments": [],
            "embeds": [],
            "components": [],
            "message_reference": {
                "type": 1,
                "message_id": referenced_id,
                "channel_id": "300",
            },
            "message_snapshots": [
                {
                    "message": {
                        "type": 0,
                        "timestamp": snapshot_timestamp,
                        "edited_timestamp": None,
                        "content": "forwarded chart",
                        "attachments": [
                            {
                                "id": "9001",
                                "filename": "chart.jpg",
                                "size": 4,
                                "content_type": "image/jpeg",
                                "url": nested_url,
                            }
                        ],
                        "embeds": [],
                        "components": [],
                    }
                }
            ],
        }
        transport.add_json(
            "/channels/300/messages",
            [message],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": root_id},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(
            nested_url,
            _ByteStream([b"jpeg"], content_type="image/jpeg", content_length=4),
        )

        result = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="nested-message-evidence",
            download_assets=True,
        )

        evidence_path = (
            result.run_root / "message-evidence/messages_300/000001.jsonl"
        )
        evidence_rows = [
            json.loads(line) for line in evidence_path.read_text().splitlines()
        ]
        self.assertEqual(len(evidence_rows), 1)
        row = evidence_rows[0]
        self.assertEqual(row["status"], "complete")
        self.assertEqual([node["kind"] for node in row["nodes"]], ["root", "snapshot"])
        self.assertEqual(row["nodes"][0]["attribution"]["kind"], "webhook")
        self.assertEqual(
            row["nodes"][1]["attribution"]["kind"],
            "snapshot_unattributed",
        )
        self.assertIsNone(row["nodes"][1]["attribution"]["author_id"])
        nested_occurrence = next(
            occurrence
            for occurrence in row["media"]
            if occurrence["field"] == "attachment"
        )
        self.assertEqual(
            nested_occurrence["json_pointer"],
            "/payload/0/message_snapshots/0/message/attachments/0",
        )
        raw_page_hash = result.manifest["streams"]["messages_300"]["page_hashes"][0]
        self.assertEqual(
            nested_occurrence["source"]["evidence_sha256"],
            raw_page_hash,
        )
        records = [
            json.loads(line)
            for line in (result.run_root / "asset-index.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["logical_key"].endswith(":attachment:9001"))
        self.assertEqual(records[0]["status"], "complete")
        self.assertEqual(records[0]["actual_bytes"], 4)
        self.assertEqual(result.manifest["message_evidence"]["root_messages"], 1)
        self.assertEqual(result.manifest["message_evidence"]["partial_messages"], 0)
        self.assertEqual(result.manifest["message_evidence"]["media_occurrences"], 1)
        transport.assert_exhausted(self)

        evidence_path.write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "message evidence.*mismatch"):
            DiscordEvidenceCollector(_FixtureTransport()).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="nested-message-evidence",
                download_assets=True,
            )

    def test_root_and_pin_channel_identity_fail_closed(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [{"id": "30", "channel_id": "999"}],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {
                "items": [
                    {
                        "pinned_at": "2026-07-20T00:00:00",
                        "message": {"id": "31", "channel_id": "999"},
                    }
                ],
                "has_more": False,
            },
            {"limit": 50},
        )

        result = DiscordEvidenceCollector(transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="root-channel-validation",
            download_assets=False,
        )

        transport.assert_exhausted(self)
        self.assertEqual(result.manifest["status"], "partial")
        self.assertEqual(result.manifest["message_evidence"]["status"], "partial")
        message_validation = result.manifest["streams"][
            "messages_300_item_validation"
        ]
        pin_validation = result.manifest["streams"]["pins_300_item_validation"]
        self.assertEqual(message_validation["status"], "failed")
        self.assertEqual(
            message_validation["diagnostics"][0]["reason"],
            "channel_id_mismatch",
        )
        self.assertEqual(pin_validation["status"], "failed")
        self.assertEqual(
            pin_validation["diagnostics"][0]["reason"],
            "pinned_at_invalid",
        )

    def test_pin_event_and_descriptor_preserve_envelope_and_coalesce_assets(
        self,
    ) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        attachment_url = "https://cdn.example/pinned.png"
        message = {
            "id": "30",
            "attachments": [
                {
                    "id": "400",
                    "filename": "pinned.png",
                    "url": attachment_url,
                    "content_type": "image/png",
                    "size": 4,
                }
            ],
        }
        transport.add_json(
            "/channels/300/messages",
            [message],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        pinned_at = "2026-07-20T08:00:00+08:00"
        transport.add_json(
            "/channels/300/messages/pins",
            {
                "items": [{"pinned_at": pinned_at, "message": message}],
                "has_more": False,
            },
            {"limit": 50},
        )
        transport.add_media(
            attachment_url,
            _ByteStream([b"png!"], content_type="image/png", content_length=4),
        )

        result = DiscordEvidenceCollector(transport, byte_transport=transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="pin-event-evidence",
            download_assets=True,
        )

        transport.assert_exhausted(self)
        pin_rows = [
            json.loads(line)
            for line in (
                result.run_root / "message-evidence/pins_300/000001.jsonl"
            ).read_text().splitlines()
        ]
        self.assertEqual(len(pin_rows), 1)
        pin_event = pin_rows[0]["pin_event"]
        self.assertEqual(pin_event["pinned_at"], pinned_at)
        self.assertEqual(pin_event["pinned_at_utc"], "2026-07-20T00:00:00+00:00")
        self.assertEqual(pin_event["json_pointer"], "/payload/items/0")
        self.assertEqual(
            pin_event["event_key"],
            "pin_event:300:30:2026-07-20T00:00:00+00:00",
        )
        descriptor = result.manifest["streams"]["pins_300"]["page_states"][0][
            "message_evidence"
        ]
        self.assertEqual(descriptor["stream"], "pins_300")
        self.assertEqual(descriptor["channel_id"], "300")
        self.assertEqual(descriptor["page_number"], 1)
        self.assertEqual(descriptor["pin_events"], 1)
        self.assertEqual(
            descriptor["raw_page_sha256"],
            result.manifest["streams"]["pins_300"]["page_hashes"][0],
        )
        fetched_at = datetime.fromisoformat(descriptor["fetched_at"])
        self.assertIsNotNone(fetched_at.tzinfo)
        record = json.loads(
            next((result.run_root / "asset-records").glob("*.json")).read_text()
        )
        self.assertEqual(len(record["sources"]), 2)
        self.assertEqual(
            {source["stream"] for source in record["sources"]},
            {"messages_300", "pins_300"},
        )
        message_bounds = result.manifest["streams"]["messages_300"]["bounds"]
        self.assertEqual(message_bounds["high_water"]["id"], "30")
        self.assertEqual(message_bounds["low_water"]["id"], "30")
        self.assertEqual(
            message_bounds["fetched_at"]["source"],
            "collector_local_clock_after_response",
        )
        self.assertIsNotNone(
            datetime.fromisoformat(
                message_bounds["fetched_at"]["first_response"]
            ).tzinfo
        )
        self.assertEqual(transport.media_calls, [attachment_url])

    def test_message_page_order_violation_fails_stream_and_run(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [{"id": "30"}, {"id": "31"}],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "31"},
        )

        result = DiscordEvidenceCollector(transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="message-order-invalid",
            download_assets=False,
        )

        transport.assert_exhausted(self)
        self.assertEqual(result.manifest["status"], "partial")
        self.assertEqual(
            result.manifest["streams"]["messages_300"]["status"],
            "failed",
        )
        order = result.manifest["streams"]["messages_300_order_validation"]
        self.assertEqual(order["status"], "failed")
        self.assertIn(
            "message_ids_not_strictly_descending",
            {item["reason"] for item in order["diagnostics"]},
        )

    def test_pin_order_and_duplicate_event_fail_closed(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100},
        )
        earlier = "2026-07-20T00:00:00+00:00"
        later = "2026-07-20T01:00:00+00:00"
        transport.add_json(
            "/channels/300/messages/pins",
            {
                "items": [
                    {"pinned_at": earlier, "message": {"id": "40"}},
                    {"pinned_at": later, "message": {"id": "41"}},
                    {"pinned_at": earlier, "message": {"id": "40"}},
                ],
                "has_more": False,
            },
            {"limit": 50},
        )

        result = DiscordEvidenceCollector(transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="pin-order-invalid",
            download_assets=False,
        )

        transport.assert_exhausted(self)
        order = result.manifest["streams"]["pins_300_order_validation"]
        self.assertEqual(order["status"], "failed")
        reasons = {item["reason"] for item in order["diagnostics"]}
        self.assertIn("pinned_at_not_non_increasing", reasons)
        self.assertIn("pin_event_duplicate", reasons)
        self.assertEqual(result.manifest["message_evidence"]["status"], "partial")
        self.assertEqual(result.manifest["status"], "partial")

    def test_message_warning_propagates_without_becoming_partial(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        root_timestamp = "2026-07-20T00:00:00.000000+00:00"
        snapshot_timestamp = "2026-07-19T00:00:00.000000+00:00"
        root_id = _snowflake_at(root_timestamp)
        unrelated_reference_id = _snowflake_at("2026-07-18T00:00:00+00:00")
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": root_id,
                    "timestamp": root_timestamp,
                    "message_reference": {
                        "type": 1,
                        "message_id": unrelated_reference_id,
                        "channel_id": "300",
                    },
                    "message_snapshots": [
                        {
                            "message": {
                                "timestamp": snapshot_timestamp,
                                "edited_timestamp": None,
                            }
                        }
                    ],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": root_id},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )

        result = DiscordEvidenceCollector(transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="message-warning",
            download_assets=False,
        )

        transport.assert_exhausted(self)
        self.assertEqual(
            result.manifest["message_evidence"]["status"],
            "complete_with_warnings",
        )
        self.assertEqual(
            result.manifest["message_evidence"]["diagnostics_by_severity"][
                "warning"
            ],
            1,
        )
        self.assertEqual(result.manifest["status"], "complete_with_warnings")

    def test_resume_rejects_raw_and_message_evidence_symlinks(self) -> None:
        for target_kind in ("raw", "evidence"):
            with self.subTest(target_kind=target_kind):
                transport = _FixtureTransport()
                _add_inventory(
                    transport,
                    [{"id": "300", "type": 2, "name": "voice"}],
                )
                transport.add_json(
                    "/channels/300/messages",
                    [{"id": "30"}],
                    {"limit": 100},
                )
                transport.add_json(
                    "/channels/300/messages",
                    [],
                    {"limit": 100, "before": "30"},
                )
                transport.add_json(
                    "/channels/300/messages/pins",
                    {"items": [], "has_more": False},
                    {"limit": 50},
                )
                run_id = f"unsafe-{target_kind}-page"
                result = DiscordEvidenceCollector(transport).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                    run_id=run_id,
                    download_assets=False,
                )
                transport.assert_exhausted(self)
                target = (
                    result.run_root / "pages/messages_300/000001.json"
                    if target_kind == "raw"
                    else result.run_root
                    / "message-evidence/messages_300/000001.jsonl"
                )
                backup = result.run_root / f"{target_kind}-page-backup"
                target.rename(backup)
                target.symlink_to(backup)

                with self.assertRaisesRegex(ValueError, "missing or unsafe"):
                    DiscordEvidenceCollector(_FixtureTransport()).collect(
                        workspace=self.workspace,
                        output_dir="evidence",
                        targets=_snapshot(
                            _target("300", kind="GUILD_VOICE (2)")
                        ),
                        run_id=run_id,
                        download_assets=False,
                    )

    def test_resume_rejects_message_evidence_descriptor_rebinding(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [{"id": "30"}],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        result = DiscordEvidenceCollector(transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="descriptor-rebinding",
            download_assets=False,
        )
        transport.assert_exhausted(self)
        checkpoint_path = result.run_root / "checkpoint.json"
        checkpoint = json.loads(checkpoint_path.read_text())
        descriptor = checkpoint["streams"]["messages_300"]["page_states"][0][
            "message_evidence"
        ]
        descriptor["raw_page_path"] = "pages/pins_300/000001.json"
        checkpoint_path.write_text(
            json.dumps(checkpoint, sort_keys=True, separators=(",", ":")) + "\n"
        )

        with self.assertRaisesRegex(ValueError, "evidence identity mismatch"):
            DiscordEvidenceCollector(_FixtureTransport()).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="descriptor-rebinding",
                download_assets=False,
            )

    def test_paginated_resume_never_uses_bulk_payload_reload(self) -> None:
        initial = _FixtureTransport()
        _add_inventory(initial, [{"id": "300", "type": 2, "name": "voice"}])
        initial.add_json(
            "/channels/300/messages",
            [{"id": "30"}],
            {"limit": 100},
        )
        initial.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        initial.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        snapshot = _snapshot(_target("300", kind="GUILD_VOICE (2)"))
        DiscordEvidenceCollector(initial).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=snapshot,
            run_id="streaming-resume",
            download_assets=False,
        )
        initial.assert_exhausted(self)

        resumed = _FixtureTransport()
        with patch.object(
            DiscordEvidenceCollector,
            "_stored_payloads",
            side_effect=AssertionError("bulk payload reload is forbidden"),
        ):
            result = DiscordEvidenceCollector(resumed).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=snapshot,
                run_id="streaming-resume",
                download_assets=False,
            )

        self.assertEqual(result.manifest["status"], "complete")
        self.assertEqual(resumed.json_calls, [("/users/@me", {})])

    def test_processed_page_replay_holds_only_constant_payloads(self) -> None:
        class TrackedPayload(list[object]):
            pass

        page_count = 1_500
        collector = DiscordEvidenceCollector(_FixtureTransport())
        collector._checkpoint = {
            "run_id": "constant-memory-replay",
            "errors": [],
            "assets": {},
            "streams": {
                "messages_300": {
                    **collector._new_page_stream_state(),
                    "status": "complete",
                    "pages": page_count,
                    "processed_pages": page_count,
                    "page_hashes": ["0" * 64] * page_count,
                    "page_states": [
                        {
                            "processing_status": "processed",
                            "next_cursor": None,
                            "terminal_status": "complete",
                            "terminal_reason": "empty_page",
                        }
                        for _ in range(page_count)
                    ],
                }
            },
        }
        collector._max_pages = None
        references: list[weakref.ReferenceType[TrackedPayload]] = []
        peak_live = 0

        def documents(_stream_key: str):  # type: ignore[no-untyped-def]
            nonlocal peak_live
            for page_number in range(page_count):
                payload = TrackedPayload([page_number])
                references.append(weakref.ref(payload))
                peak_live = max(
                    peak_live,
                    sum(reference() is not None for reference in references),
                )
                yield {"payload": payload}

        processed = 0

        def observe(_payload: object, _page: int, _digest: str) -> None:
            nonlocal processed
            processed += 1

        factory_calls = 0

        def no_network(_before: str | None, _remaining: int | None):
            nonlocal factory_calls
            factory_calls += 1
            return iter(())

        with (
            patch.object(collector, "_save_checkpoint"),
            patch.object(
                collector,
                "_stored_page_documents",
                side_effect=documents,
            ),
        ):
            for payload in collector._collect_paginated(
                "messages_300",
                no_network,
                complete_reason="empty_page",
                process_payload=observe,
            ):
                self.assertEqual(len(payload), 1)
        del payload

        self.assertEqual(processed, page_count)
        self.assertEqual(factory_calls, 0)
        self.assertLessEqual(peak_live, 2)

    def test_unchanged_resume_does_not_rewrite_complete_record_or_index(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        attachment = {
            "id": "asset",
            "filename": "asset.png",
            "url": "https://cdn.example/no-op-resume",
            "content_type": "image/png",
            "size": 4,
        }
        transport.add_json(
            "/channels/300/messages",
            [{"id": "30", "attachments": [attachment]}],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(
            attachment["url"],
            _ByteStream([b"data"], content_type="image/png", content_length=4),
        )
        result = DiscordEvidenceCollector(transport, byte_transport=transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="no-op-resume",
            download_assets=True,
        )
        transport.assert_exhausted(self)
        record_path = next((result.run_root / "asset-records").glob("*.json"))
        index_path = result.run_root / "asset-index.jsonl"
        original_record = record_path.read_bytes()
        original_index = index_path.read_bytes()

        writes: list[Path] = []
        original = discord_collector_module._atomic_write_bytes

        def record_write(path: Path, content: bytes) -> None:
            writes.append(path)
            original(path, content)

        with patch.object(
            discord_collector_module,
            "_atomic_write_bytes",
            side_effect=record_write,
        ):
            resumed = DiscordEvidenceCollector(_FixtureTransport()).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="no-op-resume",
                download_assets=True,
            )

        self.assertEqual(resumed.manifest["status"], "complete")
        self.assertFalse(any(path.parent.name == "asset-records" for path in writes))
        self.assertFalse(any(path.name == "asset-index.jsonl" for path in writes))
        self.assertEqual(record_path.read_bytes(), original_record)
        self.assertEqual(index_path.read_bytes(), original_index)

    def test_resume_hashes_each_unique_blob_only_once(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        attachments = [
            {
                "id": str(1000 + index),
                "filename": f"same-{index}.png",
                "url": f"https://cdn.example/same-{index}",
                "content_type": "image/png",
                "size": 4,
            }
            for index in range(2)
        ]
        transport.add_json(
            "/channels/300/messages",
            [{"id": "30", "attachments": attachments}],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        for attachment in attachments:
            transport.add_media(
                attachment["url"],
                _ByteStream([b"same"], content_type="image/png", content_length=4),
            )
        result = DiscordEvidenceCollector(transport, byte_transport=transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="one-hash-per-digest",
            download_assets=True,
        )
        transport.assert_exhausted(self)
        blob_root = result.run_root / "assets" / "sha256"
        blob_hashes: list[Path] = []
        original_hash = discord_collector_module._sha256_file

        def record_hash(path: Path) -> str:
            if blob_root in path.parents:
                blob_hashes.append(path)
            return original_hash(path)

        with patch.object(
            discord_collector_module,
            "_sha256_file",
            side_effect=record_hash,
        ):
            DiscordEvidenceCollector(_FixtureTransport()).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="one-hash-per-digest",
                download_assets=True,
            )

        self.assertEqual(len(blob_hashes), 1)

    def test_declared_media_type_failure_is_not_retried_on_unchanged_url(
        self,
    ) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        attachment = {
            "id": "asset",
            "filename": "asset.png",
            "url": "https://cdn.example/permanent-mime-failure",
            "content_type": "image/png",
            "size": 4,
        }
        transport.add_json(
            "/channels/300/messages",
            [{"id": "30", "attachments": [attachment]}],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(
            attachment["url"],
            _ByteStream([b"html"], content_type="text/html", content_length=4),
        )
        result = DiscordEvidenceCollector(transport, byte_transport=transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="permanent-failure",
            download_assets=True,
        )
        record_path = next((result.run_root / "asset-records").glob("*.json"))
        original = json.loads(record_path.read_text())
        self.assertEqual(
            original["terminal_reason"],
            "declared_media_type_mismatch",
        )
        self.assertEqual(original["status"], "failed")
        transport.assert_exhausted(self)

        resumed_transport = _FixtureTransport()
        resumed = DiscordEvidenceCollector(
            resumed_transport,
            byte_transport=resumed_transport,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="permanent-failure",
            download_assets=True,
        )
        persisted = json.loads(record_path.read_text())

        self.assertEqual(resumed.manifest["status"], "partial")
        self.assertEqual(resumed.manifest["media"]["status"], "partial")
        self.assertEqual(resumed.manifest["media"]["failed"], 1)
        self.assertEqual(resumed_transport.media_calls, [])
        self.assertEqual(persisted["attempt_history"], original["attempt_history"])

    def test_sqlite_asset_ledger_is_checkpointed_and_self_contained(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "attachments": [
                        {
                            "id": "asset",
                            "filename": "asset.bin",
                            "url": "https://cdn.example/ledger",
                        }
                    ],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        result = DiscordEvidenceCollector(transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="sqlite-ledger",
            download_assets=False,
        )
        transport.assert_exhausted(self)

        ledger_path = result.run_root / "asset-ledger.sqlite3"
        checkpoint = json.loads((result.run_root / "checkpoint.json").read_text())
        self.assertTrue(ledger_path.is_file())
        self.assertFalse(ledger_path.is_symlink())
        self.assertEqual(checkpoint.get("assets", {}), {})
        self.assertEqual(checkpoint["asset_ledger"], {"backend": "sqlite", "version": 1})
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(ledger_path) + suffix)
            self.assertTrue(not sidecar.exists() or sidecar.stat().st_size == 0)

        copied = self.workspace / "ledger-copy.sqlite3"
        shutil.copyfile(ledger_path, copied)
        with closing(sqlite3.connect(copied)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM asset_records").fetchone()[0],
                1,
            )
            row = connection.execute(
                "SELECT committed_sha256, pending_sha256 FROM asset_records"
            ).fetchone()
        self.assertRegex(row[0], r"^[0-9a-f]{64}$")
        self.assertIsNone(row[1])

        ledger_path.unlink()
        with self.assertRaisesRegex(ValueError, "ledger database is missing"):
            DiscordEvidenceCollector(_FixtureTransport()).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="sqlite-ledger",
                download_assets=False,
            )

    def test_sqlite_ledger_paths_reject_symlinks_before_collection(self) -> None:
        outside = self.workspace / "outside-ledger"
        outside.write_bytes(b"unchanged")
        for suffix in ("", "-wal", "-shm"):
            with self.subTest(suffix=suffix):
                run_id = "ledger-link-" + (suffix.removeprefix("-") or "db")
                run_root = self.workspace / "evidence" / "runs" / run_id
                run_root.mkdir(parents=True)
                Path(str(run_root / "asset-ledger.sqlite3") + suffix).symlink_to(outside)

                with self.assertRaisesRegex(ValueError, "symbolic link"):
                    DiscordEvidenceCollector(_FixtureTransport()).collect(
                        workspace=self.workspace,
                        output_dir="evidence",
                        targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                        run_id=run_id,
                        download_assets=False,
                    )

        self.assertEqual(outside.read_bytes(), b"unchanged")

    def test_legacy_asset_ledger_migration_is_validated_and_reentrant(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "attachments": [
                        {
                            "id": "asset",
                            "filename": "asset.bin",
                            "url": "https://cdn.example/legacy-ledger",
                        }
                    ],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        result = DiscordEvidenceCollector(transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="legacy-ledger-reentrant",
            download_assets=False,
        )
        transport.assert_exhausted(self)

        record_path = next((result.run_root / "asset-records").glob("*.json"))
        record = json.loads(record_path.read_text())
        original = record_path.read_bytes()
        checkpoint_path = result.run_root / "checkpoint.json"
        checkpoint = json.loads(checkpoint_path.read_text())
        checkpoint["assets"] = {
            record["logical_key"]: {
                "record_name": record_path.name,
                "committed_sha256": hashlib.sha256(original).hexdigest(),
                "pending_sha256": None,
            }
        }
        checkpoint.pop("asset_ledger", None)
        checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
        ledger_path = result.run_root / "asset-ledger.sqlite3"
        for suffix in ("-wal", "-shm", ""):
            path = Path(str(ledger_path) + suffix)
            if path.exists():
                path.unlink()

        record_path.write_bytes(original + b" ")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            DiscordEvidenceCollector(_FixtureTransport()).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="legacy-ledger-reentrant",
                download_assets=False,
            )
        failed_checkpoint = json.loads(checkpoint_path.read_text())
        self.assertEqual(failed_checkpoint["assets"], checkpoint["assets"])

        record_path.write_bytes(original)
        resumed = DiscordEvidenceCollector(_FixtureTransport()).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="legacy-ledger-reentrant",
            download_assets=False,
        )
        self.assertEqual(resumed.manifest["status"], "partial")
        self.assertEqual(resumed.manifest["media"]["status"], "not_requested")
        self.assertEqual(
            resumed.manifest["media"]["terminal_reason"],
            "asset_download_disabled",
        )
        migrated_checkpoint = json.loads(checkpoint_path.read_text())
        self.assertEqual(migrated_checkpoint["assets"], {})
        self.assertEqual(
            migrated_checkpoint["asset_ledger"], {"backend": "sqlite", "version": 1}
        )
        with closing(sqlite3.connect(ledger_path)) as connection:
            row = connection.execute(
                "SELECT committed_sha256, pending_sha256 FROM asset_records "
                "WHERE logical_key = ?",
                (record["logical_key"],),
            ).fetchone()
        self.assertEqual(row, (hashlib.sha256(original).hexdigest(), None))

    def test_sqlite_ledger_recovers_atomic_record_windows_and_rejects_tamper(self) -> None:
        def create_run(run_id: str) -> tuple[Path, Path, dict[str, object], bytes]:
            transport = _FixtureTransport()
            _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
            transport.add_json(
                "/channels/300/messages",
                [
                    {
                        "id": "30",
                        "attachments": [
                            {
                                "id": "asset",
                                "filename": "asset.bin",
                                "url": f"https://cdn.example/{run_id}",
                            }
                        ],
                    }
                ],
                {"limit": 100},
            )
            transport.add_json(
                "/channels/300/messages", [], {"limit": 100, "before": "30"}
            )
            transport.add_json(
                "/channels/300/messages/pins",
                {"items": [], "has_more": False},
                {"limit": 50},
            )
            result = DiscordEvidenceCollector(transport).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id=run_id,
                download_assets=False,
            )
            transport.assert_exhausted(self)
            record_path = next((result.run_root / "asset-records").glob("*.json"))
            record = json.loads(record_path.read_text())
            return (
                result.run_root / "asset-ledger.sqlite3",
                record_path,
                record,
                record_path.read_bytes(),
            )

        ledger_path, record_path, record, original = create_run("pending-promote")
        changed = dict(record)
        changed["observed_urls"] = [*record["observed_urls"], "https://cdn.example/seen"]
        changed_bytes = discord_collector_module._canonical_json_bytes(changed)
        changed_digest = hashlib.sha256(changed_bytes).hexdigest()
        record_path.write_bytes(changed_bytes)
        with closing(sqlite3.connect(ledger_path)) as connection:
            connection.execute(
                "UPDATE asset_records SET pending_sha256 = ? WHERE logical_key = ?",
                (changed_digest, record["logical_key"]),
            )
            connection.commit()
        DiscordEvidenceCollector(_FixtureTransport()).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="pending-promote",
            download_assets=False,
        )
        with closing(sqlite3.connect(ledger_path)) as connection:
            promoted = connection.execute(
                "SELECT committed_sha256, pending_sha256 FROM asset_records"
            ).fetchone()
        self.assertEqual(promoted, (changed_digest, None))

        ledger_path, record_path, record, original = create_run("pending-rollback")
        original_digest = hashlib.sha256(original).hexdigest()
        with closing(sqlite3.connect(ledger_path)) as connection:
            connection.execute(
                "UPDATE asset_records SET pending_sha256 = ? WHERE logical_key = ?",
                ("a" * 64, record["logical_key"]),
            )
            connection.commit()
        DiscordEvidenceCollector(_FixtureTransport()).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="pending-rollback",
            download_assets=False,
        )
        with closing(sqlite3.connect(ledger_path)) as connection:
            rolled_back = connection.execute(
                "SELECT committed_sha256, pending_sha256 FROM asset_records"
            ).fetchone()
        self.assertEqual(rolled_back, (original_digest, None))

        ledger_path, record_path, _, original = create_run("ledger-tamper")
        record_path.write_bytes(original + b" ")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            DiscordEvidenceCollector(_FixtureTransport()).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="ledger-tamper",
                download_assets=False,
            )
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(ledger_path) + suffix)
            self.assertTrue(not sidecar.exists() or sidecar.stat().st_size == 0)

    def test_interrupted_record_commit_reconciles_before_writing_index(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "attachments": [
                        {
                            "id": "asset",
                            "filename": "asset.bin",
                            "url": "https://cdn.example/interrupted-record",
                        }
                    ],
                }
            ],
            {"limit": 100},
        )
        original_write = discord_collector_module._atomic_write_bytes
        interrupted = False

        def interrupt_record(path: Path, content: bytes) -> None:
            nonlocal interrupted
            if path.parent.name == "asset-records" and not interrupted:
                interrupted = True
                raise RuntimeError("simulated record interruption")
            original_write(path, content)

        with patch.object(
            discord_collector_module,
            "_atomic_write_bytes",
            side_effect=interrupt_record,
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated record interruption"):
                DiscordEvidenceCollector(transport).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                    run_id="interrupted-record",
                    download_assets=False,
                )

        run_root = self.workspace / "evidence/runs/interrupted-record"
        self.assertEqual((run_root / "asset-index.jsonl").read_bytes(), b"")
        with closing(sqlite3.connect(run_root / "asset-ledger.sqlite3")) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM asset_records").fetchone()[0],
                0,
            )

        resumed = _FixtureTransport()
        resumed.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        resumed.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        result = DiscordEvidenceCollector(resumed).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="interrupted-record",
            download_assets=False,
        )
        self.assertEqual(result.manifest["media"]["records"], 1)
        resumed.assert_exhausted(self)

    def test_offline_collection_preserves_graph_threads_pages_and_assets(self) -> None:
        transport = _FixtureTransport()
        channels = [
            {"id": "100", "type": 0, "name": "text", "unknown": {"keep": 1}},
            {"id": "200", "type": 15, "name": "forum", "future": "keep"},
            {"id": "300", "type": 2, "name": "voice"},
        ]
        active_threads = [
            {"id": "400", "type": 11, "parent_id": "100", "name": "active"},
            {"id": "800", "type": 11, "parent_id": "100", "name": "both"},
        ]
        _add_inventory(transport, channels, active_threads=active_threads)
        transport.add_json(
            "/channels/900",
            {
                "id": "900",
                "type": 11,
                "guild_id": "1",
                "parent_id": "100",
                "unknown": "live",
            },
        )
        archived_metadata = {
            "thread_metadata": {"archive_timestamp": "2026-01-01T00:00:00+00:00"}
        }
        _add_archive_triplet(
            transport,
            "100",
            public=[
                {"id": "400", "type": 11, "parent_id": "100", **archived_metadata},
                {"id": "500", "type": 11, "parent_id": "100", **archived_metadata},
            ],
            private=DiscordAPIError("forbidden", status_code=403),
            joined=[{"id": "600", "type": 12, "parent_id": "100"}],
        )
        _add_archive_triplet(
            transport,
            "200",
            public=[{"id": "700", "type": 11, "parent_id": "200", **archived_metadata}],
            private={
                "threads": [
                    {"id": "750", "type": 12, "parent_id": "200", **archived_metadata}
                ],
                "members": [],
                "has_more": False,
            },
            joined=[{"id": "760", "type": 12, "parent_id": "200"}],
        )

        attachment_url = "https://cdn.example/one"
        duplicate_bytes_url = "https://cdn.example/two"
        image_url = "https://cdn.example/embed-image"
        thumbnail_url = "https://cdn.example/embed-thumbnail"
        video_url = "https://cdn.example/embed-video"
        failed_url = "https://cdn.example/failure"
        parent_message = {
            "id": "1000",
            "content": "raw unknown survives",
            "unknown_message_field": {"keep": True},
            "thread": {"id": "800", "type": 11, "parent_id": "100", "extra": 1},
            "attachments": [
                {
                    "id": "a1",
                    "filename": "../../escape.png",
                    "url": attachment_url,
                    "content_type": "image/png",
                    "size": 6,
                    "unknown_attachment": "keep",
                },
                {
                    "id": "a2",
                    "filename": "/absolute.png",
                    "url": duplicate_bytes_url,
                    "content_type": "image/png",
                    "size": 6,
                },
            ],
            "embeds": [
                {
                    "image": {"url": image_url},
                    "thumbnail": {"url": thumbnail_url},
                    "video": {"url": video_url},
                },
                {"image": {"url": failed_url}},
            ],
        }
        transport.add_json(
            "/channels/100/messages",
            [parent_message],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/100/messages",
            [],
            {"limit": 100, "before": "1000"},
        )
        transport.add_json(
            "/channels/100/messages/pins",
            {
                "items": [
                    {
                        "pinned_at": "2026-07-19T00:00:00+00:00",
                        "message": {
                            "id": "1000",
                            "attachments": [parent_message["attachments"][0]],
                        },
                    }
                ],
                "has_more": False,
                "unknown_pin_field": "keep",
            },
            {"limit": 50},
        )
        _add_empty_messages_and_pins(transport, "300")
        for thread_id in ("400", "500", "600", "700", "750", "760", "800", "900"):
            _add_empty_messages_and_pins(transport, thread_id)

        same_bytes = b"abcdef"
        for url, content_type in (
            (attachment_url, "image/png"),
            (duplicate_bytes_url, "image/png"),
            (image_url, "image/jpeg"),
            (thumbnail_url, "image/webp"),
            (video_url, "video/mp4"),
        ):
            body = same_bytes if url in (attachment_url, duplicate_bytes_url) else url.encode()
            transport.add_media(
                url,
                _ByteStream(
                    [body[:3], body[3:]],
                    content_type=content_type,
                    content_length=len(body),
                ),
            )
        transport.add_media(
            failed_url,
            DiscordAPIError("download failed", status_code=404),
        )

        collector = DiscordEvidenceCollector(transport, byte_transport=transport)
        result = collector.collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(
                _target("100", name="text"),
                _target("200", kind="GUILD_FORUM (15)", name="forum"),
                _target("300", kind="GUILD_VOICE (2)", name="voice"),
                _target(
                    "900",
                    kind="thread (explicitly identified in v2 plan; numeric type unavailable)",
                    name="explicit-thread",
                    parent_id="100",
                ),
            ),
            run_id="run-e2e",
            download_assets=True,
        )

        self.assertEqual(result.manifest["status"], "partial")
        self.assertEqual(result.manifest["media"]["status"], "partial")
        self.assertEqual(
            result.manifest["not_api_exposed"],
            ["discord_go_live", "personal_favorites"],
        )
        self.assertEqual(
            json.loads((result.run_root / "inventory/channels.json").read_text()),
            channels,
        )
        request = json.loads((result.run_root / "request.json").read_text())
        self.assertEqual(request["target_snapshot"]["audit_notes"], {"unknown": "retained"})
        self.assertEqual(
            request["target_snapshot"]["targets"][0]["future_target_field"],
            {"retained": True},
        )

        target_inventory = json.loads(
            (result.run_root / "inventory/targets.json").read_text()
        )
        threads = {item["id"]: item for item in target_inventory["threads"]}
        self.assertEqual(
            set(threads),
            {"400", "500", "600", "700", "750", "760", "800", "900"},
        )
        self.assertEqual(set(threads["400"]["sources"]), {"active", "public_archived"})
        self.assertEqual(set(threads["800"]["sources"]), {"active", "message_embedded"})
        self.assertEqual(threads["750"]["sources"], ["private_archived"])
        self.assertEqual(threads["760"]["sources"], ["joined_private_archived"])
        self.assertEqual(threads["900"]["sources"], ["explicit_target"])
        self.assertFalse(
            any(path.startswith("/channels/200/messages") for path, _ in transport.json_calls)
        )
        private_stream = result.manifest["streams"]["threads_100_private_archived"]
        self.assertEqual(private_stream["status"], "blocked")
        self.assertIn("messages_700", result.manifest["streams"])
        self.assertEqual(result.manifest["streams"]["messages_100"]["pages"], 2)
        self.assertEqual(result.manifest["streams"]["pins_100"]["pages"], 1)
        self.assertEqual(
            result.manifest["streams"]["pins_100"]["first_timestamp"],
            "2026-07-19T00:00:00+00:00",
        )

        page_payloads = [
            json.loads(path.read_text())["payload"]
            for path in (result.run_root / "pages").rglob("*.json")
        ]
        stored_parent = next(
            payload[0]
            for payload in page_payloads
            if isinstance(payload, list)
            and payload
            and isinstance(payload[0], dict)
            and payload[0].get("id") == "1000"
        )
        for field, value in parent_message.items():
            self.assertEqual(stored_parent[field], value)
        pins_payload = next(
            payload for payload in page_payloads
            if isinstance(payload, dict) and payload.get("unknown_pin_field") == "keep"
        )
        self.assertEqual(pins_payload["unknown_pin_field"], "keep")

        records = [
            json.loads(line)
            for line in (result.run_root / "asset-index.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(records), 6)
        records_by_key = {record["logical_key"]: record for record in records}
        self.assertEqual(
            set(records_by_key),
            {
                "1000:attachment:a1",
                "1000:attachment:a2",
                "1000:embed:0:image",
                "1000:embed:0:thumbnail",
                "1000:embed:0:video",
                "1000:embed:1:image",
            },
        )
        self.assertEqual(transport.media_calls.count(attachment_url), 1)
        self.assertEqual(
            records_by_key["1000:attachment:a1"]["sha256"],
            records_by_key["1000:attachment:a2"]["sha256"],
        )
        self.assertEqual(records_by_key["1000:attachment:a1"]["actual_bytes"], 6)
        self.assertEqual(records_by_key["1000:attachment:a1"]["http_content_type"], "image/png")
        self.assertEqual(records_by_key["1000:embed:1:image"]["status"], "failed")
        blobs = list((result.run_root / "assets/sha256").glob("*/*"))
        self.assertEqual(len(blobs), 4)
        self.assertFalse((self.workspace / "escape.png").exists())
        self.assertFalse(Path("/absolute.png").exists())
        self.assertEqual(len(list((result.run_root / "asset-records").glob("*.json"))), 6)
        transport.assert_exhausted(self)

    def test_declared_media_mismatch_is_distinct_from_truncated_download(
        self,
    ) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        message = {
            "id": "30",
            "attachments": [
                {
                    "id": "mime",
                    "filename": "image.png",
                    "url": "https://cdn.example/mime",
                    "content_type": "image/png",
                },
                {
                    "id": "length",
                    "filename": "audio.ogg",
                    "url": "https://cdn.example/length",
                    "content_type": "audio/ogg",
                },
            ],
        }
        transport.add_json("/channels/300/messages", [message], {"limit": 100})
        transport.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(
            "https://cdn.example/mime",
            _ByteStream([b"html"], content_type="text/html", content_length=4),
        )
        transport.add_media(
            "https://cdn.example/length",
            _ByteStream([b"1234"], content_type="audio/ogg", content_length=999),
        )

        result = DiscordEvidenceCollector(transport, byte_transport=transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="bad-metadata",
            download_assets=True,
        )

        records = [
            json.loads(line)
            for line in (result.run_root / "asset-index.jsonl").read_text().splitlines()
        ]
        records_by_reason = {
            record["terminal_reason"]: record for record in records
        }
        self.assertEqual(
            records_by_reason["declared_media_type_mismatch"]["status"],
            "failed",
        )
        self.assertEqual(
            records_by_reason["content_length_mismatch"]["status"],
            "failed",
        )
        self.assertEqual(
            {record["terminal_reason"] for record in records},
            {"declared_media_type_mismatch", "content_length_mismatch"},
        )
        self.assertEqual(result.manifest["status"], "partial")
        self.assertEqual(result.manifest["media"]["captured_with_warning"], 0)
        self.assertEqual(result.manifest["media"]["failed"], 2)
        transport.assert_exhausted(self)

    def test_size_limit_and_unexpected_item_error_do_not_stop_later_assets(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        message = {
            "id": "30",
            "attachments": [
                {
                    "id": "large",
                    "filename": "large.bin",
                    "url": "https://cdn.example/large",
                },
                {
                    "id": "broken",
                    "filename": "broken.bin",
                    "url": "https://cdn.example/broken",
                },
                {
                    "id": "later",
                    "filename": "later.bin",
                    "url": "https://cdn.example/later",
                },
            ],
        }
        transport.add_json("/channels/300/messages", [message], {"limit": 100})
        transport.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(
            "https://cdn.example/large",
            _ByteStream([b"12", b"345"], content_length=5),
        )
        transport.add_media("https://cdn.example/broken", RuntimeError("fixture failure"))
        transport.add_media(
            "https://cdn.example/later",
            _ByteStream([b"ok"], content_length=2),
        )

        result = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
            max_asset_bytes=3,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="item-failures",
            download_assets=True,
        )

        records = {
            record["logical_key"]: record
            for record in (
                json.loads(line)
                for line in (result.run_root / "asset-index.jsonl").read_text().splitlines()
            )
        }
        self.assertEqual(records["30:attachment:large"]["actual_bytes"], 5)
        self.assertEqual(
            records["30:attachment:large"]["terminal_reason"],
            "size_limit_exceeded",
        )
        self.assertEqual(records["30:attachment:broken"]["status"], "failed")
        self.assertEqual(records["30:attachment:later"]["status"], "complete")
        self.assertEqual(result.manifest["media"]["status"], "partial")
        transport.assert_exhausted(self)

    def test_same_url_asset_retries_transport_and_length_failures(self) -> None:
        cases = (
            ("transport", None),
            (
                "content-length",
                _ByteStream(
                    [b"data"],
                    content_type="image/png",
                    content_length=5,
                ),
            ),
        )
        snapshot = _snapshot(_target("300", kind="GUILD_VOICE (2)"))
        url = "https://cdn.example/same-url"
        for suffix, first_outcome in cases:
            with self.subTest(failure=suffix):
                run_id = f"same-url-{suffix}"
                first = _FixtureTransport()
                self._configure_single_attachment_run(first, url=url)
                if first_outcome is not None:
                    first.add_media(url, first_outcome)
                first_result = DiscordEvidenceCollector(
                    first,
                    byte_transport=first if first_outcome is not None else None,
                ).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=snapshot,
                    run_id=run_id,
                    download_assets=True,
                )
                first.assert_exhausted(self)
                self.assertEqual(first_result.manifest["media"]["failed"], 1)

                resumed = _FixtureTransport()
                resumed.add_media(
                    url,
                    _ByteStream(
                        [b"data"],
                        content_type="image/png",
                        content_length=4,
                    ),
                )
                resumed_result = DiscordEvidenceCollector(
                    resumed,
                    byte_transport=resumed,
                ).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=snapshot,
                    run_id=run_id,
                    download_assets=True,
                )
                resumed.assert_exhausted(self)
                self.assertEqual(resumed_result.manifest["media"]["complete"], 1)
                record = json.loads(
                    next(
                        (resumed_result.run_root / "asset-records").glob("*.json")
                    ).read_text()
                )
                expected_attempts = 1 if suffix == "transport" else 2
                self.assertEqual(len(record["attempt_history"]), expected_attempts)

    def test_permanent_asset_http_error_waits_for_fresh_url(self) -> None:
        url = "https://cdn.example/missing"
        first = _FixtureTransport()
        self._configure_single_attachment_run(first, url=url)
        first.add_media(url, DiscordAPIError("missing", status_code=404))
        snapshot = _snapshot(_target("300", kind="GUILD_VOICE (2)"))
        first_result = DiscordEvidenceCollector(first, byte_transport=first).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=snapshot,
            run_id="asset-http-404",
            download_assets=True,
        )
        first.assert_exhausted(self)
        self.assertEqual(first_result.manifest["media"]["failed"], 1)

        resumed = _FixtureTransport()
        resumed_result = DiscordEvidenceCollector(
            resumed,
            byte_transport=resumed,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=snapshot,
            run_id="asset-http-404",
            download_assets=True,
        )
        resumed.assert_exhausted(self)
        self.assertEqual(resumed_result.manifest["media"]["failed"], 1)
        self.assertEqual(resumed_result.manifest["media"]["status"], "partial")
        self.assertEqual(resumed_result.manifest["status"], "partial")
        record = json.loads(
            next((resumed_result.run_root / "asset-records").glob("*.json")).read_text()
        )
        self.assertEqual(record["terminal_reason"], "download_http_404")
        self.assertEqual(len(record["attempt_history"]), 1)

    def test_fresh_url_self_heals_warning_and_reference_only_assets(self) -> None:
        cases = (
            (
                "warning",
                {
                    "id": "400",
                    "filename": "asset.png",
                    "url": "https://cdn.example/warning-old",
                    "size": 5,
                    "content_type": "image/png",
                },
                _ByteStream(
                    [b"data"],
                    content_type="image/png",
                    content_length=4,
                ),
                "https://cdn.example/warning-new",
                _ByteStream(
                    [b"fixed"],
                    content_type="image/png",
                    content_length=5,
                ),
                "attachments",
            ),
            (
                "reference",
                {"url": "https://cdn.example/video-old"},
                _ByteStream(
                    [b"<html>"],
                    content_type="text/html",
                    content_length=6,
                ),
                "https://cdn.example/video-new",
                _ByteStream(
                    [b"video"],
                    content_type="video/mp4",
                    content_length=5,
                ),
                "video",
            ),
        )
        snapshot = _snapshot(_target("300", kind="GUILD_VOICE (2)"))
        for suffix, media, first_stream, fresh_url, fresh_stream, field in cases:
            with self.subTest(status=suffix):
                first = _FixtureTransport()
                _add_inventory(
                    first,
                    [{"id": "300", "type": 2, "name": "voice"}],
                )
                message = (
                    {"id": "30", "attachments": [media]}
                    if field == "attachments"
                    else {"id": "30", "embeds": [{field: media}]}
                )
                first.add_json(
                    "/channels/300/messages",
                    [message],
                    {"limit": 100},
                )
                first.add_json(
                    "/channels/300/messages",
                    [],
                    {"limit": 100, "before": "30"},
                )
                first.add_json(
                    "/channels/300/messages/pins",
                    KeyboardInterrupt(),
                    {"limit": 50},
                )
                first.add_media(str(media["url"]), first_stream)
                run_id = f"fresh-url-{suffix}"
                with self.assertRaises(KeyboardInterrupt):
                    DiscordEvidenceCollector(first, byte_transport=first).collect(
                        workspace=self.workspace,
                        output_dir="evidence",
                        targets=snapshot,
                        run_id=run_id,
                        download_assets=True,
                    )
                first.assert_exhausted(self)

                refreshed_media = {**media, "url": fresh_url}
                resumed = _FixtureTransport()
                refreshed_message = (
                    {"id": "30", "attachments": [refreshed_media]}
                    if field == "attachments"
                    else {"id": "30", "embeds": [{field: refreshed_media}]}
                )
                resumed.add_json(
                    "/channels/300/messages/pins",
                    {
                        "items": [
                            {
                                "pinned_at": "2026-07-20T00:00:00+00:00",
                                "message": refreshed_message,
                            }
                        ],
                        "has_more": False,
                    },
                    {"limit": 50},
                )
                resumed.add_media(fresh_url, fresh_stream)
                result = DiscordEvidenceCollector(
                    resumed,
                    byte_transport=resumed,
                ).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=snapshot,
                    run_id=run_id,
                    download_assets=True,
                )
                resumed.assert_exhausted(self)
                self.assertEqual(result.manifest["media"]["complete"], 1)
                record = json.loads(
                    next((result.run_root / "asset-records").glob("*.json")).read_text()
                )
                self.assertEqual(record["url"], fresh_url)
                self.assertEqual(len(record["attempt_history"]), 2)
                self.assertEqual(
                    {source["stream"] for source in record["sources"]},
                    {"messages_300", "pins_300"},
                )
                self.assertEqual(
                    record["observed_urls"],
                    [str(media["url"]), fresh_url],
                )

    def test_media_mime_status_taxonomy_is_fail_closed(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        message = {
            "id": "30",
            "attachments": [
                {
                    "id": "400",
                    "filename": "html.png",
                    "url": "https://cdn.example/html",
                    "size": 6,
                    "content_type": "image/png",
                },
                {
                    "id": "401",
                    "filename": "same.png",
                    "url": "https://cdn.example/same-family",
                    "size": 4,
                    "content_type": "image/png",
                },
            ],
            "embeds": [
                {"video": {"url": "https://cdn.example/webpage-video"}}
            ],
        }
        transport.add_json(
            "/channels/300/messages",
            [message],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(
            "https://cdn.example/html",
            _ByteStream([b"<html>"], content_type="text/html", content_length=6),
        )
        transport.add_media(
            "https://cdn.example/same-family",
            _ByteStream([b"jpeg"], content_type="image/jpeg", content_length=4),
        )
        transport.add_media(
            "https://cdn.example/webpage-video",
            _ByteStream([b"<html>"], content_type="text/html", content_length=6),
        )
        result = DiscordEvidenceCollector(transport, byte_transport=transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="mime-taxonomy",
            download_assets=True,
        )
        transport.assert_exhausted(self)
        records = {
            record["logical_key"]: record
            for record in (
                json.loads(line)
                for line in (result.run_root / "asset-index.jsonl").read_text().splitlines()
            )
        }
        self.assertEqual(records["30:attachment:400"]["status"], "failed")
        self.assertEqual(
            records["30:attachment:401"]["status"],
            "captured_with_warning",
        )
        self.assertEqual(records["30:embed:0:video"]["status"], "reference_only")
        self.assertEqual(
            records["30:embed:0:video"]["terminal_reason"],
            "media_reference_not_binary",
        )
        self.assertEqual(result.manifest["media"]["binary_captured"], 1)

    def test_unproxied_youtube_embed_player_policy_rejection_is_reference_only(
        self,
    ) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        player_url = (
            "https://www.youtube.com/embed/AbC_123-xyz?si=opaque-query&autoplay=1"
        )
        transport.add_json(
            "/channels/300/messages",
            [{"id": "30", "embeds": [{"video": {"url": player_url}}]}],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(
            player_url,
            discord_collector_module.DiscordMediaSecurityError(
                "external player host is outside the media allowlist"
            ),
        )

        result = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="youtube-player-reference",
            download_assets=True,
        )

        transport.assert_exhausted(self)
        record = json.loads(
            next((result.run_root / "asset-records").glob("*.json")).read_text()
        )
        self.assertEqual(record["status"], "reference_only")
        self.assertEqual(
            record["terminal_reason"],
            "youtube_embed_player_reference",
        )
        self.assertEqual(record["url"], player_url)
        self.assertEqual(record["candidate_urls"], [player_url])
        self.assertEqual(record["observed_urls"], [player_url])
        self.assertEqual(record["observations"][0]["url"], player_url)
        self.assertEqual(record["observations"][0]["proxy_url"], None)
        self.assertEqual(record["sources"][0]["stream"], "messages_300")
        self.assertEqual(
            record["reference_provenance"],
            {
                "classification": "youtube_embed_player",
                "classification_rule": (
                    "youtube_embed_player_url_rejected_by_media_policy_v1"
                ),
                "source_url": player_url,
                "url_identity": {
                    "scheme": "https",
                    "host": "www.youtube.com",
                    "path": "/embed/AbC_123-xyz",
                },
                "failed_attempt_number": 1,
                "failed_attempt_status": "failed",
                "failed_attempt_terminal_reason": "unsafe_media_url",
                "proxy_candidate_present": False,
                "binary_captured": False,
            },
        )
        self.assertEqual(
            record["attempt_history"],
            [
                {
                    "url": player_url,
                    "status": "failed",
                    "terminal_reason": "unsafe_media_url",
                    "security_rejection": {
                        "version": 1,
                        "reason_code": "media_security_policy_rejected",
                        "legacy_eligible": False,
                    },
                    "http_content_type": None,
                    "http_content_length": None,
                    "actual_bytes": 0,
                    "sha256": None,
                    "blob_path": None,
                }
            ],
        )
        self.assertEqual(record["actual_bytes"], 0)
        self.assertIsNone(record["sha256"])
        self.assertIsNone(record["blob_path"])
        self.assertEqual(list((result.run_root / "assets/sha256").glob("*/*")), [])
        self.assertEqual(result.manifest["status"], "complete_with_warnings")
        self.assertEqual(result.manifest["media"]["reference_only"], 1)
        self.assertEqual(result.manifest["media"]["binary_captured"], 0)
        self.assertEqual(result.manifest["media"]["failed"], 0)

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
            discord_collector_module._youtube_embed_player_attempt_provenance(
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
                    discord_collector_module._youtube_embed_player_attempt_provenance(
                        hostile,
                        source_url=player_url,
                        failed_attempt_number=1,
                    )
                )

    def test_youtube_reference_classification_excludes_nonmatching_media_failures(
        self,
    ) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        urls = {
            "typed": "https://www.youtube.com/embed/TypedBinary",
            "proxied": "https://www.youtube.com/embed/HasProxy",
            "proxy": "https://media.discordapp.net/external/proxy-video",
            "short": "https://youtu.be/embed/ShortHost",
            "watch": "https://www.youtube.com/watch?v=WatchPage",
            "direct": "https://www.youtube.com/media/direct.mp4",
            "other_error": "https://www.youtube-nocookie.com/embed/OtherError",
            "thumbnail_400": "https://www.youtube.com/embed/Thumbnail400",
            "thumbnail_404": "https://www.youtube.com/embed/Thumbnail404",
            "attachment": "https://www.youtube.com/embed/Attachment",
        }
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "attachments": [
                        {
                            "id": "400",
                            "filename": "direct.mp4",
                            "url": urls["attachment"],
                            "content_type": "video/mp4",
                            "size": 4,
                        }
                    ],
                    "embeds": [
                        {
                            "video": {
                                "url": urls["typed"],
                                "content_type": "video/mp4",
                            }
                        },
                        {
                            "video": {
                                "url": urls["proxied"],
                                "proxy_url": urls["proxy"],
                            }
                        },
                        {"video": {"url": urls["short"]}},
                        {"video": {"url": urls["watch"]}},
                        {"video": {"url": urls["direct"]}},
                        {"video": {"url": urls["other_error"]}},
                        {"thumbnail": {"url": urls["thumbnail_400"]}},
                        {"thumbnail": {"url": urls["thumbnail_404"]}},
                    ],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        for name in ("typed", "proxied", "short", "watch", "direct", "attachment"):
            transport.add_media(
                urls[name],
                discord_collector_module.DiscordMediaSecurityError(
                    f"blocked {name}"
                ),
            )
        transport.add_media(
            urls["proxy"],
            DiscordAPIError("proxy expired", status_code=404),
        )
        transport.add_media(urls["other_error"], RuntimeError("network error"))
        transport.add_media(
            urls["thumbnail_400"],
            DiscordAPIError("bad thumbnail", status_code=400),
        )
        transport.add_media(
            urls["thumbnail_404"],
            DiscordAPIError("missing thumbnail", status_code=404),
        )

        result = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="youtube-reference-exclusions",
            download_assets=True,
        )

        transport.assert_exhausted(self)
        records = {
            record["logical_key"]: record
            for record in (
                json.loads(line)
                for line in (result.run_root / "asset-index.jsonl").read_text().splitlines()
            )
        }
        self.assertEqual(set(records), {
            "30:attachment:400",
            *(f"30:embed:{index}:video" for index in range(6)),
            "30:embed:6:thumbnail",
            "30:embed:7:thumbnail",
        })
        self.assertTrue(all(record["status"] == "failed" for record in records.values()))
        self.assertTrue(
            all("reference_provenance" not in record for record in records.values())
        )
        self.assertEqual(
            records["30:embed:1:video"]["candidate_urls"],
            [urls["proxied"], urls["proxy"]],
        )
        self.assertEqual(
            [
                attempt["terminal_reason"]
                for attempt in records["30:embed:1:video"]["attempt_history"]
            ],
            ["unsafe_media_url", "download_http_404"],
        )
        self.assertEqual(
            records["30:embed:5:video"]["terminal_reason"],
            "download_failed_transient",
        )
        self.assertEqual(
            records["30:embed:6:thumbnail"]["terminal_reason"],
            "download_http_400",
        )
        self.assertEqual(
            records["30:embed:7:thumbnail"]["terminal_reason"],
            "download_http_404",
        )
        self.assertEqual(result.manifest["media"]["reference_only"], 0)
        self.assertEqual(result.manifest["media"]["failed"], len(records))

    def test_resume_reconciles_legacy_youtube_player_failure_idempotently(
        self,
    ) -> None:
        player_url = (
            "https://www.youtube-nocookie.com/embed/Opaque_1?start=15&rel=0"
        )
        initial = _FixtureTransport()
        _add_inventory(initial, [{"id": "300", "type": 2, "name": "voice"}])
        initial.add_json(
            "/channels/300/messages",
            [{"id": "30", "embeds": [{"video": {"url": player_url}}]}],
            {"limit": 100},
        )
        initial.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        initial.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        initial.add_media(
            player_url,
            discord_collector_module.DiscordMediaSecurityError(
                "legacy policy rejection"
            ),
        )
        first = DiscordEvidenceCollector(initial, byte_transport=initial).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="resume-youtube-player-reference",
            download_assets=True,
        )
        initial.assert_exhausted(self)
        request_path = first.run_root / "request.json"
        request_before = request_path.read_bytes()
        record_path = next((first.run_root / "asset-records").glob("*.json"))
        legacy = json.loads(record_path.read_text())
        legacy_attempt_history = deepcopy(legacy["attempt_history"])
        legacy.pop("reference_provenance", None)
        legacy.update(
            {
                "status": "failed",
                "terminal_reason": "unsafe_media_url",
                "http_content_type": None,
                "http_content_length": None,
                "actual_bytes": 0,
                "sha256": None,
                "blob_path": None,
            }
        )
        legacy_bytes = discord_collector_module._canonical_json_bytes(legacy)
        record_path.write_bytes(legacy_bytes)
        ledger_path = first.run_root / "asset-ledger.sqlite3"
        with closing(sqlite3.connect(ledger_path)) as connection:
            connection.execute(
                "UPDATE asset_records SET committed_sha256 = ?, pending_sha256 = NULL "
                "WHERE logical_key = ?",
                (hashlib.sha256(legacy_bytes).hexdigest(), legacy["logical_key"]),
            )
            connection.commit()

        resumed_transport = _FixtureTransport()
        resumed = DiscordEvidenceCollector(
            resumed_transport,
            byte_transport=resumed_transport,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="resume-youtube-player-reference",
            download_assets=True,
        )
        resumed_transport.assert_exhausted(self)
        reconciled = json.loads(record_path.read_text())
        self.assertEqual(reconciled["status"], "reference_only")
        self.assertEqual(
            reconciled["terminal_reason"],
            "youtube_embed_player_reference",
        )
        self.assertEqual(reconciled["attempt_history"], legacy_attempt_history)
        self.assertEqual(reconciled["sources"], legacy["sources"])
        self.assertEqual(reconciled["observations"], legacy["observations"])
        self.assertEqual(reconciled["observed_urls"], legacy["observed_urls"])
        self.assertEqual(resumed_transport.media_calls, [])
        self.assertEqual(request_path.read_bytes(), request_before)
        self.assertEqual(resumed.manifest["status"], "complete_with_warnings")
        first_reconciled_record = record_path.read_bytes()
        first_reconciled_index = (first.run_root / "asset-index.jsonl").read_bytes()
        with closing(sqlite3.connect(ledger_path)) as connection:
            first_metadata = connection.execute(
                "SELECT key, value FROM asset_metadata ORDER BY key"
            ).fetchall()

        second_transport = _FixtureTransport()
        second = DiscordEvidenceCollector(
            second_transport,
            byte_transport=second_transport,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="resume-youtube-player-reference",
            download_assets=True,
        )
        second_transport.assert_exhausted(self)
        with closing(sqlite3.connect(ledger_path)) as connection:
            second_metadata = connection.execute(
                "SELECT key, value FROM asset_metadata ORDER BY key"
            ).fetchall()
        self.assertEqual(second.manifest, resumed.manifest)
        self.assertEqual(record_path.read_bytes(), first_reconciled_record)
        self.assertEqual(
            (first.run_root / "asset-index.jsonl").read_bytes(),
            first_reconciled_index,
        )
        self.assertEqual(second_metadata, first_metadata)
        self.assertEqual(request_path.read_bytes(), request_before)

    def test_later_pin_proxy_replaces_youtube_reference_without_stale_provenance(
        self,
    ) -> None:
        player_url = "https://www.youtube.com/embed/LaterProxy?si=raw-query"
        proxy_url = "https://media.discordapp.net/external/later-proxy.mp4"
        snapshot = _snapshot(_target("300", kind="GUILD_VOICE (2)"))
        initial = _FixtureTransport()
        _add_inventory(initial, [{"id": "300", "type": 2, "name": "voice"}])
        initial.add_json(
            "/channels/300/messages",
            [{"id": "30", "embeds": [{"video": {"url": player_url}}]}],
            {"limit": 100},
        )
        initial.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        initial.add_json(
            "/channels/300/messages/pins",
            KeyboardInterrupt(),
            {"limit": 50},
        )
        initial.add_media(
            player_url,
            discord_collector_module.DiscordMediaSecurityError("blocked player"),
        )
        with self.assertRaises(KeyboardInterrupt):
            DiscordEvidenceCollector(initial, byte_transport=initial).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=snapshot,
                run_id="youtube-reference-later-proxy",
                download_assets=True,
            )
        initial.assert_exhausted(self)
        run_root = self.workspace / "evidence/runs/youtube-reference-later-proxy"
        request_path = run_root / "request.json"
        request_before = request_path.read_bytes()
        record_path = next((run_root / "asset-records").glob("*.json"))
        reference = json.loads(record_path.read_text())
        self.assertEqual(reference["status"], "reference_only")
        self.assertIn("reference_provenance", reference)

        pin_observation = {
            "id": "30",
            "embeds": [
                {
                    "video": {
                        "url": player_url,
                        "proxy_url": proxy_url,
                    }
                }
            ],
        }
        resumed_transport = _FixtureTransport()
        resumed_transport.add_json(
            "/channels/300/messages/pins",
            {
                "items": [
                    {
                        "pinned_at": "2026-07-20T00:00:00+00:00",
                        "message": pin_observation,
                    }
                ],
                "has_more": False,
            },
            {"limit": 50},
        )
        resumed_transport.add_media(
            proxy_url,
            _ByteStream(
                [b"video"],
                content_type="video/mp4",
                content_length=5,
            ),
        )
        resumed = DiscordEvidenceCollector(
            resumed_transport,
            byte_transport=resumed_transport,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=snapshot,
            run_id="youtube-reference-later-proxy",
            download_assets=True,
        )
        resumed_transport.assert_exhausted(self)
        complete = json.loads(record_path.read_text())
        self.assertEqual(complete["status"], "complete")
        self.assertEqual(complete["terminal_reason"], "downloaded")
        self.assertNotIn("reference_provenance", complete)
        self.assertEqual(complete["url"], proxy_url)
        self.assertEqual(complete["candidate_urls"], [player_url, proxy_url])
        self.assertEqual(
            [(attempt["url"], attempt["status"]) for attempt in complete["attempt_history"]],
            [(player_url, "failed"), (proxy_url, "complete")],
        )
        self.assertEqual(
            {source["stream"] for source in complete["sources"]},
            {"messages_300", "pins_300"},
        )
        self.assertEqual(
            [observation["proxy_url"] for observation in complete["observations"]],
            [None, proxy_url],
        )
        self.assertEqual(resumed.manifest["status"], "complete")
        self.assertEqual(resumed.manifest["media"]["binary_captured"], 1)
        self.assertEqual(request_path.read_bytes(), request_before)
        clean_record_bytes = record_path.read_bytes()
        stale = deepcopy(complete)
        stale["reference_provenance"] = deepcopy(reference["reference_provenance"])
        stale_bytes = discord_collector_module._canonical_json_bytes(stale)
        record_path.write_bytes(stale_bytes)
        ledger_path = run_root / "asset-ledger.sqlite3"
        with closing(sqlite3.connect(ledger_path)) as connection:
            connection.execute(
                "UPDATE asset_records SET committed_sha256 = ?, pending_sha256 = NULL "
                "WHERE logical_key = ?",
                (hashlib.sha256(stale_bytes).hexdigest(), stale["logical_key"]),
            )
            connection.commit()

        repair_transport = _FixtureTransport()
        repaired = DiscordEvidenceCollector(
            repair_transport,
            byte_transport=repair_transport,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=snapshot,
            run_id="youtube-reference-later-proxy",
            download_assets=True,
        )
        repair_transport.assert_exhausted(self)
        self.assertEqual(repaired.manifest, resumed.manifest)
        self.assertEqual(record_path.read_bytes(), clean_record_bytes)
        self.assertNotIn("reference_provenance", json.loads(record_path.read_text()))
        self.assertEqual(request_path.read_bytes(), request_before)
        index_before_noop = (run_root / "asset-index.jsonl").read_bytes()

        noop_transport = _FixtureTransport()
        noop = DiscordEvidenceCollector(
            noop_transport,
            byte_transport=noop_transport,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=snapshot,
            run_id="youtube-reference-later-proxy",
            download_assets=True,
        )
        noop_transport.assert_exhausted(self)
        self.assertEqual(noop.manifest, repaired.manifest)
        self.assertEqual(record_path.read_bytes(), clean_record_bytes)
        self.assertEqual((run_root / "asset-index.jsonl").read_bytes(), index_before_noop)
        self.assertEqual(request_path.read_bytes(), request_before)

        tampered = deepcopy(complete)
        tampered["reference_provenance"] = deepcopy(reference["reference_provenance"])
        tampered["reference_provenance"]["failed_attempt_number"] = 99
        tampered_bytes = discord_collector_module._canonical_json_bytes(tampered)
        record_path.write_bytes(tampered_bytes)
        with closing(sqlite3.connect(ledger_path)) as connection:
            connection.execute(
                "UPDATE asset_records SET committed_sha256 = ?, pending_sha256 = NULL "
                "WHERE logical_key = ?",
                (hashlib.sha256(tampered_bytes).hexdigest(), tampered["logical_key"]),
            )
            connection.commit()
        with self.assertRaisesRegex(ValueError, "reference provenance"):
            DiscordEvidenceCollector(_FixtureTransport()).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=snapshot,
                run_id="youtube-reference-later-proxy",
                download_assets=True,
            )

    def test_resume_rejects_tampered_youtube_reference_provenance(self) -> None:
        player_url = "https://www.youtube.com/embed/TamperProof"
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [{"id": "30", "embeds": [{"video": {"url": player_url}}]}],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(
            player_url,
            discord_collector_module.DiscordMediaSecurityError("blocked"),
        )
        result = DiscordEvidenceCollector(transport, byte_transport=transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="youtube-reference-tamper",
            download_assets=True,
        )
        transport.assert_exhausted(self)
        record_path = next((result.run_root / "asset-records").glob("*.json"))
        record = json.loads(record_path.read_text())
        record.update(
            {
                "status": "reference_only",
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
                    "source_url": "https://attacker.invalid/embed/rebound",
                    "url_identity": {
                        "scheme": "https",
                        "host": "www.youtube.com",
                        "path": "/embed/TamperProof",
                    },
                    "failed_attempt_number": 1,
                    "failed_attempt_status": "failed",
                    "failed_attempt_terminal_reason": "unsafe_media_url",
                    "proxy_candidate_present": False,
                    "binary_captured": False,
                },
            }
        )
        record_bytes = discord_collector_module._canonical_json_bytes(record)
        record_path.write_bytes(record_bytes)
        with closing(
            sqlite3.connect(result.run_root / "asset-ledger.sqlite3")
        ) as connection:
            connection.execute(
                "UPDATE asset_records SET committed_sha256 = ?, pending_sha256 = NULL "
                "WHERE logical_key = ?",
                (hashlib.sha256(record_bytes).hexdigest(), record["logical_key"]),
            )
            connection.commit()

        with self.assertRaisesRegex(ValueError, "reference provenance"):
            DiscordEvidenceCollector(_FixtureTransport()).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="youtube-reference-tamper",
                download_assets=True,
            )

    def test_disabled_asset_download_is_truthfully_partial(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "embeds": [
                        {"video": {"url": "https://external.example/watch"}}
                    ],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )

        result = DiscordEvidenceCollector(transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="disabled-external-media",
            download_assets=False,
        )

        transport.assert_exhausted(self)
        self.assertEqual(result.manifest["status"], "partial")
        self.assertEqual(result.manifest["media"]["status"], "not_requested")
        self.assertEqual(
            result.manifest["media"]["terminal_reason"],
            "asset_download_disabled",
        )
        self.assertEqual(result.manifest["media"]["not_requested"], 1)
        record = json.loads(
            next((result.run_root / "asset-records").glob("*.json")).read_text()
        )
        self.assertEqual(record["status"], "not_requested")
        self.assertEqual(record["terminal_reason"], "asset_download_disabled")

    def test_component_thumbnail_and_gallery_require_image_mime(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "components": [
                        {
                            "type": 11,
                            "media": {"url": "https://cdn.example/thumbnail"},
                        },
                        {
                            "type": 12,
                            "items": [
                                {
                                    "media": {
                                        "url": "https://cdn.example/gallery"
                                    }
                                }
                            ],
                        },
                    ],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(
            "https://cdn.example/thumbnail",
            _ByteStream([b"html"], content_type="text/html", content_length=4),
        )
        transport.add_media(
            "https://cdn.example/gallery",
            _ByteStream([b"video"], content_type="video/mp4", content_length=5),
        )
        result = DiscordEvidenceCollector(transport, byte_transport=transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="component-mime",
            download_assets=True,
        )
        transport.assert_exhausted(self)
        records = [
            json.loads(line)
            for line in (result.run_root / "asset-index.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(records), 2)
        self.assertTrue(all(record["status"] == "failed" for record in records))

    def test_attachment_alias_is_one_asset_with_multiple_observations(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        url = "https://cdn.example/aliased"
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "attachments": [
                        {
                            "id": "400",
                            "filename": "asset.png",
                            "url": url,
                            "size": 4,
                            "content_type": "image/png",
                        }
                    ],
                    "embeds": [{"image": {"url": "attachment://asset.png"}}],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(
            url,
            _ByteStream([b"data"], content_type="image/png", content_length=4),
        )
        result = DiscordEvidenceCollector(transport, byte_transport=transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="attachment-alias",
            download_assets=True,
        )
        transport.assert_exhausted(self)
        records = [
            json.loads(line)
            for line in (result.run_root / "asset-index.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["logical_key"], "30:attachment:400")
        self.assertEqual(records[0]["status"], "complete")
        self.assertEqual(len(records[0]["observations"]), 2)
        self.assertEqual(transport.media_calls, [url])

    def test_sticker_items_and_legacy_stickers_share_one_asset(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        sticker_url = "https://cdn.discordapp.com/stickers/501.png"
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "sticker_items": [
                        {"id": "501", "name": "compact", "format_type": 1}
                    ],
                    "stickers": [
                        {
                            "id": "501",
                            "name": "expanded",
                            "format_type": 1,
                            "tags": "chart",
                        }
                    ],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(
            sticker_url,
            _ByteStream([b"png!"], content_type="image/png", content_length=4),
        )

        result = DiscordEvidenceCollector(transport, byte_transport=transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="sticker-alias",
            download_assets=True,
        )

        transport.assert_exhausted(self)
        records = [
            json.loads(line)
            for line in (result.run_root / "asset-index.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["logical_key"], "sticker:501")
        self.assertEqual(record["field"], "sticker")
        self.assertEqual(record["status"], "complete")
        self.assertEqual(len(record["sources"]), 2)
        self.assertEqual(len(record["observations"]), 2)
        self.assertEqual(record["identity_conflicts"], [])
        self.assertEqual(transport.media_calls, [sticker_url])

    def test_embed_proxy_candidates_are_ordered_auditable_and_typed(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        direct_image = "https://origin.example/expired-image"
        proxy_image = "https://proxy.example/captured-image"
        proxy_only = "https://proxy.example/proxy-only-image"
        webpage_video = "https://video.example/watch"
        video_thumbnail = "https://proxy.example/video-thumbnail"
        webpage_image = "https://image.example/view"
        image_thumbnail = "https://proxy.example/image-thumbnail"
        malformed_direct = "https://[::1"
        safe_proxy = "https://proxy.example/malformed-url-fallback"
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "embeds": [
                        {
                            "image": {
                                "url": direct_image,
                                "proxy_url": proxy_image,
                            }
                        },
                        {"image": {"proxy_url": proxy_only}},
                        {
                            "video": {
                                "url": webpage_video,
                                "proxy_url": video_thumbnail,
                            }
                        },
                        {
                            "image": {
                                "url": webpage_image,
                                "proxy_url": image_thumbnail,
                            }
                        },
                        {
                            "image": {
                                "url": malformed_direct,
                                "proxy_url": safe_proxy,
                            }
                        },
                    ],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(
            direct_image,
            DiscordAPIError("expired", status_code=404),
        )
        transport.add_media(
            proxy_image,
            _ByteStream([b"png1"], content_type="image/png", content_length=4),
        )
        transport.add_media(
            proxy_only,
            _ByteStream([b"png2"], content_type="image/png", content_length=4),
        )
        transport.add_media(
            webpage_video,
            _ByteStream([b"<html>"], content_type="text/html", content_length=6),
        )
        transport.add_media(
            video_thumbnail,
            _ByteStream([b"png3"], content_type="image/png", content_length=4),
        )
        transport.add_media(
            webpage_image,
            _ByteStream([b"<html>"], content_type="text/html", content_length=6),
        )
        transport.add_media(
            image_thumbnail,
            _ByteStream([b"png4"], content_type="image/png", content_length=4),
        )
        transport.add_media(
            malformed_direct,
            discord_collector_module.DiscordMediaSecurityError(
                "malformed media URL"
            ),
        )
        transport.add_media(
            safe_proxy,
            _ByteStream([b"png5"], content_type="image/png", content_length=4),
        )

        result = DiscordEvidenceCollector(transport, byte_transport=transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="proxy-candidates",
            download_assets=True,
        )

        transport.assert_exhausted(self)
        records = {
            record["logical_key"]: record
            for record in (
                json.loads(line)
                for line in (result.run_root / "asset-index.jsonl").read_text().splitlines()
            )
        }
        fallback = records["30:embed:0:image"]
        self.assertEqual(fallback["status"], "complete")
        self.assertEqual(fallback["candidate_urls"], [direct_image, proxy_image])
        self.assertEqual(fallback["observed_urls"], [direct_image, proxy_image])
        self.assertEqual(
            [attempt["url"] for attempt in fallback["attempt_history"]],
            [direct_image, proxy_image],
        )
        self.assertEqual(records["30:embed:1:image"]["status"], "complete")
        video = records["30:embed:2:video"]
        self.assertEqual(video["status"], "reference_only")
        self.assertEqual(video["candidate_urls"], [webpage_video, video_thumbnail])
        self.assertEqual(video["observed_urls"], [webpage_video, video_thumbnail])
        self.assertEqual(
            [attempt["url"] for attempt in video["attempt_history"]],
            [webpage_video, video_thumbnail],
        )
        self.assertEqual(video["url"], webpage_video)
        html_fallback = records["30:embed:3:image"]
        self.assertEqual(html_fallback["status"], "complete")
        self.assertEqual(
            [attempt["url"] for attempt in html_fallback["attempt_history"]],
            [webpage_image, image_thumbnail],
        )
        malformed_fallback = records["30:embed:4:image"]
        self.assertEqual(malformed_fallback["status"], "complete")
        self.assertEqual(
            [attempt["terminal_reason"] for attempt in malformed_fallback["attempt_history"]],
            ["unsafe_media_url", "downloaded"],
        )
        self.assertEqual(
            transport.media_calls,
            [
                direct_image,
                proxy_image,
                proxy_only,
                webpage_video,
                video_thumbnail,
                webpage_image,
                image_thumbnail,
                malformed_direct,
                safe_proxy,
            ],
        )

    def test_unexpected_media_fixture_response_is_not_swallowed(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "attachments": [
                        {
                            "id": "missing-fixture",
                            "filename": "missing.bin",
                            "url": "https://cdn.example/unexpected",
                        }
                    ],
                }
            ],
            {"limit": 100},
        )

        with self.assertRaisesRegex(AssertionError, "unexpected media request"):
            DiscordEvidenceCollector(transport, byte_transport=transport).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="unexpected-media",
                download_assets=True,
            )

    def test_landed_page_is_replayed_after_asset_interrupt_before_cursor_advances(self) -> None:
        first = _FixtureTransport()
        _add_inventory(first, [{"id": "100", "type": 0, "name": "text"}])
        _add_archive_triplet(first, "100")
        message = {
            "id": "30",
            "thread": {"id": "400", "type": 11, "parent_id": "100"},
            "attachments": [
                {
                    "id": "asset",
                    "filename": "asset.png",
                    "url": "https://cdn.example/interrupted",
                    "content_type": "image/png",
                    "size": 8,
                }
            ],
        }
        first.add_json("/channels/100/messages", [message], {"limit": 100})
        interrupted_stream = _InterruptingByteStream(
            [], content_type="image/png", content_length=8
        )
        first.add_media("https://cdn.example/interrupted", interrupted_stream)

        with self.assertRaises(KeyboardInterrupt):
            DiscordEvidenceCollector(first, byte_transport=first).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("100")),
                run_id="landed-page",
                download_assets=True,
            )

        run_root = self.workspace / "evidence/runs/landed-page"
        checkpoint = json.loads((run_root / "checkpoint.json").read_text())
        message_state = checkpoint["streams"]["messages_100"]
        self.assertEqual(message_state["pages"], 1)
        self.assertEqual(message_state["processed_pages"], 0)
        self.assertEqual(message_state["page_states"][0]["processing_status"], "landed")
        self.assertIsNone(message_state["next_cursor"])
        self.assertTrue(interrupted_stream.closed)
        first.assert_exhausted(self)

        resumed = _FixtureTransport()
        resumed.add_media(
            "https://cdn.example/interrupted",
            _ByteStream([b"recovery"], content_type="image/png", content_length=8),
        )
        resumed.add_json(
            "/channels/100/messages", [], {"limit": 100, "before": "30"}
        )
        resumed.add_json(
            "/channels/100/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        _add_empty_messages_and_pins(resumed, "400")

        result = DiscordEvidenceCollector(resumed, byte_transport=resumed).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("100")),
            run_id="landed-page",
            download_assets=True,
        )

        self.assertEqual(result.manifest["status"], "complete")
        self.assertEqual(
            result.manifest["streams"]["messages_100"]["processed_pages"], 2
        )
        target_inventory = json.loads(
            (result.run_root / "inventory/targets.json").read_text()
        )
        self.assertEqual([thread["id"] for thread in target_inventory["threads"]], ["400"])
        assets = [
            json.loads(line)
            for line in (result.run_root / "asset-index.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["status"], "complete")
        resumed.assert_exhausted(self)

    def test_complete_asset_records_fail_closed_when_tampered_deleted_or_escaped(self) -> None:
        cases = (
            "tampered",
            "deleted",
            "escaped",
            "renamed-record",
            "deleted-record",
        )
        for case in cases:
            with self.subTest(case=case):
                transport = _FixtureTransport()
                _add_inventory(
                    transport, [{"id": "300", "type": 2, "name": "voice"}]
                )
                message = {
                    "id": "30",
                    "attachments": [
                        {
                            "id": "asset",
                            "filename": "asset.bin",
                            "url": f"https://cdn.example/{case}",
                            "size": 4,
                        }
                    ],
                }
                transport.add_json(
                    "/channels/300/messages", [message], {"limit": 100}
                )
                transport.add_json(
                    "/channels/300/messages",
                    [],
                    {"limit": 100, "before": "30"},
                )
                transport.add_json(
                    "/channels/300/messages/pins",
                    {"items": [], "has_more": False},
                    {"limit": 50},
                )
                transport.add_media(
                    f"https://cdn.example/{case}",
                    _ByteStream([b"data"], content_length=4),
                )
                result = DiscordEvidenceCollector(
                    transport, byte_transport=transport
                ).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                    run_id=f"asset-{case}",
                    download_assets=True,
                )
                transport.assert_exhausted(self)
                record_path = next((result.run_root / "asset-records").glob("*.json"))
                record = json.loads(record_path.read_text())
                blob = result.run_root / record["blob_path"]
                if case == "tampered":
                    blob.write_bytes(b"evil")
                elif case == "deleted":
                    blob.unlink()
                elif case == "escaped":
                    record["blob_path"] = "../../outside"
                    record_path.write_text(json.dumps(record), encoding="utf-8")
                elif case == "renamed-record":
                    record_path.rename(record_path.with_name("wrong-name.json"))
                else:
                    record_path.unlink()

                with self.assertRaises(ValueError):
                    DiscordEvidenceCollector(_FixtureTransport()).collect(
                        workspace=self.workspace,
                        output_dir="evidence",
                        targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                        run_id=f"asset-{case}",
                        download_assets=True,
                    )

    def test_inventory_single_responses_are_exclusive_and_changed_resume_fails(self) -> None:
        inventory_cases = {
            "inventory_bot": ("bot.json", "/users/@me", {"id": "99"}),
            "inventory_guild": (
                "guild.json",
                "/guilds/1",
                {"id": "1", "name": "changed"},
            ),
            "inventory_channels": (
                "channels.json",
                "/guilds/1/channels",
                [{"id": "300", "type": 13, "name": "changed"}],
            ),
            "inventory_active_threads": (
                "active-threads.json",
                "/guilds/1/threads/active",
                {"threads": [], "members": [], "changed": True},
            ),
        }
        for index, (stream_key, (filename, endpoint, changed)) in enumerate(
            inventory_cases.items()
        ):
            with self.subTest(stream=stream_key):
                transport = _FixtureTransport()
                _add_inventory(
                    transport, [{"id": "300", "type": 2, "name": "voice"}]
                )
                _add_empty_messages_and_pins(transport, "300")
                run_id = f"inventory-exclusive-{index}"
                result = DiscordEvidenceCollector(transport).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                    run_id=run_id,
                    download_assets=False,
                )
                transport.assert_exhausted(self)
                inventory_path = result.run_root / "inventory" / filename
                original = inventory_path.read_bytes()
                checkpoint_path = result.run_root / "checkpoint.json"
                checkpoint = json.loads(checkpoint_path.read_text())
                del checkpoint["streams"][stream_key]
                checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
                resumed = _FixtureTransport()
                resumed.add_json(endpoint, changed)

                with self.assertRaises(ValueError):
                    DiscordEvidenceCollector(resumed).collect(
                        workspace=self.workspace,
                        output_dir="evidence",
                        targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                        run_id=run_id,
                        download_assets=False,
                    )
                self.assertEqual(inventory_path.read_bytes(), original)
                resumed.assert_exhausted(self)

    def test_target_metadata_raw_response_is_exclusive_on_resume(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [])
        transport.add_json(
            "/channels/900",
            {"id": "900", "type": 11, "guild_id": "1", "parent_id": "100"},
        )
        _add_empty_messages_and_pins(transport, "900")
        snapshot = _snapshot(
            _target("900", kind="thread audit label", parent_id="100")
        )
        result = DiscordEvidenceCollector(transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=snapshot,
            run_id="target-exclusive",
            download_assets=False,
        )
        transport.assert_exhausted(self)
        page_path = result.run_root / "pages/target_metadata_900/000001.json"
        original = page_path.read_bytes()
        checkpoint_path = result.run_root / "checkpoint.json"
        checkpoint = json.loads(checkpoint_path.read_text())
        del checkpoint["streams"]["target_metadata_900"]
        checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
        resumed = _FixtureTransport()
        resumed.add_json(
            "/channels/900",
            {
                "id": "900",
                "type": 11,
                "guild_id": "1",
                "parent_id": "100",
                "changed": True,
            },
        )

        with self.assertRaises(ValueError):
            DiscordEvidenceCollector(resumed).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=snapshot,
                run_id="target-exclusive",
                download_assets=False,
            )
        self.assertEqual(page_path.read_bytes(), original)
        resumed.assert_exhausted(self)

    def test_failed_asset_retries_with_later_signed_url_and_keeps_history(self) -> None:
        first = _FixtureTransport()
        _add_inventory(first, [{"id": "300", "type": 2, "name": "voice"}])
        attachment = {
            "id": "asset",
            "filename": "asset.png",
            "url": "https://cdn.example/expired",
            "content_type": "image/png",
            "size": 4,
        }
        first.add_json(
            "/channels/300/messages",
            [{"id": "30", "attachments": [attachment]}],
            {"limit": 100},
        )
        first.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        first.add_json(
            "/channels/300/messages/pins", KeyboardInterrupt(), {"limit": 50}
        )
        first.add_media(
            "https://cdn.example/expired",
            DiscordAPIError("expired", status_code=404),
        )
        with self.assertRaises(KeyboardInterrupt):
            DiscordEvidenceCollector(first, byte_transport=first).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="asset-retry",
                download_assets=True,
            )
        first.assert_exhausted(self)

        refreshed = {**attachment, "url": "https://cdn.example/refreshed"}
        resumed = _FixtureTransport()
        resumed.add_json(
            "/channels/300/messages/pins",
            {
                "items": [
                    {
                        "pinned_at": "2026-07-20T00:00:00+00:00",
                        "message": {"id": "30", "attachments": [refreshed]},
                    }
                ],
                "has_more": False,
            },
            {"limit": 50},
        )
        resumed.add_media(
            "https://cdn.example/refreshed",
            _ByteStream([b"data"], content_type="image/png", content_length=4),
        )
        result = DiscordEvidenceCollector(resumed, byte_transport=resumed).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="asset-retry",
            download_assets=True,
        )

        self.assertEqual(result.manifest["status"], "complete")
        record = json.loads(
            (next((result.run_root / "asset-records").glob("*.json"))).read_text()
        )
        self.assertEqual(record["status"], "complete")
        self.assertEqual(record["url"], "https://cdn.example/refreshed")
        self.assertEqual(
            [(attempt["url"], attempt["status"]) for attempt in record["attempt_history"]],
            [
                ("https://cdn.example/expired", "failed"),
                ("https://cdn.example/refreshed", "complete"),
            ],
        )
        self.assertEqual(len(list((result.run_root / "asset-records").glob("*.json"))), 1)
        self.assertEqual(len(list((result.run_root / "assets/sha256").glob("*/*"))), 1)
        resumed.assert_exhausted(self)

    def test_declared_size_and_external_embed_mismatch_are_truthful(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "attachments": [
                        {
                            "id": "wrong-size",
                            "filename": "file.bin",
                            "url": "https://cdn.example/wrong-size",
                            "size": 999,
                        }
                    ],
                    "embeds": [
                        {
                            "image": {"url": "https://cdn.example/html-image"},
                            "video": {"url": "https://cdn.example/image-video"},
                        }
                    ],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(
            "https://cdn.example/wrong-size", _ByteStream([b"data"], content_length=4)
        )
        transport.add_media(
            "https://cdn.example/html-image",
            _ByteStream([b"html"], content_type="text/html", content_length=4),
        )
        transport.add_media(
            "https://cdn.example/image-video",
            _ByteStream([b"png"], content_type="image/png", content_length=3),
        )

        result = DiscordEvidenceCollector(transport, byte_transport=transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="typed-media",
            download_assets=True,
        )
        records = [
            json.loads(line)
            for line in (result.run_root / "asset-index.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            {record["terminal_reason"] for record in records},
            {
                "declared_size_mismatch",
                "media_reference_not_binary",
                "media_type_mismatch",
            },
        )
        records_by_key = {record["logical_key"]: record for record in records}
        self.assertEqual(
            records_by_key["30:attachment:wrong-size"]["status"],
            "captured_with_warning",
        )
        self.assertEqual(
            records_by_key["30:embed:0:image"]["status"],
            "reference_only",
        )
        self.assertEqual(
            records_by_key["30:embed:0:video"]["status"],
            "failed",
        )
        self.assertEqual(
            result.manifest["media"]["status"],
            "partial",
        )
        self.assertEqual(result.manifest["media"]["captured_with_warning"], 1)
        self.assertEqual(result.manifest["media"]["reference_only"], 1)
        self.assertEqual(result.manifest["media"]["failed"], 1)
        self.assertEqual(result.manifest["status"], "partial")
        transport.assert_exhausted(self)

    def test_invalid_live_metadata_is_failed_without_planning_requests(self) -> None:
        invalid_supplements = (
            {"id": "901", "type": 11, "guild_id": "1", "parent_id": "100"},
            {"id": "900", "type": "11", "guild_id": "1", "parent_id": "100"},
            {"id": "900", "type": 99, "guild_id": "1", "parent_id": "100"},
            {"id": "900", "type": 11, "guild_id": "2", "parent_id": "100"},
            {"id": "900", "type": 11, "parent_id": "100"},
            {"id": "900", "type": 11, "guild_id": "1", "parent_id": "101"},
        )
        snapshot = _snapshot(_target("900", kind="audit thread", parent_id="100"))
        for index, metadata in enumerate(invalid_supplements):
            with self.subTest(metadata=metadata):
                transport = _FixtureTransport()
                _add_inventory(transport, [])
                transport.add_json("/channels/900", metadata)
                result = DiscordEvidenceCollector(transport).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=snapshot,
                    run_id=f"invalid-supplement-{index}",
                    download_assets=False,
                )
                self.assertEqual(result.manifest["status"], "partial")
                self.assertEqual(
                    result.manifest["streams"]["target_metadata_900"]["status"],
                    "failed",
                )
                self.assertFalse(
                    any(path.startswith("/channels/900/messages") for path, _ in transport.json_calls)
                )
                transport.assert_exhausted(self)

        invalid_inventory = _FixtureTransport()
        _add_inventory(
            invalid_inventory,
            [{"id": "300", "type": "15", "name": "ambiguous"}],
        )
        result = DiscordEvidenceCollector(invalid_inventory).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_FORUM (15)")),
            run_id="invalid-inventory",
            download_assets=False,
        )
        self.assertEqual(result.manifest["status"], "partial")
        self.assertEqual(
            result.manifest["streams"]["inventory_channels_validation"]["status"],
            "failed",
        )
        self.assertFalse(
            any(path.startswith("/channels/300/") for path, _ in invalid_inventory.json_calls)
        )
        invalid_inventory.assert_exhausted(self)

    def test_asset_observations_allow_url_refresh_but_reject_identity_conflicts(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        message = {
            "id": "30",
            "attachments": [
                {
                    "id": "stable",
                    "filename": "stable.png",
                    "size": 4,
                    "content_type": "image/png; charset=binary",
                    "url": "https://cdn.example/stable-old",
                    "proxy_url": "https://proxy.example/stable-old",
                },
                {
                    "id": "conflict",
                    "filename": "conflict.png",
                    "size": 4,
                    "content_type": "image/png",
                    "url": "https://cdn.example/conflict-old",
                },
            ],
            "embeds": [
                {
                    "image": {
                        "url": "https://cdn.example/embed-old",
                        "proxy_url": "https://proxy.example/embed-old",
                        "width": 100,
                        "height": 100,
                    }
                }
            ],
        }
        transport.add_json("/channels/300/messages", [message], {"limit": 100})
        transport.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        refreshed_message = {
            "id": "30",
            "attachments": [
                {
                    **message["attachments"][0],
                    "filename": "renamed-stable.png",
                    "url": "https://cdn.example/stable-new",
                    "proxy_url": "https://proxy.example/stable-new",
                },
                {
                    **message["attachments"][1],
                    "size": 999,
                    "url": "https://cdn.example/conflict-new",
                },
            ],
            "embeds": [
                {
                    "image": {
                        "url": "https://cdn.example/embed-new",
                        "proxy_url": "https://proxy.example/embed-new",
                        "width": 101,
                        "height": 100,
                    }
                }
            ],
        }
        transport.add_json(
            "/channels/300/messages/pins",
            {
                "items": [
                    {
                        "pinned_at": "2026-07-20T00:00:00+00:00",
                        "message": refreshed_message,
                    }
                ],
                "has_more": False,
            },
            {"limit": 50},
        )
        for url, body in (
            ("https://cdn.example/stable-old", b"stab"),
            ("https://cdn.example/conflict-old", b"conf"),
            ("https://cdn.example/embed-old", b"img"),
        ):
            transport.add_media(
                url,
                _ByteStream([body], content_type="image/png", content_length=len(body)),
            )

        result = DiscordEvidenceCollector(transport, byte_transport=transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="identity-observations",
            download_assets=True,
        )

        records = {
            record["logical_key"]: record
            for record in (
                json.loads(line)
                for line in (result.run_root / "asset-index.jsonl").read_text().splitlines()
            )
        }
        self.assertEqual(records["30:attachment:stable"]["status"], "complete")
        self.assertEqual(len(records["30:attachment:stable"]["observations"]), 2)
        self.assertEqual(
            records["30:attachment:conflict"]["terminal_reason"],
            "logical_identity_conflict",
        )
        self.assertEqual(
            records["30:embed:0:image"]["terminal_reason"],
            "logical_identity_conflict",
        )
        self.assertEqual(len(records["30:attachment:conflict"]["observations"]), 2)
        self.assertEqual(result.manifest["status"], "partial")
        self.assertNotIn("https://cdn.example/stable-new", transport.media_calls)
        self.assertNotIn("https://cdn.example/conflict-new", transport.media_calls)
        self.assertNotIn("https://cdn.example/embed-new", transport.media_calls)
        transport.assert_exhausted(self)

    def test_malformed_message_and_pin_items_create_failed_validation_streams(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [7, {"id": "bad", "content": "invalid"}, {"id": "30", "content": "valid"}],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        pinned_at = "2026-07-20T00:00:00+00:00"
        transport.add_json(
            "/channels/300/messages/pins",
            {
                "items": [
                    7,
                    {"message": {"id": "31"}},
                    {"pinned_at": 7, "message": {"id": "31"}},
                    {"pinned_at": pinned_at},
                    {"pinned_at": pinned_at, "message": 7},
                    {"pinned_at": pinned_at, "message": {"id": "bad"}},
                    {"pinned_at": pinned_at, "message": {"id": "31"}},
                ],
                "has_more": False,
            },
            {"limit": 50},
        )

        result = DiscordEvidenceCollector(transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="malformed-items",
            download_assets=False,
        )

        message_validation = result.manifest["streams"][
            "messages_300_item_validation"
        ]
        pin_validation = result.manifest["streams"]["pins_300_item_validation"]
        self.assertEqual(message_validation["status"], "failed")
        self.assertEqual(message_validation["invalid_items"], 2)
        self.assertEqual(message_validation["valid_items"], 1)
        self.assertEqual(pin_validation["status"], "failed")
        self.assertEqual(pin_validation["invalid_items"], 6)
        self.assertEqual(pin_validation["valid_items"], 1)
        self.assertTrue(all("content" not in item for item in pin_validation["diagnostics"]))
        self.assertEqual(result.manifest["status"], "partial")
        raw_pin_page = json.loads(
            (result.run_root / "pages/pins_300/000001.json").read_text()
        )
        self.assertEqual(raw_pin_page["payload"]["items"][0], 7)
        transport.assert_exhausted(self)

    def test_resume_validates_blob_references_in_historical_attempts(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        attachment = {
            "id": "asset",
            "filename": "asset.png",
            "size": 4,
            "content_type": "image/png",
            "url": "https://cdn.example/current-success",
        }
        transport.add_json(
            "/channels/300/messages",
            [{"id": "30", "attachments": [attachment]}],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages", [], {"limit": 100, "before": "30"}
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(
            attachment["url"],
            _ByteStream([b"png!"], content_type="image/png", content_length=4),
        )

        result = DiscordEvidenceCollector(transport, byte_transport=transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="attempt-blob-integrity",
            download_assets=True,
        )
        self.assertEqual(result.manifest["status"], "complete")
        record_path = next((result.run_root / "asset-records").glob("*.json"))
        record = json.loads(record_path.read_text())
        historical_bytes = b"html"
        historical_digest = hashlib.sha256(historical_bytes).hexdigest()
        historical_blob = (
            result.run_root
            / "assets"
            / "sha256"
            / historical_digest[:2]
            / f"{historical_digest}.bin"
        )
        historical_blob.parent.mkdir(exist_ok=True)
        historical_blob.write_bytes(historical_bytes)
        record["attempt_history"].insert(
            0,
            {
                "url": "https://cdn.example/historical-mime-failure",
                "status": "failed",
                "terminal_reason": "mime_mismatch",
                "http_content_type": "text/html",
                "http_content_length": 4,
                "actual_bytes": 4,
                "sha256": historical_digest,
                "blob_path": historical_blob.relative_to(result.run_root).as_posix(),
            },
        )
        record_bytes = discord_collector_module._canonical_json_bytes(record)
        record_path.write_bytes(record_bytes)
        with closing(
            sqlite3.connect(result.run_root / "asset-ledger.sqlite3")
        ) as connection:
            connection.execute(
                "UPDATE asset_records SET committed_sha256 = ?, pending_sha256 = NULL "
                "WHERE logical_key = ?",
                (hashlib.sha256(record_bytes).hexdigest(), record["logical_key"]),
            )
            connection.commit()
        historical_blob.unlink()
        transport.assert_exhausted(self)

        with self.assertRaises(ValueError):
            DiscordEvidenceCollector(_FixtureTransport()).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="attempt-blob-integrity",
                download_assets=True,
            )

    def test_resume_rejects_covered_record_and_attempt_above_request_limit(self) -> None:
        url = "https://cdn.example/request-bound-limit"
        transport = _FixtureTransport()
        self._configure_single_attachment_run(transport, url=url)
        transport.add_media(
            url,
            _ByteStream([b"data"], content_type="image/png", content_length=4),
        )
        snapshot = _snapshot(_target("300", kind="GUILD_VOICE (2)"))
        result = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
            max_asset_bytes=4,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=snapshot,
            run_id="request-bound-asset-limit",
            download_assets=True,
        )
        transport.assert_exhausted(self)

        oversized = b"large"
        oversized_sha = hashlib.sha256(oversized).hexdigest()
        oversized_path = (
            result.run_root
            / "assets"
            / "sha256"
            / oversized_sha[:2]
            / f"{oversized_sha}.bin"
        )
        oversized_path.parent.mkdir(parents=True, exist_ok=True)
        oversized_path.write_bytes(oversized)
        record_path = next((result.run_root / "asset-records").glob("*.json"))
        record = json.loads(record_path.read_text())
        outcome = {
            "status": "captured_with_warning",
            "terminal_reason": "declared_size_mismatch",
            "http_content_type": "image/png",
            "http_content_length": len(oversized),
            "actual_bytes": len(oversized),
            "sha256": oversized_sha,
            "blob_path": oversized_path.relative_to(result.run_root).as_posix(),
        }
        record.update(outcome)
        record["attempt_history"][-1].update(outcome)
        record_bytes = discord_collector_module._canonical_json_bytes(record)
        record_path.write_bytes(record_bytes)
        with closing(sqlite3.connect(result.run_root / "asset-ledger.sqlite3")) as connection:
            connection.execute(
                "UPDATE asset_records SET committed_sha256 = ?, pending_sha256 = NULL "
                "WHERE logical_key = ?",
                (hashlib.sha256(record_bytes).hexdigest(), record["logical_key"]),
            )
            connection.commit()

        with self.assertRaisesRegex(ValueError, "maximum asset size"):
            DiscordEvidenceCollector(
                _FixtureTransport(),
                max_asset_bytes=4,
            ).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=snapshot,
                run_id="request-bound-asset-limit",
                download_assets=True,
            )

    def test_genuine_e754_asset_records_migrate_but_malformed_current_records_fail(self) -> None:
        current_complete: tuple[Path, dict[str, object]] | None = None
        for status in ("complete", "failed", "in_progress"):
            with self.subTest(status=status):
                run_id = f"legacy-e754-{status}"
                url = f"https://cdn.example/{run_id}"
                snapshot = _snapshot(_target("300", kind="GUILD_VOICE (2)"))
                initial = _FixtureTransport()
                _add_inventory(
                    initial, [{"id": "300", "type": 2, "name": "voice"}]
                )
                attachment = {
                    "id": "asset",
                    "filename": "asset.png",
                    "size": 4,
                    "content_type": "image/png",
                    "url": url,
                    "proxy_url": f"https://proxy.example/{run_id}",
                }
                initial.add_json(
                    "/channels/300/messages",
                    [{"id": "30", "attachments": [attachment]}],
                    {"limit": 100},
                )
                initial.add_json(
                    "/channels/300/messages",
                    [],
                    {"limit": 100, "before": "30"},
                )
                initial.add_json(
                    "/channels/300/messages/pins",
                    {"items": [], "has_more": False},
                    {"limit": 50},
                )
                if status == "complete":
                    initial.add_media(
                        url,
                        _ByteStream(
                            [b"data"], content_type="image/png", content_length=4
                        ),
                    )
                elif status == "failed":
                    initial.add_media(
                        url,
                        _ByteStream(
                            [b"html"], content_type="text/html", content_length=4
                        ),
                    )
                else:
                    initial.add_media(
                        url,
                        DiscordAPIError("interrupted predecessor", status_code=404),
                    )
                result = DiscordEvidenceCollector(
                    initial, byte_transport=initial
                ).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=snapshot,
                    run_id=run_id,
                    download_assets=True,
                )
                initial.assert_exhausted(self)

                record_path = next((result.run_root / "asset-records").glob("*.json"))
                legacy = json.loads(record_path.read_text())
                for key in (
                    "schema_version",
                    "candidate_urls",
                    "identity_metadata",
                    "observations",
                    "identity_conflicts",
                ):
                    legacy.pop(key, None)
                if status == "in_progress":
                    legacy.update(
                        {
                            "status": "in_progress",
                            "terminal_reason": "interrupted",
                            "http_content_type": None,
                            "http_content_length": None,
                            "actual_bytes": 0,
                            "sha256": None,
                            "blob_path": None,
                        }
                    )
                    legacy["attempt_history"][-1].update(
                        {
                            "status": "interrupted",
                            "terminal_reason": "interrupted",
                            "http_content_type": None,
                            "http_content_length": None,
                            "actual_bytes": 0,
                            "sha256": None,
                            "blob_path": None,
                        }
                    )
                elif status == "failed":
                    legacy["status"] = "failed"
                legacy_bytes = (
                    json.dumps(
                        legacy,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode()
                record_path.write_bytes(legacy_bytes)
                checkpoint_path = result.run_root / "checkpoint.json"
                ledger_path = result.run_root / "asset-ledger.sqlite3"
                with closing(sqlite3.connect(ledger_path)) as connection:
                    connection.execute(
                        "UPDATE asset_records SET committed_sha256 = ?, "
                        "pending_sha256 = NULL WHERE logical_key = ?",
                        (
                            hashlib.sha256(legacy_bytes).hexdigest(),
                            legacy["logical_key"],
                        ),
                    )
                    connection.commit()

                resumed = _FixtureTransport()
                if status == "in_progress":
                    resumed.add_media(
                        url,
                        _ByteStream(
                            [b"data"], content_type="image/png", content_length=4
                        ),
                    )
                resumed_result = DiscordEvidenceCollector(
                    resumed, byte_transport=resumed
                ).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=snapshot,
                    run_id=run_id,
                    download_assets=True,
                )
                migrated = json.loads(record_path.read_text())
                self.assertEqual(migrated["schema_version"], 4)
                self.assertEqual(
                    migrated["identity_metadata"],
                    {
                        "id": "asset",
                        "size": 4,
                        "content_type": "image/png",
                    },
                )
                self.assertEqual(len(migrated["observations"]), 1)
                self.assertEqual(migrated["identity_conflicts"], [])
                self.assertEqual(
                    resumed.media_calls.count(url),
                    1 if status == "in_progress" else 0,
                )
                expected_status = "partial" if status == "failed" else "complete"
                self.assertEqual(resumed_result.manifest["status"], expected_status)
                migrated_checkpoint = json.loads(checkpoint_path.read_text())
                self.assertEqual(migrated_checkpoint["assets"], {})
                with closing(sqlite3.connect(ledger_path)) as connection:
                    migrated_ledger = connection.execute(
                        "SELECT committed_sha256, pending_sha256 FROM asset_records "
                        "WHERE logical_key = ?",
                        (legacy["logical_key"],),
                    ).fetchone()
                self.assertEqual(
                    migrated_ledger,
                    (hashlib.sha256(record_path.read_bytes()).hexdigest(), None),
                )
                resumed.assert_exhausted(self)
                if status == "complete":
                    current_complete = (result.run_root, snapshot)

        assert current_complete is not None
        run_root, snapshot = current_complete
        record_path = next((run_root / "asset-records").glob("*.json"))
        malformed = json.loads(record_path.read_text())
        malformed.pop("observations")
        malformed_bytes = (
            json.dumps(
                malformed,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        record_path.write_bytes(malformed_bytes)
        with closing(
            sqlite3.connect(run_root / "asset-ledger.sqlite3")
        ) as connection:
            connection.execute(
                "UPDATE asset_records SET committed_sha256 = ?, pending_sha256 = NULL "
                "WHERE logical_key = ?",
                (hashlib.sha256(malformed_bytes).hexdigest(), malformed["logical_key"]),
            )
            connection.commit()
        with self.assertRaises(ValueError):
            DiscordEvidenceCollector(_FixtureTransport()).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=snapshot,
                run_id="legacy-e754-complete",
                download_assets=True,
            )

    def test_resume_uses_checkpoint_and_detects_page_tampering(self) -> None:
        first = _FixtureTransport()
        _add_inventory(first, [{"id": "300", "type": 2, "name": "voice"}])
        first.add_json(
            "/channels/300/messages",
            [{"id": "30", "content": "first"}],
            {"limit": 100},
        )
        first.add_json(
            "/channels/300/messages",
            KeyboardInterrupt(),
            {"limit": 100, "before": "30"},
        )
        collector = DiscordEvidenceCollector(first, byte_transport=first)
        with self.assertRaises(KeyboardInterrupt):
            collector.collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="resume",
                download_assets=False,
            )

        checkpoint = json.loads(
            (self.workspace / "evidence/runs/resume/checkpoint.json").read_text()
        )
        self.assertEqual(checkpoint["streams"]["messages_300"]["next_cursor"], "30")
        self.assertEqual(checkpoint["streams"]["messages_300"]["pages"], 1)

        resumed = _FixtureTransport()
        resumed.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        resumed.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        result = DiscordEvidenceCollector(resumed, byte_transport=resumed).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
            run_id="resume",
            download_assets=False,
        )
        self.assertEqual(result.manifest["status"], "complete")
        self.assertEqual(result.manifest["streams"]["messages_300"]["pages"], 2)
        self.assertEqual(
            result.manifest["streams"]["messages_300"]["terminal_reason"],
            "empty_page",
        )
        resumed.assert_exhausted(self)

        first_page = result.run_root / "pages/messages_300/000001.json"
        first_page.write_text('{"tampered":true}\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            DiscordEvidenceCollector(_FixtureTransport()).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=_snapshot(_target("300", kind="GUILD_VOICE (2)")),
                run_id="resume",
                download_assets=False,
            )

    def test_page_limit_is_truthfully_partial_and_records_terminal_reason(self) -> None:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 13, "name": "stage"}])
        transport.add_json(
            "/channels/300/messages",
            [{"id": "30", "unknown": "raw"}],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )

        result = DiscordEvidenceCollector(transport, byte_transport=transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=_snapshot(_target("300", kind="GUILD_STAGE_VOICE (13)")),
            run_id="limited",
            max_pages=1,
            download_assets=False,
        )

        self.assertEqual(result.manifest["status"], "partial")
        self.assertEqual(
            result.manifest["streams"]["messages_300"]["status"],
            "truncated_by_limit",
        )
        self.assertEqual(
            result.manifest["streams"]["messages_300"]["terminal_reason"],
            "truncated_by_limit",
        )
        raw_page = result.run_root / "pages/messages_300/000001.json"
        stored = json.loads(raw_page.read_text())
        self.assertEqual(stored["payload"][0]["id"], "30")
        self.assertEqual(stored["payload"][0]["unknown"], "raw")
        self.assertEqual(
            hashlib.sha256(raw_page.read_bytes()).hexdigest(),
            json.loads((result.run_root / "checkpoint.json").read_text())["streams"]
            ["messages_300"]["page_hashes"][0],
        )
        transport.assert_exhausted(self)


class DiscordMediaResolutionRecoveryTests(unittest.TestCase):
    _OFFICIAL_URL = (
        "https://cdn.discordapp.com/attachments/300/400/asset.png?sig=synthetic"
    )

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_directory.name)
        self.snapshot = _snapshot(_target("300", kind="GUILD_VOICE (2)"))

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def _seed_attachment_run(
        self,
        run_id: str,
        *,
        url: str = _OFFICIAL_URL,
        outcome: object | None = None,
        proxy_url: str | None = None,
        proxy_outcome: object | None = None,
        allow_rfc2544_fake_ip: bool = True,
        declared_size: int = 4,
    ) -> tuple[Path, Path]:
        transport = _FixtureTransport(
            allow_rfc2544_fake_ip=allow_rfc2544_fake_ip
        )
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "attachments": [
                        {
                            "id": "400",
                            "filename": "asset.png",
                            "url": url,
                            "content_type": "image/png",
                            "size": declared_size,
                            **(
                                {"proxy_url": proxy_url}
                                if proxy_url is not None
                                else {}
                            ),
                        }
                    ],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        if outcome is not None:
            transport.add_media(url, outcome)
        if proxy_url is not None and proxy_outcome is not None:
            transport.add_media(proxy_url, proxy_outcome)
        result = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
            allow_rfc2544_fake_ip=allow_rfc2544_fake_ip,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id=run_id,
            download_assets=True,
        )
        transport.assert_exhausted(self)
        record_path = next((result.run_root / "asset-records").glob("*.json"))
        return result.run_root, record_path

    def _convert_to_legacy_zero_complete(
        self,
        run_root: Path,
        record_path: Path,
    ) -> tuple[dict[str, Any], str]:
        record = self._read_record(record_path)
        empty_sha = hashlib.sha256(b"").hexdigest()
        empty_relative = (
            f"assets/sha256/{empty_sha[:2]}/{empty_sha}.bin"
        )
        empty_blob = run_root / empty_relative
        empty_blob.parent.mkdir(parents=True, exist_ok=True)
        empty_blob.write_bytes(b"")
        metadata = record["declared_metadata"]
        metadata["size"] = 0
        record["identity_metadata"]["size"] = 0
        for observation in record["observations"]:
            observation["metadata"]["size"] = 0
        source_attempt = record["attempt_history"][-1]
        source_attempt.update(
            {
                "status": "complete",
                "terminal_reason": "downloaded",
                "http_content_type": "image/png",
                "http_content_length": 0,
                "actual_bytes": 0,
                "sha256": empty_sha,
                "blob_path": empty_relative,
            }
        )
        record["attempt_history"] = [source_attempt]
        for field in (
            "url",
            "status",
            "terminal_reason",
            "http_content_type",
            "http_content_length",
            "actual_bytes",
            "sha256",
            "blob_path",
        ):
            record[field] = deepcopy(source_attempt[field])
        record.pop("failure_detail", None)
        record["schema_version"] = 3
        self._persist_record(run_root, record_path, record)
        return deepcopy(source_attempt), empty_relative

    def _convert_to_legacy_icon_record(
        self,
        run_root: Path,
        record_path: Path,
    ) -> dict[str, Any]:
        record = self._read_record(record_path)
        record["schema_version"] = 3
        record.pop("producer_migration", None)
        record["identity_metadata"] = (
            discord_collector_module._without_url_metadata(
                record["declared_metadata"]
            )
        )
        for observation in record["observations"]:
            observation["proxy_url"] = observation["metadata"].get("proxy_url")
        self._persist_record(run_root, record_path, record)
        return record

    def test_new_author_icon_uses_v4_exact_icon_descriptor(self) -> None:
        direct_url = "https://cdn.example/author.png?sig=direct"
        proxy_url = "https://media.discordapp.net/external/author.png?sig=proxy"
        transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "embeds": [
                        {
                            "author": {
                                "name": "analyst",
                                "icon_url": direct_url,
                                "proxy_icon_url": proxy_url,
                            }
                        }
                    ],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(
            direct_url,
            _ByteStream([b"icon"], content_type="image/png", content_length=4),
        )

        result = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
            allow_rfc2544_fake_ip=True,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id="new-author-icon-v4",
            download_assets=True,
        )

        transport.assert_exhausted(self)
        record_path = next((result.run_root / "asset-records").glob("*.json"))
        record = self._read_record(record_path)
        self.assertEqual(record["schema_version"], 4)
        self.assertEqual(record["identity_metadata"], {"name": "analyst"})
        self.assertEqual(record["candidate_urls"], [direct_url, proxy_url])
        self.assertEqual(record["observations"][0]["url"], direct_url)
        self.assertEqual(record["observations"][0]["proxy_url"], proxy_url)

    def test_load_migrates_legacy_icon_without_network_or_attempt_changes(self) -> None:
        direct_url = "https://cdn.example/footer.png?sig=direct"
        proxy_url = "https://media.discordapp.net/external/footer.png?sig=proxy"
        initial = _FixtureTransport(allow_rfc2544_fake_ip=True)
        _add_inventory(initial, [{"id": "300", "type": 2, "name": "voice"}])
        initial.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "embeds": [
                        {
                            "footer": {
                                "text": "footer",
                                "icon_url": direct_url,
                                "proxy_icon_url": proxy_url,
                            }
                        }
                    ],
                }
            ],
            {"limit": 100},
        )
        initial.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        initial.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        initial.add_media(
            direct_url,
            _ByteStream([b"icon"], content_type="image/png", content_length=4),
        )
        result = DiscordEvidenceCollector(
            initial,
            byte_transport=initial,
            allow_rfc2544_fake_ip=True,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id="legacy-footer-icon-v3",
            download_assets=True,
        )
        initial.assert_exhausted(self)
        record_path = next((result.run_root / "asset-records").glob("*.json"))
        legacy = self._convert_to_legacy_icon_record(result.run_root, record_path)
        attempts_before = discord_collector_module._canonical_json_bytes(
            legacy["attempt_history"]
        )

        _, resumed = self._resume("legacy-footer-icon-v3")

        resumed.assert_exhausted(self)
        migrated = self._read_record(record_path)
        self.assertEqual(resumed.media_calls, [])
        self.assertEqual(migrated["schema_version"], 4)
        self.assertEqual(migrated["identity_metadata"], {"text": "footer"})
        self.assertEqual(
            discord_collector_module._canonical_json_bytes(
                migrated["attempt_history"]
            ),
            attempts_before,
        )
        self.assertFalse(migrated["producer_migration"]["network_attempted"])

    def test_load_reclassifies_legacy_zero_then_direct_retry_succeeds(self) -> None:
        proxy_url = "https://media.discordapp.net/external/empty.png?sig=proxy"
        run_root, record_path = self._seed_attachment_run(
            "legacy-zero-direct-success",
            outcome=_ByteStream(
                [b"data"], content_type="image/png", content_length=4
            ),
            proxy_url=proxy_url,
            declared_size=0,
        )
        source_attempt, empty_relative = self._convert_to_legacy_zero_complete(
            run_root,
            record_path,
        )

        result, resumed = self._resume(
            "legacy-zero-direct-success",
            media={
                self._OFFICIAL_URL: _ByteStream(
                    [b"fixed"], content_type="image/png", content_length=5
                )
            },
        )

        resumed.assert_exhausted(self)
        migrated = self._read_record(record_path)
        self.assertEqual(resumed.media_calls, [self._OFFICIAL_URL])
        self.assertEqual(migrated["status"], "captured_with_warning")
        self.assertEqual(migrated["attempt_history"][0], source_attempt)
        self.assertFalse(
            migrated["attempt_history"][1]["evidence_reclassification"][
                "network_attempted"
            ]
        )
        self.assertEqual(migrated["attempt_history"][-1]["actual_bytes"], 5)
        self.assertTrue((run_root / empty_relative).is_file())
        self.assertEqual(result.manifest["media"]["failed"], 0)
        audit = json.loads(
            (run_root / "media-recovery-audit.json").read_text()
        )
        self.assertEqual(
            audit["counts"]["legacy_zero_byte_reclassification_rows"],
            1,
        )
        reclassified_rows = [
            row
            for row in audit["items"]
            if row["evidence_reclassification"] is not None
        ]
        self.assertEqual(len(reclassified_rows), 1)
        self.assertFalse(reclassified_rows[0]["binary_captured"])
        self.assertEqual(
            reclassified_rows[0]["disposition"],
            "legacy_zero_byte_reclassified_not_binary",
        )

    def test_zero_reclassification_direct_empty_falls_back_to_proxy(self) -> None:
        proxy_url = "https://media.discordapp.net/external/empty-fallback.png"
        run_root, record_path = self._seed_attachment_run(
            "legacy-zero-proxy-success",
            outcome=_ByteStream(
                [b"data"], content_type="image/png", content_length=4
            ),
            proxy_url=proxy_url,
            declared_size=0,
        )
        source_attempt, empty_relative = self._convert_to_legacy_zero_complete(
            run_root,
            record_path,
        )

        result, resumed = self._resume(
            "legacy-zero-proxy-success",
            media={
                self._OFFICIAL_URL: _ByteStream(
                    [], content_type="image/png", content_length=0
                ),
                proxy_url: _ByteStream(
                    [b"fixed"], content_type="image/png", content_length=5
                ),
            },
        )

        resumed.assert_exhausted(self)
        record = self._read_record(record_path)
        self.assertEqual(
            resumed.media_calls,
            [self._OFFICIAL_URL, proxy_url],
        )
        self.assertEqual(record["status"], "captured_with_warning")
        self.assertEqual(record["url"], proxy_url)
        self.assertEqual(record["attempt_history"][0], source_attempt)
        self.assertEqual(
            record["attempt_history"][2]["terminal_reason"],
            "download_failed_transient",
        )
        self.assertEqual(record["attempt_history"][-1]["actual_bytes"], 5)
        self.assertTrue((run_root / empty_relative).is_file())
        self.assertEqual(result.manifest["media"]["binary_captured"], 1)

    def test_zero_reclassification_both_empty_remains_audited_failure(self) -> None:
        proxy_url = "https://media.discordapp.net/external/both-empty.png"
        run_root, record_path = self._seed_attachment_run(
            "legacy-zero-both-empty",
            outcome=_ByteStream(
                [b"data"], content_type="image/png", content_length=4
            ),
            proxy_url=proxy_url,
            declared_size=0,
        )
        _, empty_relative = self._convert_to_legacy_zero_complete(
            run_root,
            record_path,
        )

        result, resumed = self._resume(
            "legacy-zero-both-empty",
            media={
                self._OFFICIAL_URL: _ByteStream(
                    [], content_type="image/png", content_length=0
                ),
                proxy_url: _ByteStream(
                    [], content_type="image/png", content_length=0
                ),
            },
        )

        resumed.assert_exhausted(self)
        record = self._read_record(record_path)
        self.assertEqual(
            resumed.media_calls,
            [self._OFFICIAL_URL, proxy_url],
        )
        self.assertEqual(record["status"], "failed")
        self.assertEqual(
            record["terminal_reason"],
            "download_failed_transient",
        )
        self.assertIsNone(record["sha256"])
        self.assertIsNone(record["blob_path"])
        self.assertTrue((run_root / empty_relative).is_file())
        self.assertEqual(result.manifest["media"]["failed"], 1)

    def test_zero_reclassification_atomic_commit_crash_windows_replay_once(
        self,
    ) -> None:
        stages = (
            "before_prepare",
            "after_prepare",
            "after_record_replace",
            "after_finish",
        )
        for stage in stages:
            with self.subTest(stage=stage):
                run_id = f"legacy-zero-crash-{stage}"
                proxy_url = (
                    "https://media.discordapp.net/external/"
                    f"zero-crash-{stage}.png"
                )
                run_root, record_path = self._seed_attachment_run(
                    run_id,
                    outcome=_ByteStream(
                        [b"data"], content_type="image/png", content_length=4
                    ),
                    proxy_url=proxy_url,
                    declared_size=0,
                )
                _, empty_relative = self._convert_to_legacy_zero_complete(
                    run_root,
                    record_path,
                )
                transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
                collector = DiscordEvidenceCollector(
                    transport,
                    byte_transport=transport,
                    allow_rfc2544_fake_ip=True,
                )
                original_commit = collector._commit_asset_record
                crashed = False

                def crash_at_compatibility_commit(record: dict[str, Any]) -> None:
                    nonlocal crashed
                    attempts = record.get("attempt_history", [])
                    is_compatibility_commit = any(
                        isinstance(attempt, dict)
                        and "evidence_reclassification" in attempt
                        for attempt in attempts
                    )
                    if crashed or not is_compatibility_commit:
                        original_commit(record)
                        return
                    crashed = True
                    if stage == "before_prepare":
                        raise KeyboardInterrupt("synthetic pre-prepare crash")
                    record_name = (
                        hashlib.sha256(
                            record["logical_key"].encode("utf-8")
                        ).hexdigest()
                        + ".json"
                    )
                    content = discord_collector_module._canonical_json_bytes(record)
                    digest = hashlib.sha256(content).hexdigest()
                    self.assertTrue(
                        collector._asset_ledger.prepare_commit(
                            record["logical_key"],
                            record_name,
                            digest,
                        )
                    )
                    if stage == "after_prepare":
                        raise KeyboardInterrupt("synthetic post-prepare crash")
                    discord_collector_module._atomic_write_bytes(
                        run_root / "asset-records" / record_name,
                        content,
                    )
                    if stage == "after_record_replace":
                        raise KeyboardInterrupt("synthetic post-replace crash")
                    collector._asset_ledger.finish_commit(
                        record["logical_key"], digest
                    )
                    raise KeyboardInterrupt("synthetic post-finish crash")

                with patch.object(
                    collector,
                    "_commit_asset_record",
                    side_effect=crash_at_compatibility_commit,
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        collector.collect(
                            workspace=self.workspace,
                            output_dir="evidence",
                            targets=self.snapshot,
                            run_id=run_id,
                            download_assets=True,
                        )
                self.assertTrue(crashed)
                self.assertEqual(transport.media_calls, [])
                crashed_record = self._read_record(record_path)
                expected_attempts = (
                    1 if stage in {"before_prepare", "after_prepare"} else 2
                )
                self.assertEqual(
                    len(crashed_record["attempt_history"]),
                    expected_attempts,
                )
                ledger_rows, _ = self._asset_ledger_state(run_root)
                self.assertEqual(len(ledger_rows), 1)
                pending_sha256 = ledger_rows[0][3]
                self.assertEqual(
                    pending_sha256 is not None,
                    stage in {"after_prepare", "after_record_replace"},
                )

                _, resumed = self._resume(
                    run_id,
                    media={
                        self._OFFICIAL_URL: _ByteStream(
                            [b"fixed"],
                            content_type="image/png",
                            content_length=5,
                        )
                    },
                )

                resumed.assert_exhausted(self)
                replayed = self._read_record(record_path)
                markers = [
                    attempt["evidence_reclassification"]
                    for attempt in replayed["attempt_history"]
                    if "evidence_reclassification" in attempt
                ]
                self.assertEqual(len(markers), 1)
                self.assertFalse(markers[0]["network_attempted"])
                self.assertEqual(replayed["status"], "captured_with_warning")
                self.assertEqual(resumed.media_calls, [self._OFFICIAL_URL])
                self.assertTrue((run_root / empty_relative).is_file())

    def test_zero_reclassification_rejects_unsafe_or_inexact_blob_evidence_before_network(
        self,
    ) -> None:
        cases = (
            "wrong_sha",
            "missing_blob",
            "nonzero_blob",
            "symlink_blob",
            "escaping_path",
            "declared_size",
            "current_tail",
        )
        for case in cases:
            with self.subTest(case=case):
                proxy_url = (
                    f"https://media.discordapp.net/external/{case}.png"
                )
                run_id = f"legacy-zero-reject-{case}"
                run_root, record_path = self._seed_attachment_run(
                    run_id,
                    outcome=_ByteStream(
                        [b"data"], content_type="image/png", content_length=4
                    ),
                    proxy_url=proxy_url,
                    declared_size=0,
                )
                _, empty_relative = self._convert_to_legacy_zero_complete(
                    run_root,
                    record_path,
                )
                record = self._read_record(record_path)
                empty_blob = run_root / empty_relative
                if case == "wrong_sha":
                    record["sha256"] = "0" * 64
                    record["attempt_history"][0]["sha256"] = "0" * 64
                elif case == "missing_blob":
                    empty_blob.unlink()
                elif case == "nonzero_blob":
                    empty_blob.write_bytes(b"x")
                elif case == "symlink_blob":
                    target = run_root / "assets/symlink-target"
                    target.write_bytes(b"")
                    empty_blob.unlink()
                    empty_blob.symlink_to(target)
                elif case == "escaping_path":
                    record["blob_path"] = "../escape.bin"
                    record["attempt_history"][0]["blob_path"] = "../escape.bin"
                elif case == "declared_size":
                    record["declared_metadata"]["size"] = 1
                    record["identity_metadata"]["size"] = 1
                    record["observations"][0]["metadata"]["size"] = 1
                else:
                    record["terminal_reason"] = "mime_mismatch"
                self._persist_record(run_root, record_path, record)

                transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
                collector = DiscordEvidenceCollector(
                    transport,
                    byte_transport=transport,
                    allow_rfc2544_fake_ip=True,
                )
                with self.assertRaises(ValueError):
                    collector.collect(
                        workspace=self.workspace,
                        output_dir="evidence",
                        targets=self.snapshot,
                        run_id=run_id,
                        download_assets=True,
                    )
                self.assertEqual(transport.media_calls, [])

    def test_v2_record_without_candidate_ledger_migrates_to_current_url(self) -> None:
        run_root, record_path = self._seed_attachment_run(
            "v2-missing-candidate-ledger",
            outcome=_ByteStream(
                [b"data"], content_type="image/png", content_length=4
            ),
        )
        record = self._read_record(record_path)
        current_url = record["url"]
        record["schema_version"] = 2
        record.pop("candidate_urls")
        record["identity_metadata"] = {
            "id": record["declared_metadata"]["id"],
            "filename": record["declared_metadata"]["filename"],
            "size": record["declared_metadata"]["size"],
            "content_type": record["declared_content_type"],
        }
        self._persist_record(run_root, record_path, record)

        result, transport = self._resume("v2-missing-candidate-ledger")

        transport.assert_exhausted(self)
        migrated = self._read_record(record_path)
        self.assertEqual(result.manifest["media"]["complete"], 1)
        self.assertEqual(migrated["schema_version"], 4)
        self.assertEqual(migrated["candidate_urls"], [current_url])
        self.assertNotIn("filename", migrated["identity_metadata"])

    def test_unaffected_schema3_attachment_is_validated_without_rewrite(self) -> None:
        run_root, record_path = self._seed_attachment_run(
            "schema3-positive-no-rewrite",
            outcome=_ByteStream(
                [b"data"], content_type="image/png", content_length=4
            ),
        )
        record = self._read_record(record_path)
        record["schema_version"] = 3
        self._persist_record(run_root, record_path, record)
        before_bytes = record_path.read_bytes()
        before_ledger = self._asset_ledger_state(run_root)
        before_generation = dict(before_ledger[1])["records_generation"]

        result, transport = self._resume("schema3-positive-no-rewrite")

        transport.assert_exhausted(self)
        self.assertEqual(transport.media_calls, [])
        self.assertEqual(record_path.read_bytes(), before_bytes)
        after_ledger = self._asset_ledger_state(run_root)
        self.assertEqual(after_ledger[0], before_ledger[0])
        self.assertEqual(
            dict(after_ledger[1])["records_generation"],
            before_generation,
        )
        self.assertEqual(result.manifest["media"]["complete"], 1)

    def _seed_legacy_failure(
        self,
        run_id: str,
        *,
        url: str = _OFFICIAL_URL,
        allow_rfc2544_fake_ip: bool = True,
    ) -> tuple[Path, Path]:
        run_root, record_path = self._seed_attachment_run(
            run_id,
            url=url,
            outcome=DiscordMediaSecurityError("synthetic legacy rejection"),
            allow_rfc2544_fake_ip=allow_rfc2544_fake_ip,
        )
        record = self._read_record(record_path)
        record["attempt_history"][-1].pop("security_rejection", None)
        self._persist_record(run_root, record_path, record)
        return run_root, record_path

    def _seed_warning_before_pins(
        self,
        run_id: str,
        *,
        old_url: str,
    ) -> tuple[Path, Path, dict[str, object]]:
        attachment: dict[str, object] = {
            "id": "400",
            "filename": "asset.png",
            "size": 5,
            "content_type": "image/png",
            "url": old_url,
        }
        initial = _FixtureTransport(allow_rfc2544_fake_ip=True)
        _add_inventory(initial, [{"id": "300", "type": 2, "name": "voice"}])
        initial.add_json(
            "/channels/300/messages",
            [{"id": "30", "attachments": [attachment]}],
            {"limit": 100},
        )
        initial.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        initial.add_json(
            "/channels/300/messages/pins",
            KeyboardInterrupt(),
            {"limit": 50},
        )
        initial.add_media(
            old_url,
            _ByteStream([b"data"], content_type="image/png", content_length=4),
        )
        with self.assertRaises(KeyboardInterrupt):
            DiscordEvidenceCollector(
                initial,
                byte_transport=initial,
                allow_rfc2544_fake_ip=True,
            ).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=self.snapshot,
                run_id=run_id,
                download_assets=True,
            )
        initial.assert_exhausted(self)
        run_root = self.workspace / "evidence/runs" / run_id
        record_path = next((run_root / "asset-records").glob("*.json"))
        return run_root, record_path, attachment

    def _resume_warning_with_fresh_candidate(
        self,
        run_id: str,
        *,
        old_url: str,
        fresh_url: str,
        mode: str,
        outcome: object,
    ) -> tuple[Path, Path, _FixtureTransport]:
        run_root, record_path, attachment = self._seed_warning_before_pins(
            run_id,
            old_url=old_url,
        )
        if mode == "append":
            refreshed = {**attachment, "proxy_url": fresh_url}
        elif mode == "replacement":
            refreshed = {**attachment, "url": fresh_url}
        else:
            raise AssertionError(f"unknown candidate refresh mode: {mode}")
        transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
        transport.add_json(
            "/channels/300/messages/pins",
            {
                "items": [
                    {
                        "pinned_at": "2026-07-20T00:00:00+00:00",
                        "message": {
                            "id": "30",
                            "attachments": [refreshed],
                        },
                    }
                ],
                "has_more": False,
            },
            {"limit": 50},
        )
        transport.add_media(fresh_url, outcome)
        DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
            allow_rfc2544_fake_ip=True,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id=run_id,
            download_assets=True,
        )
        transport.assert_exhausted(self)
        return run_root, record_path, transport

    def _seed_proxy_failure(
        self,
        run_id: str,
    ) -> tuple[Path, Path, str, str]:
        direct_url = "https://origin.example/watch?sig=synthetic-direct"
        proxy_url = (
            "https://media.discordapp.net/external/synthetic-proxy.png"
            "?sig=synthetic-proxy"
        )
        transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "embeds": [
                        {
                            "image": {
                                "url": direct_url,
                                "proxy_url": proxy_url,
                            }
                        }
                    ],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(
            direct_url,
            DiscordMediaSecurityError("synthetic direct rejection"),
        )
        transport.add_media(
            proxy_url,
            DiscordMediaSecurityError("synthetic proxy rejection"),
        )
        result = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
            allow_rfc2544_fake_ip=True,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id=run_id,
            download_assets=True,
        )
        transport.assert_exhausted(self)
        record_path = next((result.run_root / "asset-records").glob("*.json"))
        record = self._read_record(record_path)
        for attempt in record["attempt_history"]:
            attempt.pop("security_rejection", None)
        self._persist_record(result.run_root, record_path, record)
        return result.run_root, record_path, direct_url, proxy_url

    def _resume(
        self,
        run_id: str,
        *,
        media: Mapping[str, object] | None = None,
        allow_rfc2544_fake_ip: bool = True,
        collector: DiscordEvidenceCollector | None = None,
    ) -> tuple[object, _FixtureTransport]:
        transport = _FixtureTransport(
            allow_rfc2544_fake_ip=allow_rfc2544_fake_ip
        )
        for url, outcome in (media or {}).items():
            transport.add_media(url, outcome)
        active_collector = collector or DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
            allow_rfc2544_fake_ip=allow_rfc2544_fake_ip,
        )
        result = active_collector.collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id=run_id,
            download_assets=True,
        )
        return result, transport

    @staticmethod
    def _read_record(record_path: Path) -> dict[str, Any]:
        record = json.loads(record_path.read_text())
        assert isinstance(record, dict)
        return record

    @staticmethod
    def _persist_record(run_root: Path, record_path: Path, record: dict[str, Any]) -> None:
        content = discord_collector_module._canonical_json_bytes(record)
        record_path.write_bytes(content)
        with closing(sqlite3.connect(run_root / "asset-ledger.sqlite3")) as connection:
            connection.execute(
                "UPDATE asset_records SET committed_sha256 = ?, pending_sha256 = NULL "
                "WHERE logical_key = ?",
                (hashlib.sha256(content).hexdigest(), record["logical_key"]),
            )
            connection.execute(
                "UPDATE asset_metadata SET value = CAST(value AS INTEGER) + 1 "
                "WHERE key = 'records_generation'"
            )
            connection.execute(
                "UPDATE asset_metadata SET value = '-1' "
                "WHERE key = 'index_generation'"
            )
            connection.execute(
                "UPDATE asset_metadata SET value = '' "
                "WHERE key = 'asset_index_sha256'"
            )
            connection.commit()

    @staticmethod
    def _asset_ledger_state(
        run_root: Path,
    ) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
        with closing(sqlite3.connect(run_root / "asset-ledger.sqlite3")) as connection:
            records = connection.execute(
                "SELECT logical_key, record_name, committed_sha256, pending_sha256 "
                "FROM asset_records ORDER BY logical_key"
            ).fetchall()
            metadata = connection.execute(
                "SELECT key, value FROM asset_metadata ORDER BY key"
            ).fetchall()
        return records, metadata

    def _seed_legacy_transient(
        self,
        run_id: str,
    ) -> tuple[Path, Path]:
        run_root, record_path = self._seed_legacy_failure(run_id)
        outcome = DiscordMediaResolutionError(
            DiscordMediaResolutionReason.EAI_AGAIN
        )
        result, transport = self._resume(
            run_id,
            media={self._OFFICIAL_URL: outcome},
        )
        self.assertEqual(result.manifest["media"]["failed"], 1)
        transport.assert_exhausted(self)
        return run_root, record_path

    def test_typed_resolution_outcomes_use_reason_code_and_request_policy(self) -> None:
        cases: tuple[tuple[str, BaseException, str, str], ...] = (
            (
                "eai-again",
                DiscordMediaResolutionError(
                    DiscordMediaResolutionReason.EAI_AGAIN
                ),
                "media_resolution_failed_transient",
                "resolver_eai_again",
            ),
            (
                "timeout",
                DiscordMediaResolutionError(
                    DiscordMediaResolutionReason.TIMEOUT
                ),
                "media_resolution_failed_transient",
                "resolver_timeout",
            ),
            (
                "name-not-found",
                DiscordMediaResolutionError(
                    DiscordMediaResolutionReason.NAME_NOT_FOUND
                ),
                "media_resolution_unresolved",
                "resolver_name_not_found",
            ),
            (
                "no-data",
                DiscordMediaResolutionError(
                    DiscordMediaResolutionReason.NO_DATA
                ),
                "media_resolution_unresolved",
                "resolver_no_data",
            ),
            (
                "empty-answer",
                DiscordMediaResolutionError(
                    DiscordMediaResolutionReason.EMPTY_ANSWER
                ),
                "media_resolution_unresolved",
                "resolver_empty_answer",
            ),
            (
                "os-error",
                DiscordMediaResolutionError(
                    DiscordMediaResolutionReason.OS_ERROR_UNCLASSIFIED
                ),
                "media_resolution_unresolved",
                "resolver_os_error_unclassified",
            ),
            (
                "invalid-answer",
                DiscordMediaResolutionInvalidAnswer(),
                "media_resolution_invalid_answer",
                "resolver_invalid_answer",
            ),
        )
        for suffix, error, expected_reason, expected_detail in cases:
            with self.subTest(reason_code=expected_detail):
                error.args = ("misleading unsafe private-network text",)
                _, record_path = self._seed_attachment_run(
                    f"typed-{suffix}",
                    outcome=error,
                    allow_rfc2544_fake_ip=False,
                )
                record = self._read_record(record_path)
                attempt = record["attempt_history"][0]
                self.assertEqual(record["terminal_reason"], expected_reason)
                self.assertEqual(attempt["terminal_reason"], expected_reason)
                self.assertEqual(attempt["failure_detail"], expected_detail)
                self.assertEqual(attempt["resolution_retry_sequence"], 1)
                self.assertIsNone(attempt["policy_inputs_sha256"])
                self.assertNotIn("misleading", record_path.read_text())

        _, opt_in_path = self._seed_attachment_run(
            "typed-opt-in-policy",
            outcome=DiscordMediaResolutionError(
                DiscordMediaResolutionReason.EAI_AGAIN
            ),
            allow_rfc2544_fake_ip=True,
        )
        opt_in_attempt = self._read_record(opt_in_path)["attempt_history"][0]
        self.assertEqual(
            opt_in_attempt["policy_inputs_sha256"],
            rfc2544_fake_ip_media_policy_descriptor()["inputs_sha256"],
        )

    def test_candidate_local_retry_can_recover_behind_later_http_failure(self) -> None:
        proxy_url = "https://media.discordapp.net/external/candidate-local.png"
        _, record_path = self._seed_attachment_run(
            "candidate-local-retry",
            outcome=DiscordMediaResolutionError(
                DiscordMediaResolutionReason.EAI_AGAIN
            ),
            proxy_url=proxy_url,
            proxy_outcome=DiscordAPIError("missing", status_code=404),
        )

        _, transport = self._resume(
            "candidate-local-retry",
            media={
                self._OFFICIAL_URL: _ByteStream(
                    [b"data"], content_type="image/png", content_length=4
                )
            },
        )
        transport.assert_exhausted(self)
        record = self._read_record(record_path)
        self.assertEqual(transport.media_calls, [self._OFFICIAL_URL])
        self.assertEqual(record["status"], "complete")
        typed_a = [
            attempt
            for attempt in record["attempt_history"]
            if attempt["url"] == self._OFFICIAL_URL
            and "resolution_retry_sequence" in attempt
        ]
        self.assertEqual(
            [attempt["resolution_retry_sequence"] for attempt in typed_a],
            [1, 2],
        )

    def test_global_pending_candidate_replays_before_earlier_retryable_candidate(
        self,
    ) -> None:
        for prefix, direct_outcome, pending_outcome, generic_tail, partial_tail in (
            (
                "typed",
                DiscordMediaResolutionError(
                    DiscordMediaResolutionReason.EAI_AGAIN
                ),
                DiscordMediaResolutionError(
                    DiscordMediaResolutionReason.EAI_AGAIN
                ),
                False,
                False,
            ),
            (
                "generic-tail",
                DiscordMediaResolutionError(
                    DiscordMediaResolutionReason.EAI_AGAIN
                ),
                DiscordAPIError("missing", status_code=404),
                True,
                False,
            ),
            (
                "generic-partial-tail",
                DiscordMediaResolutionError(
                    DiscordMediaResolutionReason.EAI_AGAIN
                ),
                DiscordAPIError("missing", status_code=404),
                True,
                True,
            ),
        ):
            with self.subTest(prefix=prefix):
                proxy_url = (
                    "https://media.discordapp.net/external/"
                    f"pending-{prefix}.png"
                )
                run_id = f"pending-priority-{prefix}"
                run_root, record_path = self._seed_attachment_run(
                    run_id,
                    outcome=direct_outcome,
                    proxy_url=proxy_url,
                    proxy_outcome=pending_outcome,
                )
                record = self._read_record(record_path)
                pending = record["attempt_history"][-1]
                if generic_tail:
                    for field in (
                        "policy_inputs_sha256",
                        "resolution_retry_sequence",
                        "retry_trigger",
                        "retry_of_attempt_number",
                        "failure_detail",
                    ):
                        pending.pop(field, None)
                pending.update(
                    {
                        "status": "interrupted",
                        "terminal_reason": "interrupted",
                        "failure_detail": None,
                        "http_content_type": None,
                        "http_content_length": None,
                        "actual_bytes": 0,
                        "sha256": None,
                        "blob_path": None,
                    }
                )
                if partial_tail:
                    pending.update(
                        {
                            "http_content_type": "image/png",
                            "http_content_length": 4,
                            "actual_bytes": 2,
                        }
                    )
                record.update(
                    {
                        "url": proxy_url,
                        "status": "in_progress",
                        "terminal_reason": "interrupted",
                        "http_content_type": None,
                        "http_content_length": None,
                        "actual_bytes": 0,
                        "sha256": None,
                        "blob_path": None,
                    }
                )
                if partial_tail:
                    record.update(
                        {
                            "http_content_type": "image/png",
                            "http_content_length": 4,
                            "actual_bytes": 2,
                        }
                    )
                self._persist_record(run_root, record_path, record)

                _, transport = self._resume(
                    run_id,
                    media={
                        proxy_url: _ByteStream(
                            [b"data"], content_type="image/png", content_length=4
                        )
                    },
                )
                transport.assert_exhausted(self)
                replayed = self._read_record(record_path)
                self.assertEqual(transport.media_calls, [proxy_url])
                self.assertEqual(replayed["status"], "complete")
                self.assertEqual(
                    len(replayed["attempt_history"]),
                    3 if partial_tail else 2,
                )
                self.assertEqual(
                    replayed["attempt_history"][-1]["url"],
                    proxy_url,
                )
                self.assertEqual(
                    replayed["attempt_history"][-1]["status"],
                    "complete",
                )
                if generic_tail:
                    self.assertNotIn(
                        "resolution_retry_sequence",
                        replayed["attempt_history"][-1],
                    )
                if partial_tail:
                    historical_partial = replayed["attempt_history"][-2]
                    self.assertEqual(historical_partial["status"], "failed")
                    self.assertEqual(
                        historical_partial["terminal_reason"],
                        "interrupted",
                    )
                    self.assertEqual(historical_partial["actual_bytes"], 2)

    def test_generic_pending_resolver_outcome_upgrades_same_attempt_in_place(
        self,
    ) -> None:
        cases: tuple[
            tuple[str, BaseException, str, str | None, bool], ...
        ] = (
            (
                "eai-again",
                DiscordMediaResolutionError(
                    DiscordMediaResolutionReason.EAI_AGAIN
                ),
                "media_resolution_failed_transient",
                "resolver_eai_again",
                True,
            ),
            (
                "name-not-found",
                DiscordMediaResolutionError(
                    DiscordMediaResolutionReason.NAME_NOT_FOUND
                ),
                "media_resolution_unresolved",
                "resolver_name_not_found",
                True,
            ),
            (
                "invalid-answer",
                DiscordMediaResolutionInvalidAnswer(),
                "media_resolution_invalid_answer",
                "resolver_invalid_answer",
                True,
            ),
            (
                "http",
                DiscordAPIError("missing", status_code=404),
                "download_http_404",
                None,
                False,
            ),
        )
        for suffix, outcome, reason, detail, becomes_typed in cases:
            with self.subTest(suffix=suffix):
                proxy_url = (
                    "https://media.discordapp.net/external/"
                    f"generic-upgrade-{suffix}.png"
                )
                run_id = f"generic-upgrade-{suffix}"
                run_root, record_path = self._seed_attachment_run(
                    run_id,
                    outcome=DiscordAPIError("missing", status_code=404),
                    proxy_url=proxy_url,
                    proxy_outcome=DiscordAPIError(
                        "missing", status_code=404
                    ),
                )
                record = self._read_record(record_path)
                pending = record["attempt_history"][-1]
                for field in (
                    "policy_inputs_sha256",
                    "resolution_retry_sequence",
                    "retry_trigger",
                    "retry_of_attempt_number",
                    "failure_detail",
                ):
                    pending.pop(field, None)
                pending.update(
                    {
                        "status": "interrupted",
                        "terminal_reason": "interrupted",
                        "failure_detail": None,
                        "http_content_type": None,
                        "http_content_length": None,
                        "actual_bytes": 0,
                        "sha256": None,
                        "blob_path": None,
                    }
                )
                record.update(
                    {
                        "url": proxy_url,
                        "status": "in_progress",
                        "terminal_reason": "interrupted",
                        "failure_detail": None,
                        "http_content_type": None,
                        "http_content_length": None,
                        "actual_bytes": 0,
                        "sha256": None,
                        "blob_path": None,
                    }
                )
                original_attempt_count = len(record["attempt_history"])
                self._persist_record(run_root, record_path, record)

                _, transport = self._resume(
                    run_id,
                    media={proxy_url: outcome},
                )
                transport.assert_exhausted(self)
                replayed = self._read_record(record_path)
                self.assertEqual(transport.media_calls, [proxy_url])
                self.assertEqual(
                    len(replayed["attempt_history"]),
                    original_attempt_count,
                )
                attempt = replayed["attempt_history"][-1]
                self.assertEqual(attempt["url"], proxy_url)
                self.assertEqual(attempt["status"], "failed")
                self.assertEqual(attempt["terminal_reason"], reason)
                self.assertEqual(attempt["failure_detail"], detail)
                if becomes_typed:
                    self.assertEqual(
                        attempt["resolution_retry_sequence"], 1
                    )
                    self.assertIn("policy_inputs_sha256", attempt)
                    self.assertNotIn("retry_trigger", attempt)
                    self.assertNotIn("retry_of_attempt_number", attempt)
                else:
                    self.assertNotIn(
                        "resolution_retry_sequence", attempt
                    )
                    self.assertNotIn("policy_inputs_sha256", attempt)

    def test_typed_retry_priority_discards_stale_legacy_marker_plan(self) -> None:
        proxy_url = "https://media.discordapp.net/external/legacy-tail.png"
        run_root, record_path = self._seed_attachment_run(
            "typed-before-legacy",
            outcome=DiscordMediaResolutionError(
                DiscordMediaResolutionReason.EAI_AGAIN
            ),
            proxy_url=proxy_url,
            proxy_outcome=DiscordMediaSecurityError("synthetic legacy rejection"),
        )
        record = self._read_record(record_path)
        record["attempt_history"][-1].pop("security_rejection", None)
        self._persist_record(run_root, record_path, record)

        _, transport = self._resume(
            "typed-before-legacy",
            media={
                self._OFFICIAL_URL: _ByteStream(
                    [b"data"], content_type="image/png", content_length=4
                )
            },
        )
        transport.assert_exhausted(self)
        recovered = self._read_record(record_path)
        self.assertEqual(transport.media_calls, [self._OFFICIAL_URL])
        self.assertEqual(recovered["status"], "complete")
        self.assertFalse(
            any(
                attempt.get("url") == proxy_url
                and attempt.get("retry_trigger") == LEGACY_RETRY_TRIGGER
                for attempt in recovered["attempt_history"]
            )
        )

    def test_pending_candidate_refresh_without_bytes_finalizes_old_marker_first(
        self,
    ) -> None:
        run_root, record_path = self._seed_attachment_run(
            "pending-refresh-no-bytes",
            outcome=DiscordMediaResolutionError(
                DiscordMediaResolutionReason.EAI_AGAIN
            ),
        )
        record = self._read_record(record_path)
        pending = record["attempt_history"][-1]
        pending.update(
            {
                "status": "interrupted",
                "terminal_reason": "interrupted",
                "failure_detail": None,
            }
        )
        record.update(
            {
                "status": "in_progress",
                "terminal_reason": "interrupted",
            }
        )
        request_path = run_root / "request.json"
        request_content = request_path.read_bytes()
        context = media_resolution_context(
            json.loads(request_content),
            hashlib.sha256(request_content).hexdigest(),
        )
        validate_resolution_attempt_history(record, context=context)

        transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
        collector = DiscordEvidenceCollector(
            transport,
            allow_rfc2544_fake_ip=True,
        )
        collector._resolution_context = context
        collector._asset_records = {record["logical_key"]: record}
        collector._attempted_asset_urls = set()
        collector._download_assets = True
        refreshed_url = "https://cdn.discordapp.com/attachments/refreshed.png"
        refreshed_metadata = deepcopy(record["declared_metadata"])
        refreshed_metadata["url"] = refreshed_url
        candidate = {
            "logical_key": record["logical_key"],
            "kind": record["kind"],
            "field": record["field"],
            "url": refreshed_url,
            "candidate_urls": [refreshed_url],
            "declared_metadata": refreshed_metadata,
            "declared_content_type": record["declared_content_type"],
            "identity_metadata": deepcopy(record["identity_metadata"]),
        }
        snapshots: list[dict[str, Any]] = []

        def capture(current: dict[str, Any]) -> None:
            validate_resolution_attempt_history(current, context=context)
            snapshots.append(deepcopy(current))

        with patch.object(collector, "_write_asset_record", side_effect=capture):
            collector._collect_asset(
                candidate,
                {"message_id": "30", "channel_id": "300", "stream": "pins_300"},
            )

        self.assertTrue(snapshots)
        self.assertEqual(transport.media_calls, [])
        self.assertEqual(record["candidate_urls"], [refreshed_url])
        self.assertEqual(record["url"], refreshed_url)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["terminal_reason"], "byte_transport_unavailable")
        self.assertEqual(len(record["attempt_history"]), 1)
        self.assertEqual(
            record["attempt_history"][0]["terminal_reason"],
            "byte_transport_unavailable",
        )
        self.assertEqual(
            record["attempt_history"][0]["resolution_retry_sequence"],
            1,
        )

    def test_pending_replay_nonabsorbing_outcomes_apply_refreshed_candidate(
        self,
    ) -> None:
        cases = (
            (
                "reference",
                "video",
                "https://www.youtube.com/embed/PendingRefresh",
                DiscordMediaSecurityError("synthetic player policy rejection"),
                "https://cdn.example/refreshed-video.mp4",
                _ByteStream(
                    [b"video"], content_type="video/mp4", content_length=5
                ),
                {},
            ),
            (
                "http",
                "attachment",
                "https://cdn.example/pending-http.png",
                DiscordAPIError("missing", status_code=404),
                "https://cdn.example/refreshed-http.png",
                _ByteStream(
                    [b"data"], content_type="image/png", content_length=4
                ),
                {
                    "id": "400",
                    "filename": "asset.png",
                    "size": 4,
                    "content_type": "image/png",
                },
            ),
            (
                "warning",
                "attachment",
                "https://cdn.example/pending-warning.png",
                _ByteStream(
                    [b"data"], content_type="image/png", content_length=4
                ),
                "https://cdn.example/refreshed-warning.png",
                _ByteStream(
                    [b"fixed"], content_type="image/png", content_length=5
                ),
                {
                    "id": "400",
                    "filename": "asset.png",
                    "size": 5,
                    "content_type": "image/png",
                },
            ),
        )
        for (
            suffix,
            field,
            pending_url,
            pending_outcome,
            refreshed_url,
            refreshed_outcome,
            stable_metadata,
        ) in cases:
            with self.subTest(outcome=suffix):
                run_id = f"pending-refresh-{suffix}"
                initial = _FixtureTransport(allow_rfc2544_fake_ip=True)
                _add_inventory(
                    initial,
                    [{"id": "300", "type": 2, "name": "voice"}],
                )
                initial_media = {**stable_metadata, "url": pending_url}
                initial_message = (
                    {"id": "30", "attachments": [initial_media]}
                    if field == "attachment"
                    else {"id": "30", "embeds": [{field: initial_media}]}
                )
                initial.add_json(
                    "/channels/300/messages",
                    [initial_message],
                    {"limit": 100},
                )
                initial.add_json(
                    "/channels/300/messages",
                    [],
                    {"limit": 100, "before": "30"},
                )
                initial.add_json(
                    "/channels/300/messages/pins",
                    KeyboardInterrupt(),
                    {"limit": 50},
                )
                initial.add_media(
                    pending_url,
                    DiscordMediaResolutionError(
                        DiscordMediaResolutionReason.EAI_AGAIN
                    ),
                )
                with self.assertRaises(KeyboardInterrupt):
                    DiscordEvidenceCollector(
                        initial,
                        byte_transport=initial,
                        allow_rfc2544_fake_ip=True,
                    ).collect(
                        workspace=self.workspace,
                        output_dir="evidence",
                        targets=self.snapshot,
                        run_id=run_id,
                        download_assets=True,
                    )
                initial.assert_exhausted(self)

                run_root = self.workspace / "evidence/runs" / run_id
                record_path = next((run_root / "asset-records").glob("*.json"))
                record = self._read_record(record_path)
                record["attempt_history"][-1].update(
                    {
                        "status": "interrupted",
                        "terminal_reason": "interrupted",
                        "failure_detail": None,
                        "http_content_type": None,
                        "http_content_length": None,
                        "actual_bytes": 0,
                        "sha256": None,
                        "blob_path": None,
                    }
                )
                record.update(
                    {
                        "status": "in_progress",
                        "terminal_reason": "interrupted",
                        "http_content_type": None,
                        "http_content_length": None,
                        "actual_bytes": 0,
                        "sha256": None,
                        "blob_path": None,
                    }
                )
                self._persist_record(run_root, record_path, record)

                refreshed_media = {**stable_metadata, "url": refreshed_url}
                refreshed_message = (
                    {"id": "30", "attachments": [refreshed_media]}
                    if field == "attachment"
                    else {"id": "30", "embeds": [{field: refreshed_media}]}
                )
                resumed = _FixtureTransport(allow_rfc2544_fake_ip=True)
                resumed.add_media(pending_url, pending_outcome)
                resumed.add_json(
                    "/channels/300/messages/pins",
                    {
                        "items": [
                            {
                                "pinned_at": "2026-07-20T00:00:00+00:00",
                                "message": refreshed_message,
                            }
                        ],
                        "has_more": False,
                    },
                    {"limit": 50},
                )
                resumed.add_media(refreshed_url, refreshed_outcome)
                result = DiscordEvidenceCollector(
                    resumed,
                    byte_transport=resumed,
                    allow_rfc2544_fake_ip=True,
                ).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=self.snapshot,
                    run_id=run_id,
                    download_assets=True,
                )

                resumed.assert_exhausted(self)
                recovered = self._read_record(record_path)
                self.assertEqual(resumed.media_calls, [pending_url, refreshed_url])
                self.assertEqual(result.manifest["media"]["complete"], 1)
                self.assertEqual(recovered["status"], "complete")
                self.assertEqual(recovered["url"], refreshed_url)
                self.assertEqual(recovered["candidate_urls"], [refreshed_url])
                self.assertEqual(len(recovered["attempt_history"]), 2)

    def test_pending_replay_absorbing_outcomes_ignore_refreshed_candidate(
        self,
    ) -> None:
        pending_url = "https://cdn.example/pending-absorbing.png"
        refreshed_url = "https://cdn.example/refreshed-must-not-run.png"
        for suffix, max_asset_bytes, expected_reason in (
            ("complete", 4, "downloaded"),
            ("hard", 3, "size_limit_exceeded"),
        ):
            with self.subTest(outcome=suffix):
                run_id = f"pending-absorbing-{suffix}"
                attachment = {
                    "id": "400",
                    "filename": "asset.png",
                    "size": 4,
                    "content_type": "image/png",
                    "url": pending_url,
                }
                initial = _FixtureTransport(allow_rfc2544_fake_ip=True)
                _add_inventory(
                    initial,
                    [{"id": "300", "type": 2, "name": "voice"}],
                )
                initial.add_json(
                    "/channels/300/messages",
                    [{"id": "30", "attachments": [attachment]}],
                    {"limit": 100},
                )
                initial.add_json(
                    "/channels/300/messages",
                    [],
                    {"limit": 100, "before": "30"},
                )
                initial.add_json(
                    "/channels/300/messages/pins",
                    KeyboardInterrupt(),
                    {"limit": 50},
                )
                initial.add_media(
                    pending_url,
                    DiscordMediaResolutionError(
                        DiscordMediaResolutionReason.EAI_AGAIN
                    ),
                )
                with self.assertRaises(KeyboardInterrupt):
                    DiscordEvidenceCollector(
                        initial,
                        byte_transport=initial,
                        max_asset_bytes=max_asset_bytes,
                        allow_rfc2544_fake_ip=True,
                    ).collect(
                        workspace=self.workspace,
                        output_dir="evidence",
                        targets=self.snapshot,
                        run_id=run_id,
                        download_assets=True,
                    )
                initial.assert_exhausted(self)

                run_root = self.workspace / "evidence/runs" / run_id
                record_path = next((run_root / "asset-records").glob("*.json"))
                record = self._read_record(record_path)
                record["attempt_history"][-1].update(
                    {
                        "status": "interrupted",
                        "terminal_reason": "interrupted",
                        "failure_detail": None,
                        "http_content_type": None,
                        "http_content_length": None,
                        "actual_bytes": 0,
                        "sha256": None,
                        "blob_path": None,
                    }
                )
                record.update(
                    {
                        "status": "in_progress",
                        "terminal_reason": "interrupted",
                        "http_content_type": None,
                        "http_content_length": None,
                        "actual_bytes": 0,
                        "sha256": None,
                        "blob_path": None,
                    }
                )
                self._persist_record(run_root, record_path, record)

                resumed = _FixtureTransport(allow_rfc2544_fake_ip=True)
                resumed.add_media(
                    pending_url,
                    _ByteStream(
                        [b"data"], content_type="image/png", content_length=4
                    ),
                )
                resumed.add_json(
                    "/channels/300/messages/pins",
                    {
                        "items": [
                            {
                                "pinned_at": "2026-07-20T00:00:00+00:00",
                                "message": {
                                    "id": "30",
                                    "attachments": [
                                        {**attachment, "url": refreshed_url}
                                    ],
                                },
                            }
                        ],
                        "has_more": False,
                    },
                    {"limit": 50},
                )
                result = DiscordEvidenceCollector(
                    resumed,
                    byte_transport=resumed,
                    max_asset_bytes=max_asset_bytes,
                    allow_rfc2544_fake_ip=True,
                ).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=self.snapshot,
                    run_id=run_id,
                    download_assets=True,
                )

                resumed.assert_exhausted(self)
                absorbed = self._read_record(record_path)
                self.assertEqual(resumed.media_calls, [pending_url])
                self.assertEqual(absorbed["url"], pending_url)
                self.assertEqual(absorbed["candidate_urls"], [pending_url])
                self.assertEqual(absorbed["terminal_reason"], expected_reason)
                if suffix == "complete":
                    self.assertEqual(result.manifest["media"]["complete"], 1)
                else:
                    self.assertEqual(result.manifest["media"]["failed"], 1)

    def test_warning_baseline_tries_fresh_candidate_then_restores_on_failure(
        self,
    ) -> None:
        old_url = "https://cdn.example/warning-baseline-old.png"
        fresh_url = "https://media.discordapp.net/external/warning-fresh.png"
        for suffix, fresh_outcome, expected_status, expected_url in (
            (
                "complete",
                _ByteStream(
                    [b"fixed"], content_type="image/png", content_length=5
                ),
                "complete",
                fresh_url,
            ),
            (
                "http-failure",
                DiscordAPIError("missing", status_code=404),
                "captured_with_warning",
                old_url,
            ),
        ):
            with self.subTest(fresh=suffix):
                run_id = f"warning-baseline-{suffix}"
                attachment = {
                    "id": "400",
                    "filename": "asset.png",
                    "size": 5,
                    "content_type": "image/png",
                    "url": old_url,
                }
                initial = _FixtureTransport(allow_rfc2544_fake_ip=True)
                _add_inventory(
                    initial,
                    [{"id": "300", "type": 2, "name": "voice"}],
                )
                initial.add_json(
                    "/channels/300/messages",
                    [{"id": "30", "attachments": [attachment]}],
                    {"limit": 100},
                )
                initial.add_json(
                    "/channels/300/messages",
                    [],
                    {"limit": 100, "before": "30"},
                )
                initial.add_json(
                    "/channels/300/messages/pins",
                    KeyboardInterrupt(),
                    {"limit": 50},
                )
                initial.add_media(
                    old_url,
                    _ByteStream(
                        [b"data"], content_type="image/png", content_length=4
                    ),
                )
                with self.assertRaises(KeyboardInterrupt):
                    DiscordEvidenceCollector(
                        initial,
                        byte_transport=initial,
                        allow_rfc2544_fake_ip=True,
                    ).collect(
                        workspace=self.workspace,
                        output_dir="evidence",
                        targets=self.snapshot,
                        run_id=run_id,
                        download_assets=True,
                    )
                initial.assert_exhausted(self)

                resumed = _FixtureTransport(allow_rfc2544_fake_ip=True)
                resumed.add_json(
                    "/channels/300/messages/pins",
                    {
                        "items": [
                            {
                                "pinned_at": "2026-07-20T00:00:00+00:00",
                                "message": {
                                    "id": "30",
                                    "attachments": [
                                        {**attachment, "proxy_url": fresh_url}
                                    ],
                                },
                            }
                        ],
                        "has_more": False,
                    },
                    {"limit": 50},
                )
                resumed.add_media(fresh_url, fresh_outcome)
                result = DiscordEvidenceCollector(
                    resumed,
                    byte_transport=resumed,
                    allow_rfc2544_fake_ip=True,
                ).collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=self.snapshot,
                    run_id=run_id,
                    download_assets=True,
                )

                resumed.assert_exhausted(self)
                record_path = next(
                    (result.run_root / "asset-records").glob("*.json")
                )
                record = self._read_record(record_path)
                self.assertEqual(resumed.media_calls, [fresh_url])
                self.assertEqual(record["status"], expected_status)
                self.assertEqual(record["url"], expected_url)
                self.assertEqual(len(record["attempt_history"]), 2)
                if suffix == "http-failure":
                    self.assertEqual(
                        record["candidate_urls"], [old_url, fresh_url]
                    )
                    self.assertEqual(
                        record["attempt_history"][-1]["terminal_reason"],
                        "download_http_404",
                    )
                    self.assertEqual(result.manifest["media"]["failed"], 0)
                    self.assertEqual(
                        result.manifest["media"]["captured_with_warning"],
                        1,
                    )

    def test_covered_baseline_retries_typed_candidate_without_message_replay(
        self,
    ) -> None:
        for mode, fresh_url in (
            ("append", "https://media.discordapp.net/external/typed-append.png"),
            ("replacement", "https://cdn.example/typed-replacement.png"),
        ):
            with self.subTest(mode=mode):
                old_url = f"https://cdn.example/typed-baseline-{mode}.png"
                run_id = f"covered-typed-{mode}"
                _, record_path, first = self._resume_warning_with_fresh_candidate(
                    run_id,
                    old_url=old_url,
                    fresh_url=fresh_url,
                    mode=mode,
                    outcome=DiscordMediaResolutionError(
                        DiscordMediaResolutionReason.EAI_AGAIN
                    ),
                )
                self.assertEqual(first.media_calls, [fresh_url])
                baseline = self._read_record(record_path)
                self.assertEqual(baseline["status"], "captured_with_warning")
                self.assertEqual(baseline["url"], old_url)
                self.assertEqual(
                    baseline["candidate_urls"],
                    [old_url, fresh_url],
                )
                self.assertEqual(
                    baseline["attempt_history"][-1][
                        "resolution_retry_sequence"
                    ],
                    1,
                )

                _, second = self._resume(
                    run_id,
                    media={
                        fresh_url: _ByteStream(
                            [b"fixed"],
                            content_type="image/png",
                            content_length=5,
                        )
                    },
                )
                second.assert_exhausted(self)
                recovered = self._read_record(record_path)
                self.assertEqual(second.media_calls, [fresh_url])
                self.assertEqual(recovered["status"], "complete")
                self.assertEqual(recovered["url"], fresh_url)
                self.assertEqual(
                    [
                        attempt["resolution_retry_sequence"]
                        for attempt in recovered["attempt_history"]
                        if attempt.get("url") == fresh_url
                        and "resolution_retry_sequence" in attempt
                    ],
                    [1, 2],
                )

    def test_covered_baseline_retries_generic_candidate_without_message_replay(
        self,
    ) -> None:
        old_url = "https://cdn.example/generic-baseline.png"
        fresh_url = "https://media.discordapp.net/external/generic-fresh.png"
        run_id = "covered-generic-retry"
        _, record_path, first = self._resume_warning_with_fresh_candidate(
            run_id,
            old_url=old_url,
            fresh_url=fresh_url,
            mode="append",
            outcome=_ByteStream(
                [], content_type="image/png", content_length=0
            ),
        )
        self.assertEqual(first.media_calls, [fresh_url])
        baseline = self._read_record(record_path)
        self.assertEqual(baseline["url"], old_url)
        self.assertEqual(baseline["candidate_urls"], [old_url, fresh_url])
        self.assertEqual(
            baseline["attempt_history"][-1]["terminal_reason"],
            "download_failed_transient",
        )
        self.assertNotIn(
            "resolution_retry_sequence",
            baseline["attempt_history"][-1],
        )

        _, second = self._resume(
            run_id,
            media={
                fresh_url: _ByteStream(
                    [b"fixed"], content_type="image/png", content_length=5
                )
            },
        )
        second.assert_exhausted(self)
        recovered = self._read_record(record_path)
        self.assertEqual(second.media_calls, [fresh_url])
        self.assertEqual(recovered["status"], "complete")
        self.assertEqual(recovered["url"], fresh_url)

    def test_covered_baseline_keeps_typed_budget_until_exact_exhaustion(
        self,
    ) -> None:
        old_url = "https://cdn.example/budget-baseline.png"
        fresh_url = "https://media.discordapp.net/external/budget-fresh.png"
        run_id = "covered-typed-budget"
        _, record_path, _ = self._resume_warning_with_fresh_candidate(
            run_id,
            old_url=old_url,
            fresh_url=fresh_url,
            mode="append",
            outcome=DiscordMediaResolutionError(
                DiscordMediaResolutionReason.EAI_AGAIN
            ),
        )
        for expected_sequence in (2, 3):
            _, transport = self._resume(
                run_id,
                media={
                    fresh_url: DiscordMediaResolutionError(
                        DiscordMediaResolutionReason.EAI_AGAIN
                    )
                },
            )
            transport.assert_exhausted(self)
            current = self._read_record(record_path)
            self.assertEqual(transport.media_calls, [fresh_url])
            self.assertEqual(current["url"], old_url)
            self.assertEqual(current["candidate_urls"], [old_url, fresh_url])
            self.assertEqual(
                current["attempt_history"][-1][
                    "resolution_retry_sequence"
                ],
                expected_sequence,
            )

        _, stopped = self._resume(run_id)
        stopped.assert_exhausted(self)
        exhausted = self._read_record(record_path)
        self.assertEqual(stopped.media_calls, [])
        self.assertEqual(
            [
                attempt["resolution_retry_sequence"]
                for attempt in exhausted["attempt_history"]
                if attempt.get("url") == fresh_url
                and "resolution_retry_sequence" in attempt
            ],
            [1, 2, 3],
        )
        self.assertEqual(
            exhausted["attempt_history"][-1]["terminal_reason"],
            "media_resolution_retry_exhausted",
        )

    def test_covered_baseline_replays_crashed_retry_marker_without_new_sequence(
        self,
    ) -> None:
        old_url = "https://cdn.example/crash-marker-baseline.png"
        fresh_url = "https://cdn.example/crash-marker-fresh.png"
        run_id = "covered-typed-marker-crash"
        _, record_path, _ = self._resume_warning_with_fresh_candidate(
            run_id,
            old_url=old_url,
            fresh_url=fresh_url,
            mode="replacement",
            outcome=DiscordMediaResolutionError(
                DiscordMediaResolutionReason.EAI_AGAIN
            ),
        )

        crash_transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
        collector = DiscordEvidenceCollector(
            crash_transport,
            byte_transport=crash_transport,
            allow_rfc2544_fake_ip=True,
        )
        original_commit = collector._commit_asset_record
        crashed = False

        def crash_after_sequence_two_marker(record: dict[str, Any]) -> None:
            nonlocal crashed
            original_commit(record)
            attempt = record["attempt_history"][-1]
            if (
                not crashed
                and attempt.get("url") == fresh_url
                and attempt.get("resolution_retry_sequence") == 2
                and attempt.get("status") == "in_progress"
            ):
                crashed = True
                raise KeyboardInterrupt("synthetic covered retry marker crash")

        with patch.object(
            collector,
            "_commit_asset_record",
            side_effect=crash_after_sequence_two_marker,
        ):
            with self.assertRaises(KeyboardInterrupt):
                collector.collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=self.snapshot,
                    run_id=run_id,
                    download_assets=True,
                )
        crash_transport.assert_exhausted(self)
        interrupted = self._read_record(record_path)
        self.assertEqual(crash_transport.media_calls, [])
        self.assertEqual(interrupted["candidate_urls"], [old_url, fresh_url])
        self.assertEqual(interrupted["attempt_history"][-1]["status"], "in_progress")
        self.assertEqual(
            interrupted["attempt_history"][-1]["resolution_retry_sequence"],
            2,
        )

        _, resumed = self._resume(
            run_id,
            media={
                fresh_url: _ByteStream(
                    [b"fixed"], content_type="image/png", content_length=5
                )
            },
        )
        resumed.assert_exhausted(self)
        recovered = self._read_record(record_path)
        self.assertEqual(resumed.media_calls, [fresh_url])
        self.assertEqual(recovered["status"], "complete")
        self.assertEqual(
            [
                attempt["resolution_retry_sequence"]
                for attempt in recovered["attempt_history"]
                if attempt.get("url") == fresh_url
                and "resolution_retry_sequence" in attempt
            ],
            [1, 2],
        )

    def test_pending_youtube_reference_survives_fresh_candidate_failure(
        self,
    ) -> None:
        old_url = "https://www.youtube.com/embed/PendingFallback"
        fresh_url = "https://media.discordapp.net/external/fresh-404.mp4"
        run_id = "pending-youtube-covered-fallback"
        initial = _FixtureTransport(allow_rfc2544_fake_ip=True)
        _add_inventory(initial, [{"id": "300", "type": 2, "name": "voice"}])
        initial.add_json(
            "/channels/300/messages",
            [{"id": "30", "embeds": [{"video": {"url": old_url}}]}],
            {"limit": 100},
        )
        initial.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        initial.add_json(
            "/channels/300/messages/pins",
            KeyboardInterrupt(),
            {"limit": 50},
        )
        initial.add_media(
            old_url,
            DiscordMediaResolutionError(
                DiscordMediaResolutionReason.EAI_AGAIN
            ),
        )
        with self.assertRaises(KeyboardInterrupt):
            DiscordEvidenceCollector(
                initial,
                byte_transport=initial,
                allow_rfc2544_fake_ip=True,
            ).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=self.snapshot,
                run_id=run_id,
                download_assets=True,
            )
        initial.assert_exhausted(self)

        run_root = self.workspace / "evidence/runs" / run_id
        record_path = next((run_root / "asset-records").glob("*.json"))
        record = self._read_record(record_path)
        record["attempt_history"][-1].update(
            {
                "status": "interrupted",
                "terminal_reason": "interrupted",
                "failure_detail": None,
                "http_content_type": None,
                "http_content_length": None,
                "actual_bytes": 0,
                "sha256": None,
                "blob_path": None,
            }
        )
        record.update(
            {
                "status": "in_progress",
                "terminal_reason": "interrupted",
                "http_content_type": None,
                "http_content_length": None,
                "actual_bytes": 0,
                "sha256": None,
                "blob_path": None,
            }
        )
        self._persist_record(run_root, record_path, record)

        resumed = _FixtureTransport(allow_rfc2544_fake_ip=True)
        resumed.add_media(
            old_url,
            DiscordMediaSecurityError("synthetic player policy rejection"),
        )
        resumed.add_json(
            "/channels/300/messages/pins",
            {
                "items": [
                    {
                        "pinned_at": "2026-07-20T00:00:00+00:00",
                        "message": {
                            "id": "30",
                            "embeds": [{"video": {"url": fresh_url}}],
                        },
                    }
                ],
                "has_more": False,
            },
            {"limit": 50},
        )
        resumed.add_media(
            fresh_url,
            DiscordAPIError("missing", status_code=404),
        )
        result = DiscordEvidenceCollector(
            resumed,
            byte_transport=resumed,
            allow_rfc2544_fake_ip=True,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id=run_id,
            download_assets=True,
        )

        resumed.assert_exhausted(self)
        restored = self._read_record(record_path)
        self.assertEqual(resumed.media_calls, [old_url, fresh_url])
        self.assertEqual(restored["status"], "reference_only")
        self.assertEqual(
            restored["terminal_reason"],
            "youtube_embed_player_reference",
        )
        self.assertEqual(restored["url"], old_url)
        self.assertEqual(restored["candidate_urls"], [old_url, fresh_url])
        self.assertEqual(
            restored["reference_provenance"]["failed_attempt_number"],
            1,
        )
        self.assertEqual(len(restored["attempt_history"]), 2)
        self.assertEqual(
            restored["attempt_history"][-1]["terminal_reason"],
            "download_http_404",
        )
        self.assertEqual(result.manifest["media"]["failed"], 0)
        self.assertEqual(result.manifest["media"]["reference_only"], 1)

        reference_bytes = record_path.read_bytes()
        _, no_op = self._resume(run_id)
        no_op.assert_exhausted(self)
        self.assertEqual(no_op.media_calls, [])
        self.assertEqual(record_path.read_bytes(), reference_bytes)

        latest_url = "https://media.discordapp.net/external/latest-404.mp4"
        multi_generation = self._read_record(record_path)
        multi_generation["candidate_urls"] = [old_url, latest_url]
        multi_generation["observed_urls"].append(latest_url)
        multi_generation["observations"].append(
            {
                "source": deepcopy(multi_generation["sources"][-1]),
                "url": latest_url,
                "proxy_url": None,
                "metadata": {"url": latest_url},
            }
        )
        multi_generation["attempt_history"].append(
            {
                "url": latest_url,
                "status": "failed",
                "terminal_reason": "download_http_404",
                "http_content_type": None,
                "http_content_length": None,
                "actual_bytes": 0,
                "sha256": None,
                "blob_path": None,
            }
        )
        self._persist_record(run_root, record_path, multi_generation)
        multi_generation_bytes = record_path.read_bytes()

        _, multi_generation_no_op = self._resume(run_id)
        multi_generation_no_op.assert_exhausted(self)
        self.assertEqual(multi_generation_no_op.media_calls, [])
        self.assertEqual(record_path.read_bytes(), multi_generation_bytes)

    def test_youtube_reference_retries_typed_candidate_without_message_replay(
        self,
    ) -> None:
        old_url = "https://www.youtube.com/embed/TypedFallback"
        fresh_url = "https://media.discordapp.net/external/typed-fallback.mp4"
        run_id = "youtube-reference-typed-fallback"
        initial = _FixtureTransport(allow_rfc2544_fake_ip=True)
        _add_inventory(initial, [{"id": "300", "type": 2, "name": "voice"}])
        initial.add_json(
            "/channels/300/messages",
            [{"id": "30", "embeds": [{"video": {"url": old_url}}]}],
            {"limit": 100},
        )
        initial.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        initial.add_json(
            "/channels/300/messages/pins",
            KeyboardInterrupt(),
            {"limit": 50},
        )
        initial.add_media(
            old_url,
            DiscordMediaSecurityError("synthetic player policy rejection"),
        )
        with self.assertRaises(KeyboardInterrupt):
            DiscordEvidenceCollector(
                initial,
                byte_transport=initial,
                allow_rfc2544_fake_ip=True,
            ).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=self.snapshot,
                run_id=run_id,
                download_assets=True,
            )
        initial.assert_exhausted(self)

        first = _FixtureTransport(allow_rfc2544_fake_ip=True)
        first.add_json(
            "/channels/300/messages/pins",
            {
                "items": [
                    {
                        "pinned_at": "2026-07-20T00:00:00+00:00",
                        "message": {
                            "id": "30",
                            "embeds": [{"video": {"url": fresh_url}}],
                        },
                    }
                ],
                "has_more": False,
            },
            {"limit": 50},
        )
        first.add_media(
            fresh_url,
            DiscordMediaResolutionError(
                DiscordMediaResolutionReason.EAI_AGAIN
            ),
        )
        DiscordEvidenceCollector(
            first,
            byte_transport=first,
            allow_rfc2544_fake_ip=True,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id=run_id,
            download_assets=True,
        )
        first.assert_exhausted(self)
        record_path = next(
            (
                self.workspace
                / "evidence/runs"
                / run_id
                / "asset-records"
            ).glob("*.json")
        )
        reference = self._read_record(record_path)
        self.assertEqual(first.media_calls, [fresh_url])
        self.assertEqual(reference["status"], "reference_only")
        self.assertEqual(reference["url"], old_url)
        self.assertEqual(reference["candidate_urls"], [old_url, fresh_url])
        self.assertEqual(
            reference["attempt_history"][-1]["resolution_retry_sequence"],
            1,
        )

        _, second = self._resume(
            run_id,
            media={
                fresh_url: _ByteStream(
                    [b"video"],
                    content_type="video/mp4",
                    content_length=5,
                )
            },
        )
        second.assert_exhausted(self)
        recovered = self._read_record(record_path)
        self.assertEqual(second.media_calls, [fresh_url])
        self.assertEqual(recovered["status"], "complete")
        self.assertEqual(recovered["url"], fresh_url)
        self.assertEqual(recovered["candidate_urls"], [old_url, fresh_url])
        self.assertNotIn("reference_provenance", recovered)
        self.assertEqual(
            [
                attempt["resolution_retry_sequence"]
                for attempt in recovered["attempt_history"]
                if attempt.get("url") == fresh_url
                and "resolution_retry_sequence" in attempt
            ],
            [1, 2],
        )

    def test_youtube_reference_proxy_failure_keeps_unproxied_source_baseline(
        self,
    ) -> None:
        old_url = "https://www.youtube.com/embed/ProxyFallback"
        proxy_url = "https://media.discordapp.net/external/proxy-fallback.mp4"
        run_id = "youtube-reference-proxy-fallback"
        initial = _FixtureTransport(allow_rfc2544_fake_ip=True)
        _add_inventory(initial, [{"id": "300", "type": 2, "name": "voice"}])
        initial.add_json(
            "/channels/300/messages",
            [{"id": "30", "embeds": [{"video": {"url": old_url}}]}],
            {"limit": 100},
        )
        initial.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        initial.add_json(
            "/channels/300/messages/pins",
            KeyboardInterrupt(),
            {"limit": 50},
        )
        initial.add_media(
            old_url,
            DiscordMediaSecurityError("synthetic player policy rejection"),
        )
        with self.assertRaises(KeyboardInterrupt):
            DiscordEvidenceCollector(
                initial,
                byte_transport=initial,
                allow_rfc2544_fake_ip=True,
            ).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=self.snapshot,
                run_id=run_id,
                download_assets=True,
            )
        initial.assert_exhausted(self)

        resumed = _FixtureTransport(allow_rfc2544_fake_ip=True)
        resumed.add_json(
            "/channels/300/messages/pins",
            {
                "items": [
                    {
                        "pinned_at": "2026-07-20T00:00:00+00:00",
                        "message": {
                            "id": "30",
                            "embeds": [
                                {
                                    "video": {
                                        "url": old_url,
                                        "proxy_url": proxy_url,
                                    }
                                }
                            ],
                        },
                    }
                ],
                "has_more": False,
            },
            {"limit": 50},
        )
        resumed.add_media(
            proxy_url,
            DiscordAPIError("missing", status_code=404),
        )
        result = DiscordEvidenceCollector(
            resumed,
            byte_transport=resumed,
            allow_rfc2544_fake_ip=True,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id=run_id,
            download_assets=True,
        )
        resumed.assert_exhausted(self)
        record_path = next((result.run_root / "asset-records").glob("*.json"))
        reference = self._read_record(record_path)
        self.assertEqual(resumed.media_calls, [proxy_url])
        self.assertEqual(reference["status"], "reference_only")
        self.assertEqual(reference["url"], old_url)
        self.assertEqual(reference["candidate_urls"], [old_url, proxy_url])
        self.assertIsNone(reference["declared_metadata"].get("proxy_url"))
        self.assertEqual(
            reference["attempt_history"][-1]["terminal_reason"],
            "download_http_404",
        )

        reference_bytes = record_path.read_bytes()
        _, no_op = self._resume(run_id)
        no_op.assert_exhausted(self)
        self.assertEqual(no_op.media_calls, [])
        self.assertEqual(record_path.read_bytes(), reference_bytes)

    def test_covered_fallback_crash_replays_pending_and_remaining_candidates(
        self,
    ) -> None:
        old_url = "https://cdn.example/crash-baseline-old.png"
        fresh_one = "https://cdn.example/crash-fresh-one.png"
        fresh_two = "https://media.discordapp.net/external/crash-fresh-two.png"
        run_id = "covered-fallback-crash"
        _, record_path, attachment = self._seed_warning_before_pins(
            run_id,
            old_url=old_url,
        )

        first_resume = _FixtureTransport(allow_rfc2544_fake_ip=True)
        first_resume.add_json(
            "/channels/300/messages/pins",
            {
                "items": [
                    {
                        "pinned_at": "2026-07-20T00:00:00+00:00",
                        "message": {
                            "id": "30",
                            "attachments": [
                                {
                                    **attachment,
                                    "url": fresh_one,
                                    "proxy_url": fresh_two,
                                }
                            ],
                        },
                    }
                ],
                "has_more": False,
            },
            {"limit": 50},
        )
        first_resume.add_media(
            fresh_one,
            DiscordAPIError("missing", status_code=404),
        )
        collector = DiscordEvidenceCollector(
            first_resume,
            byte_transport=first_resume,
            allow_rfc2544_fake_ip=True,
        )
        original_commit = collector._commit_asset_record
        interrupted = False

        def crash_after_second_marker(record: dict[str, Any]) -> None:
            nonlocal interrupted
            original_commit(record)
            attempts = record.get("attempt_history", [])
            if (
                not interrupted
                and len(attempts) == 3
                and attempts[-1].get("url") == fresh_two
                and attempts[-1].get("status") == "in_progress"
            ):
                interrupted = True
                raise KeyboardInterrupt("synthetic second candidate marker crash")

        with patch.object(
            collector,
            "_commit_asset_record",
            side_effect=crash_after_second_marker,
        ):
            with self.assertRaises(KeyboardInterrupt):
                collector.collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=self.snapshot,
                    run_id=run_id,
                    download_assets=True,
                )
        first_resume.assert_exhausted(self)
        interrupted_record = self._read_record(record_path)
        self.assertEqual(first_resume.media_calls, [fresh_one])
        self.assertEqual(interrupted_record["status"], "in_progress")
        self.assertEqual(interrupted_record["url"], fresh_two)
        self.assertEqual(
            interrupted_record["attempt_history"][-2]["terminal_reason"],
            "download_http_404",
        )
        self.assertEqual(
            interrupted_record["attempt_history"][-1]["status"],
            "in_progress",
        )

        second_resume = _FixtureTransport(allow_rfc2544_fake_ip=True)
        second_resume.add_media(
            fresh_two,
            _ByteStream(
                [b"fixed"], content_type="image/png", content_length=5
            ),
        )
        result = DiscordEvidenceCollector(
            second_resume,
            byte_transport=second_resume,
            allow_rfc2544_fake_ip=True,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id=run_id,
            download_assets=True,
        )

        second_resume.assert_exhausted(self)
        recovered = self._read_record(record_path)
        self.assertEqual(second_resume.media_calls, [fresh_two])
        self.assertEqual(recovered["status"], "complete")
        self.assertEqual(recovered["url"], fresh_two)
        self.assertEqual(len(recovered["attempt_history"]), 3)
        self.assertEqual(result.manifest["media"]["complete"], 1)

    def test_resume_repairs_legacy_covered_fallback_commit_window(self) -> None:
        old_url = "https://cdn.example/legacy-window-old.png"
        fresh_url = "https://cdn.example/legacy-window-failed.png"
        run_id = "covered-fallback-legacy-window"
        run_root, record_path, _ = self._seed_warning_before_pins(
            run_id,
            old_url=old_url,
        )
        record = self._read_record(record_path)
        fresh_source = {
            "message_id": "30",
            "channel_id": "300",
            "stream": "pins_300",
        }
        fresh_metadata = deepcopy(record["declared_metadata"])
        fresh_metadata["url"] = fresh_url
        record["sources"].append(fresh_source)
        record["observations"].append(
            {
                "source": fresh_source,
                "metadata": fresh_metadata,
                "url": fresh_url,
                "proxy_url": None,
            }
        )
        record["observed_urls"].append(fresh_url)
        record.update(
            {
                "url": fresh_url,
                "candidate_urls": [fresh_url],
                "declared_metadata": fresh_metadata,
                "status": "failed",
                "terminal_reason": "download_http_404",
                "http_content_type": None,
                "http_content_length": None,
                "actual_bytes": 0,
                "sha256": None,
                "blob_path": None,
            }
        )
        record["attempt_history"].append(
            {
                "url": fresh_url,
                "status": "failed",
                "terminal_reason": "download_http_404",
                "http_content_type": None,
                "http_content_length": None,
                "actual_bytes": 0,
                "sha256": None,
                "blob_path": None,
            }
        )
        self._persist_record(run_root, record_path, record)

        resumed = _FixtureTransport(allow_rfc2544_fake_ip=True)
        resumed.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        result = DiscordEvidenceCollector(
            resumed,
            byte_transport=resumed,
            allow_rfc2544_fake_ip=True,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id=run_id,
            download_assets=True,
        )

        resumed.assert_exhausted(self)
        repaired = self._read_record(record_path)
        self.assertEqual(resumed.media_calls, [])
        self.assertEqual(repaired["status"], "captured_with_warning")
        self.assertEqual(repaired["url"], old_url)
        self.assertEqual(repaired["candidate_urls"], [old_url, fresh_url])
        self.assertEqual(len(repaired["attempt_history"]), 2)
        self.assertEqual(result.manifest["media"]["failed"], 0)

    def test_resume_covered_warning_continues_unseen_persisted_candidate(
        self,
    ) -> None:
        baseline_url = "https://cdn.example/persisted-baseline.png"
        unseen_url = (
            "https://media.discordapp.net/external/persisted-unseen.png"
        )
        run_id = "covered-persisted-unseen"
        run_root, record_path, _ = self._seed_warning_before_pins(
            run_id,
            old_url=baseline_url,
        )
        record = self._read_record(record_path)
        record["declared_metadata"]["proxy_url"] = unseen_url
        record["observations"][0]["metadata"]["proxy_url"] = unseen_url
        record["observations"][0]["proxy_url"] = unseen_url
        record["observed_urls"].append(unseen_url)
        record["candidate_urls"] = [baseline_url, unseen_url]
        self._persist_record(run_root, record_path, record)

        transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
        transport.add_media(
            unseen_url,
            _ByteStream(
                [b"fixed"], content_type="image/png", content_length=5
            ),
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        result = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
            allow_rfc2544_fake_ip=True,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id=run_id,
            download_assets=True,
        )

        transport.assert_exhausted(self)
        recovered = self._read_record(record_path)
        self.assertEqual(transport.media_calls, [unseen_url])
        self.assertEqual(recovered["status"], "complete")
        self.assertEqual(recovered["url"], unseen_url)
        self.assertEqual(result.manifest["media"]["complete"], 1)

    def test_covered_baseline_keeps_typed_retry_ahead_of_unseen_candidate(
        self,
    ) -> None:
        baseline_url = "https://cdn.example/order-baseline.png"
        typed_url = "https://cdn.example/order-typed.png"
        unseen_url = "https://media.discordapp.net/external/order-unseen.png"
        run_id = "covered-baseline-priority"
        run_root, record_path, _ = self._seed_warning_before_pins(
            run_id,
            old_url=baseline_url,
        )
        record = self._read_record(record_path)
        request_content = (run_root / "request.json").read_bytes()
        context = media_resolution_context(
            json.loads(request_content),
            hashlib.sha256(request_content).hexdigest(),
        )
        source = {
            "message_id": "30",
            "channel_id": "300",
            "stream": "pins_300_priority",
        }
        metadata = deepcopy(record["declared_metadata"])
        metadata["url"] = typed_url
        metadata["proxy_url"] = unseen_url
        record["sources"].append(source)
        record["observations"].append(
            {
                "source": source,
                "metadata": metadata,
                "url": typed_url,
                "proxy_url": unseen_url,
            }
        )
        record["observed_urls"].extend([typed_url, unseen_url])
        typed_attempt = {
            "url": typed_url,
            "status": "failed",
            "terminal_reason": "media_resolution_failed_transient",
            "failure_detail": "resolver_eai_again",
            "policy_inputs_sha256": context.policy_inputs_sha256,
            "resolution_retry_sequence": 1,
            "http_content_type": None,
            "http_content_length": None,
            "actual_bytes": 0,
            "sha256": None,
            "blob_path": None,
        }
        record["attempt_history"].append(typed_attempt)
        record.update(
            {
                "url": typed_url,
                "candidate_urls": [typed_url, unseen_url],
                "declared_metadata": deepcopy(metadata),
                "status": "failed",
                "terminal_reason": "media_resolution_failed_transient",
                "http_content_type": None,
                "http_content_length": None,
                "actual_bytes": 0,
                "sha256": None,
                "blob_path": None,
            }
        )
        self._persist_record(run_root, record_path, record)

        resumed = _FixtureTransport(allow_rfc2544_fake_ip=True)
        resumed.add_media(
            typed_url,
            DiscordAPIError("missing", status_code=404),
        )
        resumed.add_media(
            unseen_url,
            _ByteStream(
                [b"fixed"], content_type="image/png", content_length=5
            ),
        )
        resumed.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        result = DiscordEvidenceCollector(
            resumed,
            byte_transport=resumed,
            allow_rfc2544_fake_ip=True,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id=run_id,
            download_assets=True,
        )

        resumed.assert_exhausted(self)
        recovered = self._read_record(record_path)
        self.assertEqual(resumed.media_calls, [typed_url, unseen_url])
        self.assertEqual(recovered["status"], "complete")
        self.assertEqual(recovered["url"], unseen_url)
        self.assertEqual(result.manifest["media"]["complete"], 1)

    def test_attempted_candidate_retry_does_not_mutate_uncommitted_record(self) -> None:
        proxy_url = "https://media.discordapp.net/external/already-attempted.png"
        run_root, record_path = self._seed_attachment_run(
            "attempted-candidate-stability",
            outcome=DiscordMediaResolutionError(
                DiscordMediaResolutionReason.EAI_AGAIN
            ),
            proxy_url=proxy_url,
            proxy_outcome=DiscordAPIError("missing", status_code=404),
        )
        record = self._read_record(record_path)
        request_content = (run_root / "request.json").read_bytes()
        context = media_resolution_context(
            json.loads(request_content),
            hashlib.sha256(request_content).hexdigest(),
        )
        validate_resolution_attempt_history(record, context=context)
        before = discord_collector_module._canonical_json_bytes(record)

        transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
        collector = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
            allow_rfc2544_fake_ip=True,
        )
        collector._resolution_context = context
        collector._attempted_asset_urls = {(record["logical_key"], self._OFFICIAL_URL)}
        writes: list[bytes] = []

        def capture(current: dict[str, Any]) -> None:
            validate_resolution_attempt_history(current, context=context)
            writes.append(discord_collector_module._canonical_json_bytes(current))

        with patch.object(collector, "_write_asset_record", side_effect=capture):
            collector._download_asset_candidates(record)

        self.assertEqual(transport.media_calls, [])
        self.assertEqual(writes, [])
        self.assertEqual(
            discord_collector_module._canonical_json_bytes(record),
            before,
        )

    def test_unobserved_candidate_rejection_does_not_append_attempt_marker(
        self,
    ) -> None:
        _, record_path, _ = self._seed_warning_before_pins(
            "unobserved-candidate-no-marker",
            old_url="https://cdn.example/observed-baseline.png",
        )
        record = self._read_record(record_path)
        unobserved_url = "https://media.discordapp.net/external/unobserved.png"
        record["url"] = unobserved_url
        record["candidate_urls"].append(unobserved_url)
        before = discord_collector_module._canonical_json_bytes(record)
        transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
        collector = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
            allow_rfc2544_fake_ip=True,
        )
        collector._attempted_asset_urls = set()

        with self.assertRaisesRegex(
            ValueError,
            "candidate observation is missing",
        ):
            collector._download_asset(record)

        transport.assert_exhausted(self)
        self.assertEqual(transport.media_calls, [])
        self.assertEqual(collector._attempted_asset_urls, set())
        self.assertEqual(
            discord_collector_module._canonical_json_bytes(record),
            before,
        )

    def test_terminal_candidate_rotation_without_action_preserves_committed_state(
        self,
    ) -> None:
        proxy_url = "https://media.discordapp.net/external/terminal-rotation.png"
        cases = (
            (
                "http",
                DiscordAPIError("missing", status_code=404),
                set(),
            ),
            (
                "typed-already-attempted",
                DiscordMediaResolutionError(
                    DiscordMediaResolutionReason.EAI_AGAIN
                ),
                {("30:attachment:400", self._OFFICIAL_URL)},
            ),
        )
        for suffix, direct_outcome, attempted in cases:
            with self.subTest(case=suffix):
                run_root, record_path = self._seed_attachment_run(
                    f"terminal-rotation-{suffix}",
                    outcome=direct_outcome,
                    proxy_url=proxy_url,
                    proxy_outcome=DiscordAPIError("missing", status_code=404),
                )
                record = self._read_record(record_path)
                attempt_history_before = deepcopy(record["attempt_history"])
                terminal_before = {
                    field: deepcopy(record.get(field))
                    for field in (
                        "url",
                        "candidate_urls",
                        "status",
                        "terminal_reason",
                        "http_content_type",
                        "http_content_length",
                        "actual_bytes",
                        "sha256",
                        "blob_path",
                    )
                }
                request_content = (run_root / "request.json").read_bytes()
                context = media_resolution_context(
                    json.loads(request_content),
                    hashlib.sha256(request_content).hexdigest(),
                )
                transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
                collector = DiscordEvidenceCollector(
                    transport,
                    byte_transport=transport,
                    allow_rfc2544_fake_ip=True,
                )
                collector._resolution_context = context
                collector._asset_records = {record["logical_key"]: record}
                collector._attempted_asset_urls = set(attempted)
                collector._download_assets = True

                refreshed_metadata = deepcopy(record["declared_metadata"])
                refreshed_metadata.pop("proxy_url", None)
                candidate = {
                    "logical_key": record["logical_key"],
                    "kind": record["kind"],
                    "field": record["field"],
                    "url": self._OFFICIAL_URL,
                    "candidate_urls": [self._OFFICIAL_URL],
                    "declared_metadata": refreshed_metadata,
                    "declared_content_type": record["declared_content_type"],
                    "identity_metadata": deepcopy(record["identity_metadata"]),
                }
                writes: list[dict[str, Any]] = []

                def capture(current: dict[str, Any]) -> None:
                    validate_resolution_attempt_history(current, context=context)
                    writes.append(deepcopy(current))

                with patch.object(
                    collector,
                    "_write_asset_record",
                    side_effect=capture,
                ):
                    collector._collect_asset(
                        candidate,
                        {
                            "message_id": "30",
                            "channel_id": "300",
                            "stream": "pins_300",
                        },
                    )

                self.assertEqual(transport.media_calls, [])
                self.assertEqual(len(writes), 1)
                self.assertEqual(record["attempt_history"], attempt_history_before)
                for field, value in terminal_before.items():
                    self.assertEqual(record.get(field), value, field)
                self.assertEqual(
                    {source["stream"] for source in record["sources"]},
                    {"messages_300", "pins_300"},
                )

    def test_identity_conflict_finalizes_pending_tail_without_new_io(self) -> None:
        for pending_kind in ("typed", "untyped-partial"):
            with self.subTest(pending_kind=pending_kind):
                run_id = f"pending-conflict-{pending_kind}"
                run_root, record_path = self._seed_attachment_run(
                    run_id,
                    outcome=DiscordMediaResolutionError(
                        DiscordMediaResolutionReason.EAI_AGAIN
                    ),
                )
                record = self._read_record(record_path)
                pending = record["attempt_history"][-1]
                if pending_kind == "typed":
                    pending.update(
                        {
                            "status": "in_progress",
                            "terminal_reason": None,
                            "failure_detail": None,
                        }
                    )
                    record.update(
                        {"status": "in_progress", "terminal_reason": None}
                    )
                else:
                    for field in (
                        "policy_inputs_sha256",
                        "resolution_retry_sequence",
                        "failure_detail",
                    ):
                        pending.pop(field, None)
                    pending.update(
                        {
                            "status": "interrupted",
                            "terminal_reason": "interrupted",
                            "http_content_type": "image/png",
                            "http_content_length": 4,
                            "actual_bytes": 2,
                        }
                    )
                    record.update(
                        {
                            "status": "in_progress",
                            "terminal_reason": "interrupted",
                            "http_content_type": "image/png",
                            "http_content_length": 4,
                            "actual_bytes": 2,
                        }
                    )
                request_path = run_root / "request.json"
                request_content = request_path.read_bytes()
                context = media_resolution_context(
                    json.loads(request_content),
                    hashlib.sha256(request_content).hexdigest(),
                )
                validate_resolution_attempt_history(record, context=context)

                transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
                collector = DiscordEvidenceCollector(
                    transport,
                    byte_transport=transport,
                    allow_rfc2544_fake_ip=True,
                )
                collector._resolution_context = context
                collector._asset_records = {record["logical_key"]: record}
                collector._attempted_asset_urls = set()
                collector._download_assets = True
                conflict_url = (
                    "https://cdn.discordapp.com/attachments/conflict-new.png"
                )
                conflict_metadata = deepcopy(record["declared_metadata"])
                conflict_metadata.update({"url": conflict_url, "size": 5})
                conflict_identity = deepcopy(record["identity_metadata"])
                conflict_identity["size"] = 5
                candidate = {
                    "logical_key": record["logical_key"],
                    "kind": record["kind"],
                    "field": record["field"],
                    "url": conflict_url,
                    "candidate_urls": [conflict_url],
                    "declared_metadata": conflict_metadata,
                    "declared_content_type": record["declared_content_type"],
                    "identity_metadata": conflict_identity,
                }
                writes: list[dict[str, Any]] = []

                def capture(current: dict[str, Any]) -> None:
                    validate_resolution_attempt_history(current, context=context)
                    writes.append(deepcopy(current))

                source = {
                    "message_id": "30",
                    "channel_id": "300",
                    "stream": "pins_300",
                }
                with patch.object(collector, "_write_asset_record", side_effect=capture):
                    collector._collect_asset(candidate, source)
                    collector._collect_asset(candidate, source)

                self.assertEqual(transport.media_calls, [])
                self.assertEqual(len(writes), 2)
                self.assertEqual(len(record["attempt_history"]), 1)
                self.assertEqual(record["status"], "failed")
                self.assertEqual(
                    record["attempt_history"][0]["terminal_reason"],
                    "logical_identity_conflict",
                )
                self.assertIsNone(record["attempt_history"][0]["failure_detail"])
                self.assertEqual(
                    record["identity_conflicts"],
                    [{"observation_index": 1, "observed_identity": conflict_identity}],
                )

    def test_legacy_marker_is_append_only_and_proxy_only(self) -> None:
        _, record_path = self._seed_legacy_failure("legacy-append-only")
        before = self._read_record(record_path)
        old_attempt_bytes = discord_collector_module._canonical_json_bytes(
            before["attempt_history"][0],
            newline=False,
        )
        outcome = DiscordMediaResolutionError(
            DiscordMediaResolutionReason.EAI_AGAIN
        )
        _, resumed_transport = self._resume(
            "legacy-append-only",
            media={self._OFFICIAL_URL: outcome},
        )
        resumed_transport.assert_exhausted(self)
        after = self._read_record(record_path)
        self.assertEqual(
            discord_collector_module._canonical_json_bytes(
                after["attempt_history"][0],
                newline=False,
            ),
            old_attempt_bytes,
        )
        marker = after["attempt_history"][1]
        self.assertEqual(marker["retry_trigger"], LEGACY_RETRY_TRIGGER)
        self.assertEqual(marker["retry_of_attempt_number"], 1)
        self.assertEqual(marker["resolution_retry_sequence"], 1)
        self.assertEqual(
            marker["policy_inputs_sha256"],
            rfc2544_fake_ip_media_policy_descriptor()["inputs_sha256"],
        )

        _, proxy_record_path, direct_url, proxy_url = self._seed_proxy_failure(
            "legacy-proxy-only"
        )
        result, proxy_transport = self._resume(
            "legacy-proxy-only",
            media={
                proxy_url: _ByteStream(
                    [b"png!"],
                    content_type="image/png",
                    content_length=4,
                )
            },
        )
        proxy_transport.assert_exhausted(self)
        proxy_record = self._read_record(proxy_record_path)
        self.assertEqual(result.manifest["media"]["complete"], 1)
        self.assertEqual(proxy_transport.media_calls, [proxy_url])
        self.assertNotIn(direct_url, proxy_transport.media_calls)
        self.assertEqual(proxy_record["attempt_history"][-1]["retry_of_attempt_number"], 2)

    def test_resolution_retry_budget_is_three_committed_sequences(self) -> None:
        _, record_path = self._seed_legacy_failure("bounded-resolution")
        outcomes = (
            DiscordMediaResolutionError(DiscordMediaResolutionReason.EAI_AGAIN),
            DiscordMediaResolutionError(DiscordMediaResolutionReason.TIMEOUT),
            DiscordMediaResolutionError(DiscordMediaResolutionReason.EAI_AGAIN),
        )
        for sequence, outcome in enumerate(outcomes, start=1):
            with self.subTest(sequence=sequence):
                _, transport = self._resume(
                    "bounded-resolution",
                    media={self._OFFICIAL_URL: outcome},
                )
                transport.assert_exhausted(self)
                record = self._read_record(record_path)
                typed = [
                    attempt
                    for attempt in record["attempt_history"]
                    if "resolution_retry_sequence" in attempt
                ]
                self.assertEqual(
                    [attempt["resolution_retry_sequence"] for attempt in typed],
                    list(range(1, sequence + 1)),
                )
        exhausted = self._read_record(record_path)
        self.assertEqual(
            exhausted["terminal_reason"],
            "media_resolution_retry_exhausted",
        )
        self.assertEqual(
            exhausted["attempt_history"][-1]["retry_trigger"],
            RESOLUTION_RETRY_TRIGGER,
        )

        _, terminal_transport = self._resume("bounded-resolution")
        self.assertEqual(terminal_transport.media_calls, [])
        terminal = self._read_record(record_path)
        self.assertEqual(len(terminal["attempt_history"]), 4)

    def test_mixed_failures_consume_three_typed_sequences_then_stop_io(self) -> None:
        run_id = "mixed-resolution-budget"
        _, record_path = self._seed_attachment_run(
            run_id,
            outcome=DiscordMediaResolutionError(
                DiscordMediaResolutionReason.EAI_AGAIN
            ),
        )
        for outcome in (
            DiscordMediaResolutionError(DiscordMediaResolutionReason.TIMEOUT),
            RuntimeError("synthetic transport failure"),
        ):
            _, transport = self._resume(
                run_id,
                media={self._OFFICIAL_URL: outcome},
            )
            transport.assert_exhausted(self)

        terminal_bytes = record_path.read_bytes()
        terminal = self._read_record(record_path)
        typed = [
            attempt
            for attempt in terminal["attempt_history"]
            if "resolution_retry_sequence" in attempt
        ]
        self.assertEqual(
            [attempt["resolution_retry_sequence"] for attempt in typed],
            [1, 2, 3],
        )
        self.assertEqual(typed[-1]["terminal_reason"], "download_failed_transient")

        outcome = DiscordMediaResolutionError(DiscordMediaResolutionReason.EAI_AGAIN)
        _, stopped_transport = self._resume(
            run_id,
            media={self._OFFICIAL_URL: outcome},
        )
        self.assertEqual(stopped_transport.media_calls, [])
        self.assertEqual(record_path.read_bytes(), terminal_bytes)

    def test_content_length_failure_advances_the_next_typed_sequence(self) -> None:
        run_id = "mixed-content-length"
        _, record_path = self._seed_attachment_run(
            run_id,
            outcome=DiscordMediaResolutionError(
                DiscordMediaResolutionReason.EAI_AGAIN
            ),
        )
        _, mismatch_transport = self._resume(
            run_id,
            media={
                self._OFFICIAL_URL: _ByteStream(
                    [b"data"], content_type="image/png", content_length=5
                )
            },
        )
        mismatch_transport.assert_exhausted(self)
        mismatch = self._read_record(record_path)
        self.assertEqual(mismatch["terminal_reason"], "content_length_mismatch")
        self.assertEqual(
            mismatch["attempt_history"][-1]["resolution_retry_sequence"],
            2,
        )

        _, completed_transport = self._resume(
            run_id,
            media={
                self._OFFICIAL_URL: _ByteStream(
                    [b"data"], content_type="image/png", content_length=4
                )
            },
        )
        completed_transport.assert_exhausted(self)
        completed = self._read_record(record_path)
        final_attempt = completed["attempt_history"][-1]
        self.assertEqual(completed["status"], "complete")
        self.assertEqual(final_attempt["resolution_retry_sequence"], 3)
        self.assertEqual(final_attempt["retry_of_attempt_number"], 2)

    def test_missing_byte_transport_preserves_typed_retry_for_later_resume(self) -> None:
        run_id = "typed-retry-without-byte-transport"
        run_root, record_path = self._seed_attachment_run(
            run_id,
            outcome=DiscordMediaResolutionError(
                DiscordMediaResolutionReason.EAI_AGAIN
            ),
        )
        before_record = record_path.read_bytes()
        before_ledger = self._asset_ledger_state(run_root)

        no_bytes_transport = _FixtureTransport()
        DiscordEvidenceCollector(
            no_bytes_transport,
            byte_transport=None,
            allow_rfc2544_fake_ip=True,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id=run_id,
            download_assets=True,
        )
        self.assertEqual(no_bytes_transport.media_calls, [])
        self.assertEqual(record_path.read_bytes(), before_record)
        self.assertEqual(self._asset_ledger_state(run_root), before_ledger)

        _, resumed_transport = self._resume(
            run_id,
            media={
                self._OFFICIAL_URL: _ByteStream(
                    [b"data"], content_type="image/png", content_length=4
                )
            },
        )
        resumed_transport.assert_exhausted(self)
        resumed = self._read_record(record_path)
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(
            [
                attempt["resolution_retry_sequence"]
                for attempt in resumed["attempt_history"]
            ],
            [1, 2],
        )
        self.assertEqual(
            resumed["attempt_history"][-1]["retry_of_attempt_number"],
            1,
        )

    def test_resolution_terminal_outcomes_do_not_resume_again(self) -> None:
        cases: tuple[tuple[str, BaseException, str], ...] = (
            (
                "unresolved",
                DiscordMediaResolutionError(
                    DiscordMediaResolutionReason.NAME_NOT_FOUND
                ),
                "media_resolution_unresolved",
            ),
            (
                "invalid",
                DiscordMediaResolutionInvalidAnswer(),
                "media_resolution_invalid_answer",
            ),
            (
                "security",
                DiscordMediaSecurityError("new synthetic security rejection"),
                "unsafe_media_url",
            ),
        )
        for suffix, outcome, terminal_reason in cases:
            with self.subTest(outcome=suffix):
                run_id = f"terminal-{suffix}"
                _, record_path = self._seed_legacy_failure(run_id)
                _, first_transport = self._resume(
                    run_id,
                    media={self._OFFICIAL_URL: outcome},
                )
                first_transport.assert_exhausted(self)
                first = self._read_record(record_path)
                self.assertEqual(first["terminal_reason"], terminal_reason)
                first_attempts = deepcopy(first["attempt_history"])

                _, no_op_transport = self._resume(run_id)
                self.assertEqual(no_op_transport.media_calls, [])
                self.assertEqual(
                    self._read_record(record_path)["attempt_history"],
                    first_attempts,
                )

    def test_fresh_security_rejection_is_terminal_across_resume(self) -> None:
        run_id = "fresh-security-terminal"
        _, record_path = self._seed_attachment_run(
            run_id,
            outcome=DiscordMediaSecurityError("synthetic fresh rejection"),
        )
        first = self._read_record(record_path)
        self.assertEqual(
            first["attempt_history"][-1].get("security_rejection"),
            {
                "version": 1,
                "reason_code": "media_security_policy_rejected",
                "legacy_eligible": False,
            },
        )

        _, transport = self._resume(
            run_id,
            media={
                self._OFFICIAL_URL: DiscordMediaSecurityError(
                    "must remain unused"
                )
            },
        )

        self.assertEqual(transport.media_calls, [])
        self.assertEqual(
            self._read_record(record_path)["attempt_history"],
            first["attempt_history"],
        )

    def test_fresh_global_tail_blocks_older_candidate_without_media_io(self) -> None:
        run_id = "fresh-global-tail-blocks-legacy-candidate"
        fresh_url = (
            "https://cdn.discordapp.com/attachments/300/401/fresh.png"
            "?sig=synthetic"
        )
        run_root, record_path = self._seed_attachment_run(
            run_id,
            outcome=DiscordMediaSecurityError("synthetic legacy rejection"),
            proxy_url=fresh_url,
            proxy_outcome=DiscordMediaSecurityError(
                "synthetic fresh rejection"
            ),
        )
        record = self._read_record(record_path)
        self.assertEqual(
            [attempt["url"] for attempt in record["attempt_history"]],
            [self._OFFICIAL_URL, fresh_url],
        )
        record["attempt_history"][0].pop("security_rejection", None)
        expected_history = deepcopy(record["attempt_history"])
        self._persist_record(run_root, record_path, record)

        _, transport = self._resume(
            run_id,
            media={
                self._OFFICIAL_URL: DiscordMediaSecurityError(
                    "must remain unused"
                ),
                fresh_url: DiscordMediaSecurityError("must remain unused"),
            },
        )

        self.assertEqual(transport.media_calls, [])
        self.assertEqual(
            self._read_record(record_path)["attempt_history"],
            expected_history,
        )

    def test_legacy_recovery_rejections_are_network_free(self) -> None:
        cases = (
            ("nonofficial", "https://cdn.example/asset.png", True),
            ("non443", "https://cdn.discordapp.com:444/asset.png", True),
            (
                "credentials",
                "https://user:pass@cdn.discordapp.com/asset.png",
                True,
            ),
            ("nonopt", self._OFFICIAL_URL, False),
        )
        for suffix, url, allow_policy in cases:
            with self.subTest(rejection=suffix):
                run_id = f"legacy-reject-{suffix}"
                _, record_path = self._seed_legacy_failure(
                    run_id,
                    url=url,
                    allow_rfc2544_fake_ip=allow_policy,
                )
                _, transport = self._resume(
                    run_id,
                    allow_rfc2544_fake_ip=allow_policy,
                )
                self.assertEqual(transport.media_calls, [])
                self.assertEqual(len(self._read_record(record_path)["attempt_history"]), 1)

        _, complete_path = self._seed_attachment_run(
            "legacy-reject-covered",
            outcome=_ByteStream(
                [b"data"], content_type="image/png", content_length=4
            ),
        )
        _, complete_transport = self._resume("legacy-reject-covered")
        self.assertEqual(complete_transport.media_calls, [])
        self.assertEqual(self._read_record(complete_path)["status"], "complete")

        _, http_path = self._seed_attachment_run(
            "legacy-reject-current-404",
            outcome=DiscordAPIError("missing", status_code=404),
        )
        _, http_transport = self._resume("legacy-reject-current-404")
        self.assertEqual(http_transport.media_calls, [])
        self.assertEqual(
            self._read_record(http_path)["terminal_reason"],
            "download_http_404",
        )

    def test_record_latest_bytes_and_blob_conditions_all_block_legacy(self) -> None:
        mutations: tuple[tuple[str, Callable[[dict[str, Any], Path], None]], ...] = (
            (
                "record-not-unsafe",
                lambda record, _root: record.update(
                    {"status": "failed", "terminal_reason": "download_http_404"}
                ),
            ),
            (
                "latest-not-unsafe",
                lambda record, _root: record["attempt_history"].append(
                    {
                        "url": record["url"],
                        "status": "failed",
                        "terminal_reason": "download_http_404",
                        "http_content_type": None,
                        "http_content_length": None,
                        "actual_bytes": 0,
                        "sha256": None,
                        "blob_path": None,
                    }
                ),
            ),
            (
                "bytes",
                lambda record, _root: (
                    record.update({"actual_bytes": 1}),
                    record["attempt_history"][-1].update({"actual_bytes": 1}),
                ),
            ),
        )
        for suffix, mutate in mutations:
            with self.subTest(rejection=suffix):
                run_id = f"legacy-shape-{suffix}"
                run_root, record_path = self._seed_legacy_failure(run_id)
                record = self._read_record(record_path)
                mutate(record, run_root)
                self._persist_record(run_root, record_path, record)
                if suffix in {"record-not-unsafe", "latest-not-unsafe"}:
                    transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
                    with self.assertRaises(ValueError):
                        self._resume(run_id, collector=DiscordEvidenceCollector(
                            transport,
                            byte_transport=transport,
                            allow_rfc2544_fake_ip=True,
                        ))
                    self.assertEqual(transport.media_calls, [])
                else:
                    _, transport = self._resume(run_id)
                    self.assertEqual(transport.media_calls, [])

        run_root, record_path = self._seed_legacy_failure("legacy-shape-blob")
        record = self._read_record(record_path)
        blob_bytes = b"x"
        digest = hashlib.sha256(blob_bytes).hexdigest()
        relative = Path("assets") / "sha256" / digest[:2] / f"{digest}.bin"
        blob_path = run_root / relative
        blob_path.parent.mkdir(exist_ok=True)
        blob_path.write_bytes(blob_bytes)
        for item in (record, record["attempt_history"][-1]):
            item.update(
                {
                    "actual_bytes": 1,
                    "sha256": digest,
                    "blob_path": relative.as_posix(),
                }
            )
        self._persist_record(run_root, record_path, record)
        _, blob_transport = self._resume("legacy-shape-blob")
        self.assertEqual(blob_transport.media_calls, [])

    def test_corrupt_typed_histories_fail_before_byte_transport(self) -> None:
        def duplicate_marker(record: dict[str, Any]) -> None:
            record["attempt_history"].append(
                deepcopy(record["attempt_history"][-1])
            )

        def sequence_four(record: dict[str, Any]) -> None:
            attempt = deepcopy(record["attempt_history"][-1])
            attempt.update(
                {
                    "retry_trigger": RESOLUTION_RETRY_TRIGGER,
                    "retry_of_attempt_number": 2,
                    "resolution_retry_sequence": 4,
                    "terminal_reason": "media_resolution_retry_exhausted",
                }
            )
            record["attempt_history"].append(attempt)

        def typed_pending_current_mismatch(record: dict[str, Any]) -> None:
            record["attempt_history"][-1].update(
                {
                    "status": "in_progress",
                    "terminal_reason": None,
                    "failure_detail": None,
                    "http_content_type": None,
                    "http_content_length": None,
                    "actual_bytes": 0,
                    "sha256": None,
                    "blob_path": None,
                }
            )

        def interrupted_current_with_stale_unsafe_tail(
            record: dict[str, Any],
        ) -> None:
            record["attempt_history"] = record["attempt_history"][:1]
            record.update(
                {
                    "status": "in_progress",
                    "terminal_reason": "interrupted",
                    "http_content_type": None,
                    "http_content_length": None,
                    "actual_bytes": 0,
                    "sha256": None,
                    "blob_path": None,
                }
            )

        mutations: tuple[tuple[str, Callable[[dict[str, Any]], None]], ...] = (
            (
                "retry-of-zero",
                lambda record: record["attempt_history"][-1].update(
                    {"retry_of_attempt_number": 0}
                ),
            ),
            (
                "candidate-cross-reference",
                lambda record: record["attempt_history"][-1].update(
                    {"url": "https://media.discordapp.net/external/other.png"}
                ),
            ),
            ("duplicate-marker", duplicate_marker),
            (
                "policy-mismatch",
                lambda record: record["attempt_history"][-1].update(
                    {"policy_inputs_sha256": "0" * 64}
                ),
            ),
            (
                "sequence-skip",
                lambda record: record["attempt_history"][-1].update(
                    {"resolution_retry_sequence": 2}
                ),
            ),
            ("sequence-over-maximum", sequence_four),
            (
                "detail-reason-mismatch",
                lambda record: record["attempt_history"][-1].update(
                    {"failure_detail": "resolver_name_not_found"}
                ),
            ),
            ("typed-pending-current-mismatch", typed_pending_current_mismatch),
            (
                "interrupted-current-stale-unsafe-tail",
                interrupted_current_with_stale_unsafe_tail,
            ),
        )
        for suffix, mutate in mutations:
            with self.subTest(corruption=suffix):
                run_id = f"corrupt-{suffix}"
                run_root, record_path = self._seed_legacy_transient(run_id)
                record = self._read_record(record_path)
                mutate(record)
                self._persist_record(run_root, record_path, record)
                transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
                collector = DiscordEvidenceCollector(
                    transport,
                    byte_transport=transport,
                    allow_rfc2544_fake_ip=True,
                )
                with self.assertRaises(ValueError):
                    collector.collect(
                        workspace=self.workspace,
                        output_dir="evidence",
                        targets=self.snapshot,
                        run_id=run_id,
                        download_assets=True,
                    )
                self.assertEqual(transport.media_calls, [])

    def test_non_opt_in_forged_policy_hash_fails_before_network(self) -> None:
        run_root, record_path = self._seed_attachment_run(
            "nonopt-forged-policy",
            outcome=DiscordMediaResolutionError(
                DiscordMediaResolutionReason.EAI_AGAIN
            ),
            allow_rfc2544_fake_ip=False,
        )
        record = self._read_record(record_path)
        record["attempt_history"][-1]["policy_inputs_sha256"] = (
            rfc2544_fake_ip_media_policy_descriptor()["inputs_sha256"]
        )
        self._persist_record(run_root, record_path, record)
        transport = _FixtureTransport()
        with self.assertRaises(ValueError):
            DiscordEvidenceCollector(
                transport,
                byte_transport=transport,
            ).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=self.snapshot,
                run_id="nonopt-forged-policy",
                download_assets=True,
            )
        self.assertEqual(transport.media_calls, [])

    def test_marker_precommit_failure_retries_without_duplicate_marker(self) -> None:
        _, record_path = self._seed_legacy_failure("crash-pre-marker")
        transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
        collector = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
            allow_rfc2544_fake_ip=True,
        )
        original_commit = collector._commit_asset_record

        def fail_before_marker(record: dict[str, Any]) -> None:
            attempts = record.get("attempt_history", [])
            if attempts and attempts[-1].get("retry_trigger") == LEGACY_RETRY_TRIGGER:
                raise RuntimeError("synthetic pre-marker commit crash")
            original_commit(record)

        with patch.object(
            collector,
            "_commit_asset_record",
            side_effect=fail_before_marker,
        ):
            with self.assertRaisesRegex(RuntimeError, "pre-marker"):
                collector.collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=self.snapshot,
                    run_id="crash-pre-marker",
                    download_assets=True,
                )
        self.assertEqual(transport.media_calls, [])
        self.assertEqual(len(self._read_record(record_path)["attempt_history"]), 1)

        _, resumed_transport = self._resume(
            "crash-pre-marker",
            media={
                self._OFFICIAL_URL: DiscordMediaResolutionError(
                    DiscordMediaResolutionReason.EAI_AGAIN
                )
            },
        )
        resumed_transport.assert_exhausted(self)
        attempts = self._read_record(record_path)["attempt_history"]
        self.assertEqual(
            sum(
                attempt.get("retry_trigger") == LEGACY_RETRY_TRIGGER
                for attempt in attempts
            ),
            1,
        )

    def test_marker_is_committed_before_tempfile_and_replay_reuses_attempt(self) -> None:
        run_root, record_path = self._seed_legacy_failure("crash-post-marker")
        real_mkstemp = tempfile.mkstemp
        inspected_attempt_number: int | None = None

        def inspect_marker_before_asset_temp(*args: object, **kwargs: object):
            nonlocal inspected_attempt_number
            if kwargs.get("prefix") == ".asset-":
                record = self._read_record(record_path)
                inspected_attempt_number = len(record["attempt_history"])
                marker = record["attempt_history"][-1]
                self.assertEqual(marker["status"], "in_progress")
                self.assertEqual(marker["retry_trigger"], LEGACY_RETRY_TRIGGER)
                with closing(
                    sqlite3.connect(run_root / "asset-ledger.sqlite3")
                ) as connection:
                    committed, pending = connection.execute(
                        "SELECT committed_sha256, pending_sha256 FROM asset_records"
                    ).fetchone()
                self.assertEqual(
                    committed,
                    hashlib.sha256(record_path.read_bytes()).hexdigest(),
                )
                self.assertIsNone(pending)
                raise KeyboardInterrupt("synthetic post-marker crash")
            return real_mkstemp(*args, **kwargs)

        transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
        collector = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
            allow_rfc2544_fake_ip=True,
        )
        with patch.object(
            discord_collector_module.tempfile,
            "mkstemp",
            side_effect=inspect_marker_before_asset_temp,
        ):
            with self.assertRaises(KeyboardInterrupt):
                collector.collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=self.snapshot,
                    run_id="crash-post-marker",
                    download_assets=True,
                )
        self.assertEqual(transport.media_calls, [])
        interrupted = self._read_record(record_path)
        self.assertEqual(interrupted["attempt_history"][-1]["status"], "interrupted")
        self.assertEqual(len(interrupted["attempt_history"]), inspected_attempt_number)

        _, resumed_transport = self._resume(
            "crash-post-marker",
            media={
                self._OFFICIAL_URL: _ByteStream(
                    [b"data"], content_type="image/png", content_length=4
                )
            },
        )
        resumed_transport.assert_exhausted(self)
        replayed = self._read_record(record_path)
        self.assertEqual(replayed["status"], "complete")
        self.assertEqual(len(replayed["attempt_history"]), inspected_attempt_number)
        self.assertEqual(replayed["attempt_history"][-1]["status"], "complete")

    def test_blob_promote_crash_replays_same_committed_attempt(self) -> None:
        run_root, record_path = self._seed_legacy_failure("crash-post-blob")
        transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
        transport.add_media(
            self._OFFICIAL_URL,
            _ByteStream([b"data"], content_type="image/png", content_length=4),
        )
        collector = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
            allow_rfc2544_fake_ip=True,
        )
        with patch.object(
            collector,
            "_finish_asset_attempt",
            side_effect=KeyboardInterrupt("synthetic post-promote crash"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                collector.collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=self.snapshot,
                    run_id="crash-post-blob",
                    download_assets=True,
                )
        transport.assert_exhausted(self)
        committed = self._read_record(record_path)
        self.assertEqual(committed["attempt_history"][-1]["status"], "in_progress")
        attempt_count = len(committed["attempt_history"])
        self.assertEqual(len(list((run_root / "assets/sha256").glob("*/*"))), 1)

        _, replay_transport = self._resume(
            "crash-post-blob",
            media={
                self._OFFICIAL_URL: _ByteStream(
                    [b"data"], content_type="image/png", content_length=4
                )
            },
        )
        replay_transport.assert_exhausted(self)
        replayed = self._read_record(record_path)
        self.assertEqual(replayed["status"], "complete")
        self.assertEqual(len(replayed["attempt_history"]), attempt_count)

    def test_terminal_commit_crash_advances_without_duplicate_legacy_marker(self) -> None:
        _, record_path = self._seed_legacy_failure("crash-post-terminal")
        transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
        transport.add_media(
            self._OFFICIAL_URL,
            DiscordMediaResolutionError(
                DiscordMediaResolutionReason.EAI_AGAIN
            ),
        )
        collector = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
            allow_rfc2544_fake_ip=True,
        )
        original_write = collector._write_asset_record
        interrupted = False

        def crash_after_terminal_commit(record: dict[str, Any]) -> None:
            nonlocal interrupted
            original_write(record)
            attempts = record.get("attempt_history", [])
            if (
                not interrupted
                and attempts
                and attempts[-1].get("terminal_reason")
                == "media_resolution_failed_transient"
            ):
                interrupted = True
                raise KeyboardInterrupt("synthetic post-terminal commit crash")

        with patch.object(
            collector,
            "_write_asset_record",
            side_effect=crash_after_terminal_commit,
        ):
            with self.assertRaises(KeyboardInterrupt):
                collector.collect(
                    workspace=self.workspace,
                    output_dir="evidence",
                    targets=self.snapshot,
                    run_id="crash-post-terminal",
                    download_assets=True,
                )
        transport.assert_exhausted(self)
        terminal = self._read_record(record_path)
        self.assertEqual(
            terminal["attempt_history"][-1]["resolution_retry_sequence"],
            1,
        )

        _, replay_transport = self._resume(
            "crash-post-terminal",
            media={
                self._OFFICIAL_URL: DiscordMediaResolutionError(
                    DiscordMediaResolutionReason.EAI_AGAIN
                )
            },
        )
        replay_transport.assert_exhausted(self)
        replayed = self._read_record(record_path)
        typed = [
            attempt
            for attempt in replayed["attempt_history"]
            if "resolution_retry_sequence" in attempt
        ]
        self.assertEqual(
            [attempt["resolution_retry_sequence"] for attempt in typed],
            [1, 2],
        )
        self.assertEqual(
            sum(
                attempt.get("retry_trigger") == LEGACY_RETRY_TRIGGER
                for attempt in replayed["attempt_history"]
            ),
            1,
        )


class DiscordMediaRecoveryAuditPublicationTests(unittest.TestCase):
    _SIGNED_URL = (
        "https://cdn.discordapp.com/attachments/300/400/audit.png"
        "?sig=synthetic-audit&token=synthetic-token"
    )

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_directory.name)
        self.snapshot = _snapshot(_target("300", kind="GUILD_VOICE (2)"))

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def _collect_attachment(
        self,
        run_id: str,
        *,
        outcome: object,
        allow_rfc2544_fake_ip: bool,
    ) -> object:
        transport = _FixtureTransport(
            allow_rfc2544_fake_ip=allow_rfc2544_fake_ip
        )
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "attachments": [
                        {
                            "id": "400",
                            "filename": "audit.png",
                            "url": self._SIGNED_URL,
                            "content_type": "image/png",
                            "size": 4,
                        }
                    ],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(self._SIGNED_URL, outcome)
        result = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
            allow_rfc2544_fake_ip=allow_rfc2544_fake_ip,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id=run_id,
            download_assets=True,
        )
        transport.assert_exhausted(self)
        return result

    def _resume(
        self,
        run_id: str,
        *,
        outcome: object | None = None,
        allow_rfc2544_fake_ip: bool,
    ) -> tuple[object, _FixtureTransport]:
        transport = _FixtureTransport(
            allow_rfc2544_fake_ip=allow_rfc2544_fake_ip
        )
        if outcome is not None:
            transport.add_media(self._SIGNED_URL, outcome)
        result = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
            allow_rfc2544_fake_ip=allow_rfc2544_fake_ip,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id=run_id,
            download_assets=True,
        )
        return result, transport

    @staticmethod
    def _convert_fresh_security_record_to_legacy(result: object) -> None:
        run_root = result.run_root
        record_path = next((run_root / "asset-records").glob("*.json"))
        record = json.loads(record_path.read_text())
        for attempt in record["attempt_history"]:
            attempt.pop("security_rejection", None)
        content = discord_collector_module._canonical_json_bytes(record)
        record_path.write_bytes(content)
        with closing(sqlite3.connect(run_root / "asset-ledger.sqlite3")) as connection:
            connection.execute(
                "UPDATE asset_records SET committed_sha256 = ?, pending_sha256 = NULL "
                "WHERE logical_key = ?",
                (hashlib.sha256(content).hexdigest(), record["logical_key"]),
            )
            connection.execute(
                "UPDATE asset_metadata SET value = CAST(value AS INTEGER) + 1 "
                "WHERE key = 'records_generation'"
            )
            connection.execute(
                "UPDATE asset_metadata SET value = '-1' "
                "WHERE key = 'index_generation'"
            )
            connection.execute(
                "UPDATE asset_metadata SET value = '' "
                "WHERE key = 'asset_index_sha256'"
            )
            connection.commit()

    def _assert_published_audit(
        self,
        result: object,
        *,
        expected_policy_sha256: str | None,
    ) -> tuple[bytes, dict[str, Any]]:
        run_root = result.run_root
        final_manifest = json.loads((run_root / "manifest.json").read_text())
        descriptor = final_manifest["media_recovery_audit"]
        self.assertEqual(
            set(descriptor),
            {"version", "path", "sha256", "counts"},
        )
        self.assertEqual(descriptor["version"], MEDIA_RECOVERY_AUDIT_VERSION)
        self.assertEqual(descriptor["path"], MEDIA_RECOVERY_AUDIT_FILENAME)
        content = (run_root / descriptor["path"]).read_bytes()
        audit = json.loads(content)
        index_content = (run_root / "asset-index.jsonl").read_bytes()
        index_sha256 = hashlib.sha256(index_content).hexdigest()
        self.assertEqual(audit["asset_index_sha256"], index_sha256)
        with closing(
            sqlite3.connect(run_root / "asset-ledger.sqlite3")
        ) as connection:
            bound_index_sha256 = connection.execute(
                "SELECT value FROM asset_metadata "
                "WHERE key = 'asset_index_sha256'"
            ).fetchone()[0]
        self.assertEqual(audit["asset_index_sha256"], bound_index_sha256)
        self.assertEqual(audit["policy_inputs_sha256"], expected_policy_sha256)
        self.assertEqual(
            descriptor["sha256"],
            hashlib.sha256(content).hexdigest(),
        )
        self.assertEqual(descriptor["counts"], audit["counts"])
        self.assertNotIn(self._SIGNED_URL.encode("utf-8"), content)
        self.assertNotIn(b"sig=synthetic-audit", content)
        self.assertNotIn(b"synthetic-token", content)
        self.assertNotIn(b"Authorization", content)
        self.assertNotIn(b"bearer-secret", content)
        return content, audit

    def test_publishes_hash_bound_audit_for_opt_in_and_non_opt_in_requests(
        self,
    ) -> None:
        opt_in_policy = (
            "17b89647c19c760f58058291784f0fa55a6b55f7c91c23db738a4221d704e325"
        )
        self.assertEqual(
            rfc2544_fake_ip_media_policy_descriptor()["inputs_sha256"],
            opt_in_policy,
        )
        for suffix, allow_policy, expected_policy in (
            ("opt-in", True, opt_in_policy),
            ("non-opt-in", False, None),
        ):
            with self.subTest(policy=suffix):
                result = self._collect_attachment(
                    f"audit-{suffix}",
                    outcome=DiscordAPIError(
                        "synthetic 404 Authorization bearer-secret",
                        status_code=404,
                    ),
                    allow_rfc2544_fake_ip=allow_policy,
                )
                _, audit = self._assert_published_audit(
                    result,
                    expected_policy_sha256=expected_policy,
                )
                self.assertEqual(audit["version"], MEDIA_RECOVERY_AUDIT_VERSION)
                self.assertEqual(
                    audit["kind"],
                    "discord_media_resolution_recovery_audit",
                )
                self.assertEqual(audit["run_id"], f"audit-{suffix}")
                self.assertEqual(audit["counts"]["current_failed_records"], 1)
                self.assertEqual(audit["counts"]["unresolved_blockers"], 1)
                self.assertEqual(
                    result.manifest["media"]["failed"],
                    audit["counts"]["current_failed_records"],
                )

    def test_empty_http_200_body_is_failed_without_promoting_empty_blob(self) -> None:
        result = self._collect_attachment(
            "audit-empty-http-body",
            outcome=_ByteStream(
                [],
                content_type="application/octet-stream",
                content_length=0,
            ),
            allow_rfc2544_fake_ip=False,
        )

        record = json.loads(
            next((result.run_root / "asset-records").glob("*.json")).read_text()
        )
        attempt = record["attempt_history"][-1]
        for value in (record, attempt):
            self.assertEqual(value["status"], "failed")
            self.assertEqual(value["terminal_reason"], "download_failed_transient")
            self.assertEqual(value["actual_bytes"], 0)
            self.assertIsNone(value["sha256"])
            self.assertIsNone(value["blob_path"])
            self.assertEqual(value["http_content_length"], 0)
            self.assertEqual(
                value["http_content_type"],
                "application/octet-stream",
            )
        self.assertEqual(result.manifest["status"], "partial")
        self.assertEqual(result.manifest["media"]["failed"], 1)
        _, audit = self._assert_published_audit(
            result,
            expected_policy_sha256=None,
        )
        self.assertEqual(audit["counts"]["current_failed_records"], 1)
        self.assertEqual(audit["counts"]["other_media_failure_records"], 1)
        self.assertEqual(audit["counts"]["unresolved_blockers"], 1)
        blob_root = result.run_root / "assets" / "sha256"
        self.assertFalse(
            blob_root.exists() and any(path.is_file() for path in blob_root.rglob("*"))
        )

    def test_empty_http_body_preserves_content_length_mismatch_precedence(self) -> None:
        result = self._collect_attachment(
            "audit-empty-http-length-mismatch",
            outcome=_ByteStream(
                [],
                content_type="application/octet-stream",
                content_length=4,
            ),
            allow_rfc2544_fake_ip=False,
        )

        record = json.loads(
            next((result.run_root / "asset-records").glob("*.json")).read_text()
        )
        attempt = record["attempt_history"][-1]
        for value in (record, attempt):
            self.assertEqual(value["status"], "failed")
            self.assertEqual(value["terminal_reason"], "content_length_mismatch")
            self.assertEqual(value["actual_bytes"], 0)
            self.assertEqual(value["http_content_length"], 4)
            self.assertIsNone(value["sha256"])
            self.assertIsNone(value["blob_path"])
        blob_root = result.run_root / "assets" / "sha256"
        self.assertFalse(
            blob_root.exists() and any(path.is_file() for path in blob_root.rglob("*"))
        )

    def test_publication_verification_reads_disk_manifest_not_returned_object(
        self,
    ) -> None:
        result = self._collect_attachment(
            "audit-disk-manifest",
            outcome=DiscordAPIError("synthetic missing", status_code=404),
            allow_rfc2544_fake_ip=False,
        )
        result.manifest["media_recovery_audit"] = {
            "version": 999,
            "path": "untrusted-returned-object.json",
            "sha256": "0" * 64,
            "counts": {},
        }

        content, audit = self._assert_published_audit(
            result,
            expected_policy_sha256=None,
        )

        self.assertEqual(
            hashlib.sha256(content).hexdigest(),
            json.loads((result.run_root / "manifest.json").read_text())[
                "media_recovery_audit"
            ]["sha256"],
        )
        self.assertEqual(audit["counts"]["unresolved_blockers"], 1)

    def test_failed_404_candidate_remains_covered_by_later_proxy_binary(self) -> None:
        direct_url = "https://origin.example/image.png?sig=synthetic-direct"
        proxy_url = (
            "https://media.discordapp.net/external/audit-proxy.png"
            "?sig=synthetic-proxy"
        )
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "embeds": [
                        {"image": {"url": direct_url, "proxy_url": proxy_url}}
                    ],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        transport.add_media(
            direct_url,
            DiscordAPIError("synthetic direct 404", status_code=404),
        )
        transport.add_media(
            proxy_url,
            _ByteStream([b"data"], content_type="image/png", content_length=4),
        )

        result = DiscordEvidenceCollector(
            transport,
            byte_transport=transport,
        ).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id="audit-covered-404",
            download_assets=True,
        )

        transport.assert_exhausted(self)
        content, audit = self._assert_published_audit(
            result,
            expected_policy_sha256=None,
        )
        attempt_rows = [
            row for row in audit["items"] if row["item_kind"] == "attempt"
        ]
        self.assertEqual(len(attempt_rows), 1)
        self.assertEqual(attempt_rows[0]["terminal_reason"], "download_http_404")
        self.assertEqual(
            attempt_rows[0]["disposition"],
            "candidate_failed_record_covered",
        )
        self.assertEqual(
            audit["counts"]["candidate_failed_record_covered_attempt_rows"],
            1,
        )
        self.assertEqual(audit["counts"]["unresolved_blockers"], 0)
        self.assertEqual(result.manifest["media"]["binary_captured"], 1)
        record = json.loads(
            next((result.run_root / "asset-records").glob("*.json")).read_text()
        )
        self.assertEqual(
            [attempt["terminal_reason"] for attempt in record["attempt_history"]],
            ["download_http_404", "downloaded"],
        )
        self.assertNotIn(direct_url.encode("utf-8"), content)
        self.assertNotIn(proxy_url.encode("utf-8"), content)

    def test_unchanged_resume_keeps_exact_audit_bytes_and_sha(self) -> None:
        first = self._collect_attachment(
            "audit-unchanged-resume",
            outcome=DiscordAPIError("synthetic missing", status_code=404),
            allow_rfc2544_fake_ip=False,
        )
        first_bytes, _ = self._assert_published_audit(
            first,
            expected_policy_sha256=None,
        )
        first_sha256 = first.manifest["media_recovery_audit"]["sha256"]

        resumed, transport = self._resume(
            "audit-unchanged-resume",
            allow_rfc2544_fake_ip=False,
        )

        transport.assert_exhausted(self)
        resumed_bytes, _ = self._assert_published_audit(
            resumed,
            expected_policy_sha256=None,
        )
        self.assertEqual(resumed_bytes, first_bytes)
        self.assertEqual(
            resumed.manifest["media_recovery_audit"]["sha256"],
            first_sha256,
        )

    def test_successful_recovery_legitimately_replaces_audit_bytes_and_hash(
        self,
    ) -> None:
        first = self._collect_attachment(
            "audit-recovered",
            outcome=DiscordMediaSecurityError("synthetic legacy security failure"),
            allow_rfc2544_fake_ip=True,
        )
        policy_sha256 = rfc2544_fake_ip_media_policy_descriptor()["inputs_sha256"]
        first_bytes, first_audit = self._assert_published_audit(
            first,
            expected_policy_sha256=policy_sha256,
        )
        self._convert_fresh_security_record_to_legacy(first)

        recovered, transport = self._resume(
            "audit-recovered",
            outcome=_ByteStream(
                [b"data"], content_type="image/png", content_length=4
            ),
            allow_rfc2544_fake_ip=True,
        )

        transport.assert_exhausted(self)
        recovered_bytes, recovered_audit = self._assert_published_audit(
            recovered,
            expected_policy_sha256=policy_sha256,
        )
        self.assertNotEqual(recovered_bytes, first_bytes)
        self.assertNotEqual(
            recovered.manifest["media_recovery_audit"]["sha256"],
            first.manifest["media_recovery_audit"]["sha256"],
        )
        self.assertEqual(first_audit["counts"]["unresolved_blockers"], 1)
        self.assertEqual(recovered_audit["counts"]["unresolved_blockers"], 0)
        self.assertEqual(recovered_audit["counts"]["legacy_attempt_rows"], 1)
        self.assertEqual(
            recovered_audit["counts"]["binary_captured_attempt_rows"],
            1,
        )

    def test_interrupted_finalization_publishes_typed_pending_audit(self) -> None:
        first = self._collect_attachment(
            "audit-interrupted",
            outcome=DiscordMediaSecurityError("synthetic legacy security failure"),
            allow_rfc2544_fake_ip=True,
        )
        first_audit_bytes = (
            first.run_root / MEDIA_RECOVERY_AUDIT_FILENAME
        ).read_bytes()
        self._convert_fresh_security_record_to_legacy(first)
        interrupting = _InterruptingByteStream(
            [b"unused"], content_type="image/png", content_length=4
        )
        transport = _FixtureTransport(allow_rfc2544_fake_ip=True)
        transport.add_media(self._SIGNED_URL, interrupting)

        with self.assertRaises(KeyboardInterrupt):
            DiscordEvidenceCollector(
                transport,
                byte_transport=transport,
                allow_rfc2544_fake_ip=True,
            ).collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=self.snapshot,
                run_id="audit-interrupted",
                download_assets=True,
            )

        transport.assert_exhausted(self)
        manifest = json.loads((first.run_root / "manifest.json").read_text())
        audit_bytes = (first.run_root / MEDIA_RECOVERY_AUDIT_FILENAME).read_bytes()
        audit = json.loads(audit_bytes)
        self.assertNotEqual(audit_bytes, first_audit_bytes)
        self.assertEqual(
            manifest["media_recovery_audit"]["sha256"],
            hashlib.sha256(audit_bytes).hexdigest(),
        )
        typed_rows = [
            row
            for row in audit["items"]
            if row["retry_trigger"] == LEGACY_RETRY_TRIGGER
        ]
        self.assertEqual(len(typed_rows), 1)
        self.assertEqual(typed_rows[0]["status"], "interrupted")
        self.assertEqual(typed_rows[0]["terminal_reason"], "interrupted")
        self.assertIsNone(typed_rows[0]["failure_detail"])
        self.assertEqual(typed_rows[0]["disposition"], "resolution_retry_pending")
        self.assertEqual(audit["counts"]["current_failed_records"], 0)
        self.assertEqual(audit["counts"]["unresolved_blockers"], 0)

    def test_unchanged_index_state_fails_closed_before_audit_publication(
        self,
    ) -> None:
        for unsafe_state in ("missing", "mismatch", "symlink"):
            with self.subTest(unsafe_state=unsafe_state):
                run_id = f"audit-index-{unsafe_state}"
                first = self._collect_attachment(
                    run_id,
                    outcome=DiscordAPIError("synthetic missing", status_code=404),
                    allow_rfc2544_fake_ip=False,
                )
                index_path = first.run_root / "asset-index.jsonl"
                original_index = index_path.read_bytes()
                audit_path = first.run_root / MEDIA_RECOVERY_AUDIT_FILENAME
                original_audit = audit_path.read_bytes()
                if unsafe_state == "missing":
                    index_path.unlink()
                elif unsafe_state == "mismatch":
                    index_path.write_bytes(original_index + b"tampered")
                else:
                    target = self.workspace / f"{run_id}-index-target"
                    target.write_bytes(original_index)
                    index_path.unlink()
                    index_path.symlink_to(target)

                with self.assertRaisesRegex(
                    ValueError,
                    "asset index",
                ):
                    self._resume(
                        run_id,
                        allow_rfc2544_fake_ip=False,
                    )
                self.assertEqual(audit_path.read_bytes(), original_audit)

    def test_finalization_orders_index_checkpoint_audit_summaries_manifest(
        self,
    ) -> None:
        events: list[str] = []
        real_atomic_write = discord_collector_module._atomic_write_bytes
        real_atomic_chunks = discord_collector_module._atomic_write_chunks
        real_has_pending = discord_collector_module._AssetLedger.has_pending
        real_reconcile = discord_collector_module._AssetLedger.reconcile
        real_checkpoint = discord_collector_module._AssetLedger.checkpoint
        real_save_checkpoint = DiscordEvidenceCollector._save_checkpoint
        transport = _FixtureTransport()
        collector = DiscordEvidenceCollector(transport)
        real_retry_pending = collector._retry_pending_assets

        def recording_write(path: Path, content: bytes) -> None:
            if path.name in {
                MEDIA_RECOVERY_AUDIT_FILENAME,
                "errors.jsonl",
                "manifest.json",
            }:
                events.append(path.name)
            real_atomic_write(path, content)

        def recording_chunks(path: Path, chunks: object) -> str:
            if path.name == "asset-index.jsonl":
                events.append(path.name)
            return real_atomic_chunks(path, chunks)

        def recording_has_pending(ledger: object) -> bool:
            events.append("pending-check")
            return real_has_pending(ledger)

        def recording_reconcile(
            ledger: object,
            entry: sqlite3.Row,
            path: Path,
        ) -> bool:
            events.append("reconcile-record")
            return real_reconcile(ledger, entry, path)

        def recording_checkpoint(ledger: object) -> None:
            events.append("ledger-checkpoint")
            real_checkpoint(ledger)

        def recording_save_checkpoint(active_collector: object) -> None:
            events.append("save-checkpoint")
            real_save_checkpoint(active_collector)

        def inject_real_pending_record() -> None:
            real_retry_pending()
            record = deepcopy(next(iter(collector._asset_records.values())))
            record["observed_urls"] = [
                *record["observed_urls"],
                "https://cdn.example/finalization-pending-observation",
            ]
            logical_key = record["logical_key"]
            record_name = hashlib.sha256(logical_key.encode("utf-8")).hexdigest()
            record_path = collector._run_root / "asset-records" / f"{record_name}.json"
            content = discord_collector_module._canonical_json_bytes(record)
            digest = hashlib.sha256(content).hexdigest()
            self.assertTrue(
                collector._asset_ledger.prepare_commit(
                    logical_key,
                    record_path.name,
                    digest,
                )
            )
            discord_collector_module._atomic_write_bytes(record_path, content)

        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            [
                {
                    "id": "30",
                    "attachments": [
                        {
                            "id": "400",
                            "filename": "pending.bin",
                            "url": "https://cdn.example/finalization-pending",
                        }
                    ],
                }
            ],
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {"limit": 100, "before": "30"},
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        with patch.object(
            discord_collector_module,
            "_atomic_write_bytes",
            side_effect=recording_write,
        ), patch.object(
            discord_collector_module,
            "_atomic_write_chunks",
            side_effect=recording_chunks,
        ), patch.object(
            discord_collector_module._AssetLedger,
            "has_pending",
            new=recording_has_pending,
        ), patch.object(
            discord_collector_module._AssetLedger,
            "reconcile",
            new=recording_reconcile,
        ), patch.object(
            discord_collector_module._AssetLedger,
            "checkpoint",
            new=recording_checkpoint,
        ), patch.object(
            DiscordEvidenceCollector,
            "_save_checkpoint",
            new=recording_save_checkpoint,
        ), patch.object(
            collector,
            "_retry_pending_assets",
            side_effect=inject_real_pending_record,
        ):
            result = collector.collect(
                workspace=self.workspace,
                output_dir="evidence",
                targets=self.snapshot,
                run_id="audit-finalization-order",
                download_assets=False,
            )

        transport.assert_exhausted(self)
        finalization_start = events.index("pending-check")
        self.assertEqual(
            events[finalization_start : finalization_start + 8],
            [
                "pending-check",
                "reconcile-record",
                "save-checkpoint",
                "asset-index.jsonl",
                "ledger-checkpoint",
                MEDIA_RECOVERY_AUDIT_FILENAME,
                "errors.jsonl",
                "manifest.json",
            ],
        )
        record_path = next((result.run_root / "asset-records").glob("*.json"))
        record = json.loads(record_path.read_text())
        self.assertIn(
            "https://cdn.example/finalization-pending-observation",
            record["observed_urls"],
        )
        with closing(
            sqlite3.connect(result.run_root / "asset-ledger.sqlite3")
        ) as connection:
            committed, pending = connection.execute(
                "SELECT committed_sha256, pending_sha256 FROM asset_records"
            ).fetchone()
        self.assertEqual(
            committed,
            hashlib.sha256(
                discord_collector_module._canonical_json_bytes(record)
            ).hexdigest(),
        )
        self.assertIsNone(pending)


class DiscordReferenceResolutionPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.snapshot = _snapshot(
            _target("300", kind="GUILD_VOICE (2)", name="reference-audit")
        )

    @staticmethod
    def _reply_fixture() -> list[dict[str, object]]:
        target = _complete_fixture_message(
            {"id": "10", "type": 0, "content": "raw-body-secret"},
            "300",
        )
        assert isinstance(target, dict)
        nested_source = _complete_fixture_message(
            {
                "id": "20",
                "type": 19,
                "message_reference": {
                    "type": 0,
                    "channel_id": "300",
                    "message_id": "10",
                },
            },
            "300",
        )
        assert isinstance(nested_source, dict)
        top_level_source = deepcopy(nested_source)
        top_level_source["referenced_message"] = deepcopy(target)
        root = _complete_fixture_message(
            {
                "id": "30",
                "type": 19,
                "message_reference": {
                    "type": 0,
                    "channel_id": "300",
                    "message_id": "20",
                },
                "referenced_message": deepcopy(nested_source),
            },
            "300",
        )
        assert isinstance(root, dict)
        return [root, top_level_source, target]

    def _collect(
        self,
        run_id: str,
        *,
        messages: list[dict[str, object]] | None = None,
    ) -> object:
        transport = _FixtureTransport()
        _add_inventory(transport, [{"id": "300", "type": 2, "name": "voice"}])
        transport.add_json(
            "/channels/300/messages",
            messages if messages is not None else self._reply_fixture(),
            {"limit": 100},
        )
        transport.add_json(
            "/channels/300/messages",
            [],
            {
                "limit": 100,
                "before": min(
                    (
                        str(message["id"])
                        for message in (
                            messages if messages is not None else self._reply_fixture()
                        )
                    ),
                    key=int,
                ),
            },
        )
        transport.add_json(
            "/channels/300/messages/pins",
            {"items": [], "has_more": False},
            {"limit": 50},
        )
        result = DiscordEvidenceCollector(transport).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id=run_id,
            download_assets=False,
        )
        transport.assert_exhausted(self)
        return result

    def test_publishes_immutable_reference_sidecar_without_rewriting_raw_evidence(
        self,
    ) -> None:
        result = self._collect("reference-sidecar")

        self.assertIn("message_reference_resolution_audit", result.manifest)
        descriptor = result.manifest["message_reference_resolution_audit"]
        self.assertEqual(
            set(descriptor),
            {"version", "path", "sha256", "counts"},
        )
        self.assertEqual(descriptor["version"], 1)
        self.assertRegex(
            descriptor["path"],
            r"^message-reference-resolution-audits/[0-9a-f]{64}\.json$",
        )
        content = (result.run_root / descriptor["path"]).read_bytes()
        audit = json.loads(content)
        self.assertEqual(hashlib.sha256(content).hexdigest(), descriptor["sha256"])
        self.assertEqual(audit["counts"], descriptor["counts"])
        self.assertEqual(audit["counts"]["raw_errors"], 1)
        self.assertEqual(audit["counts"]["local_resolved"], 1)
        self.assertEqual(audit["counts"]["effective_errors"], 0)
        self.assertEqual(audit["counts"]["effective_error_diagnostics"], 0)
        self.assertEqual(audit["counts"]["effective_partial_messages"], 0)
        self.assertEqual(audit["source"]["root_messages"], 3)
        self.assertEqual(audit["source"]["resolution_input_messages"], 2)
        self.assertEqual(result.manifest["message_evidence"]["partial_messages"], 1)
        self.assertEqual(
            result.manifest["message_evidence"]["diagnostics_by_severity"]["error"],
            1,
        )
        self.assertEqual(
            result.manifest["message_evidence"]["effective_status"],
            "complete",
        )
        self.assertEqual(result.manifest["status"], "complete")
        self.assertNotIn(b"raw-body-secret", content)
        self.assertNotIn(b"https://", content)

        evidence_files = sorted((result.run_root / "message-evidence").rglob("*.jsonl"))
        evidence_before = {path: path.read_bytes() for path in evidence_files}
        root_row = next(
            row
            for path in evidence_files
            for row in map(json.loads, path.read_text().splitlines())
            if any(
                node.get("kind") == "root" and node.get("message_id") == "30"
                for node in row.get("nodes", [])
                if isinstance(node, dict)
            )
        )
        self.assertEqual(root_row["status"], "partial")
        self.assertTrue(
            any(
                item.get("code") == "referenced_message_unknown"
                for item in root_row["diagnostics"]
            )
        )

        first_stat = (result.run_root / descriptor["path"]).stat()
        resumed = DiscordEvidenceCollector(_FixtureTransport()).collect(
            workspace=self.workspace,
            output_dir="evidence",
            targets=self.snapshot,
            run_id="reference-sidecar",
            download_assets=False,
        )
        resumed_descriptor = resumed.manifest["message_reference_resolution_audit"]
        self.assertEqual(resumed_descriptor, descriptor)
        resumed_stat = (resumed.run_root / resumed_descriptor["path"]).stat()
        self.assertEqual(resumed_stat.st_ino, first_stat.st_ino)
        self.assertEqual(
            {path: path.read_bytes() for path in evidence_files},
            evidence_before,
        )

    def test_reference_sidecar_binds_the_checkpoint_request_hash(self) -> None:
        result = self._collect("reference-request-binding")
        checkpoint = json.loads((result.run_root / "checkpoint.json").read_text())

        with self.assertRaisesRegex(ValueError, "checkpoint request"):
            build_message_reference_resolution_audit(
                run_root=result.run_root,
                checkpoint=checkpoint,
                run_id=checkpoint["run_id"],
                request_sha256="0" * 64,
            )

    def test_reference_sidecar_binds_warning_code_counts(self) -> None:
        root_id = _snowflake_at("2026-01-11T00:00:00.123000+00:00")
        reference_id = _snowflake_at("2026-01-10T23:00:00.123000+00:00")
        root = _complete_fixture_message(
            {"id": root_id, "type": 0},
            "300",
        )
        assert isinstance(root, dict)
        root["message_reference"] = {
            "type": 1,
            "message_id": reference_id,
            "channel_id": "300",
        }
        root["message_snapshots"] = [
            {
                "message": {
                    "type": 0,
                    "content": "immutable snapshot",
                    "timestamp": "2026-01-10T22:58:00.123000+00:00",
                    "edited_timestamp": None,
                    "attachments": [],
                    "embeds": [],
                    "components": [],
                }
            }
        ]

        result = self._collect("reference-warning-codes", messages=[root])
        counts = result.manifest["message_reference_resolution_audit"]["counts"]

        expected_severity = {"error": 0, "warning": 1, "info": 0}
        expected_codes = {
            "error": {},
            "warning": {"snapshot_timestamp_reference_mismatch": 1},
            "info": {},
        }
        self.assertEqual(counts["raw_diagnostics_by_severity"], expected_severity)
        self.assertEqual(
            counts["effective_diagnostics_by_severity"],
            expected_severity,
        )
        self.assertEqual(
            counts["raw_diagnostic_codes_by_severity"],
            expected_codes,
        )
        self.assertEqual(
            counts["effective_diagnostic_codes_by_severity"],
            expected_codes,
        )
        summary = result.manifest["message_evidence"]
        self.assertEqual(summary["diagnostic_codes_by_severity"], expected_codes)
        self.assertEqual(
            summary["effective_diagnostic_codes_by_severity"],
            expected_codes,
        )
        self.assertEqual(summary["effective_status"], "complete_with_warnings")
        self.assertEqual(result.manifest["status"], "complete_with_warnings")

    def test_run_status_uses_effective_reference_warning_state(self) -> None:
        messages = self._reply_fixture()
        warning_root_id = _snowflake_at("2026-01-11T00:00:00.123000+00:00")
        warning_reference_id = _snowflake_at(
            "2026-01-10T23:00:00.123000+00:00"
        )
        warning_root = _complete_fixture_message(
            {"id": warning_root_id, "type": 0},
            "300",
        )
        assert isinstance(warning_root, dict)
        warning_root["message_reference"] = {
            "type": 1,
            "message_id": warning_reference_id,
            "channel_id": "300",
        }
        warning_root["message_snapshots"] = [
            {
                "message": {
                    "type": 0,
                    "content": "immutable snapshot",
                    "timestamp": "2026-01-10T22:58:00.123000+00:00",
                    "edited_timestamp": None,
                    "attachments": [],
                    "embeds": [],
                    "components": [],
                }
            }
        ]
        messages.insert(0, warning_root)

        result = self._collect(
            "reference-resolved-error-and-warning",
            messages=messages,
        )
        summary = result.manifest["message_evidence"]

        self.assertEqual(summary["status"], "partial")
        self.assertEqual(summary["effective_status"], "complete_with_warnings")
        self.assertEqual(result.manifest["status"], "complete_with_warnings")

    def test_reference_sidecar_rejects_parent_exchange_without_deleting_owned_link(
        self,
    ) -> None:
        run_root = self.workspace / "exchange-run"
        run_root.mkdir()
        audit = {
            "schema_version": 1,
            "kind": "discord_message_reference_resolution_audit",
            "counts": {},
        }
        audit_directory = (
            run_root
            / discord_reference_sidecar_module.MESSAGE_REFERENCE_RESOLUTION_AUDIT_DIRECTORY
        )
        moved_directory = run_root / "moved-audit-directory"
        real_link = os.link
        exchanged = False

        def exchange_parent_before_link(
            source: object,
            destination: object,
            *args: object,
            **kwargs: object,
        ) -> None:
            nonlocal exchanged
            if not exchanged:
                exchanged = True
                audit_directory.rename(moved_directory)
                audit_directory.mkdir()
            real_link(source, destination, *args, **kwargs)

        with patch.object(
            discord_reference_sidecar_module.os,
            "link",
            side_effect=exchange_parent_before_link,
        ), self.assertRaisesRegex(ValueError, "publication changed"):
            discord_reference_sidecar_module.publish_message_reference_resolution_audit(
                run_root=run_root,
                audit=audit,
            )

        self.assertTrue(exchanged)
        self.assertEqual(list(audit_directory.iterdir()), [])
        retained = list(moved_directory.iterdir())
        self.assertEqual(len(retained), 1)
        self.assertEqual(
            retained[0].read_bytes(),
            discord_reference_sidecar_module.canonical_message_reference_resolution_audit_bytes(
                audit
            ),
        )

    def test_reference_sidecar_rejects_post_link_destination_replacement(
        self,
    ) -> None:
        run_root = self.workspace / "post-link-replacement"
        run_root.mkdir()
        audit = {
            "schema_version": 1,
            "kind": "discord_message_reference_resolution_audit",
            "counts": {},
        }
        real_link = os.link
        replaced = False

        def replace_destination_after_link(
            source: object,
            destination: object,
            *args: object,
            **kwargs: object,
        ) -> None:
            nonlocal replaced
            real_link(source, destination, *args, **kwargs)
            destination_fd = kwargs["dst_dir_fd"]
            os.unlink(destination, dir_fd=destination_fd)
            foreign_fd = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=destination_fd,
            )
            try:
                os.write(foreign_fd, b"foreign-content\n")
            finally:
                os.close(foreign_fd)
            replaced = True

        with patch.object(
            discord_reference_sidecar_module.os,
            "link",
            side_effect=replace_destination_after_link,
        ), self.assertRaisesRegex(ValueError, "publication changed|content differs"):
            discord_reference_sidecar_module.publish_message_reference_resolution_audit(
                run_root=run_root,
                audit=audit,
            )

        self.assertTrue(replaced)

    def test_reference_sidecar_fsync_failure_does_not_delete_replacement(
        self,
    ) -> None:
        run_root = self.workspace / "post-link-rollback"
        run_root.mkdir()
        audit = {
            "schema_version": 1,
            "kind": "discord_message_reference_resolution_audit",
            "counts": {},
        }
        content = (
            discord_reference_sidecar_module.canonical_message_reference_resolution_audit_bytes(
                audit
            )
        )
        digest = hashlib.sha256(content).hexdigest()
        destination_path = (
            run_root
            / discord_reference_sidecar_module.MESSAGE_REFERENCE_RESOLUTION_AUDIT_DIRECTORY
            / f"{digest}.json"
        )
        real_link = os.link
        real_fsync = os.fsync
        linked_destination: tuple[object, int] | None = None
        injected = False

        def record_link(
            source: object,
            destination: object,
            *args: object,
            **kwargs: object,
        ) -> None:
            nonlocal linked_destination
            real_link(source, destination, *args, **kwargs)
            linked_destination = (destination, kwargs["dst_dir_fd"])

        def replace_then_fail(fd: int) -> None:
            nonlocal injected
            if linked_destination is not None and not injected:
                destination, destination_fd = linked_destination
                if fd == destination_fd:
                    injected = True
                    os.unlink(destination, dir_fd=destination_fd)
                    foreign_fd = os.open(
                        destination,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=destination_fd,
                    )
                    try:
                        os.write(foreign_fd, b"foreign-content\n")
                    finally:
                        os.close(foreign_fd)
                    raise OSError("injected directory fsync failure")
            real_fsync(fd)

        with patch.object(
            discord_reference_sidecar_module.os,
            "link",
            side_effect=record_link,
        ), patch.object(
            discord_reference_sidecar_module.os,
            "fsync",
            side_effect=replace_then_fail,
        ), self.assertRaisesRegex(OSError, "injected directory fsync failure"):
            discord_reference_sidecar_module.publish_message_reference_resolution_audit(
                run_root=run_root,
                audit=audit,
            )

        self.assertTrue(injected)
        self.assertEqual(destination_path.read_bytes(), b"foreign-content\n")

    def test_reference_sidecar_temp_collision_preserves_foreign_file(self) -> None:
        run_root = self.workspace / "temp-name-collision"
        run_root.mkdir()
        audit = {
            "schema_version": 1,
            "kind": "discord_message_reference_resolution_audit",
            "counts": {},
        }
        content = (
            discord_reference_sidecar_module.canonical_message_reference_resolution_audit_bytes(
                audit
            )
        )
        digest = hashlib.sha256(content).hexdigest()
        audit_directory = (
            run_root
            / discord_reference_sidecar_module.MESSAGE_REFERENCE_RESOLUTION_AUDIT_DIRECTORY
        )
        audit_directory.mkdir()
        token = "fixed-token"
        foreign_temp = audit_directory / f".{digest}.json.{token}"
        foreign_temp.write_bytes(b"foreign-temp\n")

        with patch.object(
            discord_reference_sidecar_module.secrets,
            "token_hex",
            return_value=token,
        ), self.assertRaises(FileExistsError):
            discord_reference_sidecar_module.publish_message_reference_resolution_audit(
                run_root=run_root,
                audit=audit,
            )

        self.assertEqual(foreign_temp.read_bytes(), b"foreign-temp\n")

    def test_reference_sidecar_rechecks_destination_after_temp_cleanup(self) -> None:
        run_root = self.workspace / "cleanup-destination-replacement"
        run_root.mkdir()
        audit = {
            "schema_version": 1,
            "kind": "discord_message_reference_resolution_audit",
            "counts": {},
        }
        real_link = os.link
        real_unlink = os.unlink
        linked_destination: tuple[object, int] | None = None
        replaced = False

        def record_link(
            source: object,
            destination: object,
            *args: object,
            **kwargs: object,
        ) -> None:
            nonlocal linked_destination
            real_link(source, destination, *args, **kwargs)
            linked_destination = (destination, kwargs["dst_dir_fd"])

        def replace_after_temp_cleanup(
            path: object,
            *,
            dir_fd: int | None = None,
        ) -> None:
            nonlocal replaced
            real_unlink(path, dir_fd=dir_fd)
            if (
                not replaced
                and linked_destination is not None
                and isinstance(path, str)
                and path.startswith(".")
            ):
                destination, destination_fd = linked_destination
                replaced = True
                real_unlink(destination, dir_fd=destination_fd)
                foreign_fd = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=destination_fd,
                )
                try:
                    os.write(foreign_fd, b"foreign-content\n")
                finally:
                    os.close(foreign_fd)

        with patch.object(
            discord_reference_sidecar_module.os,
            "link",
            side_effect=record_link,
        ), patch.object(
            discord_reference_sidecar_module.os,
            "unlink",
            side_effect=replace_after_temp_cleanup,
        ), self.assertRaisesRegex(ValueError, "publication changed|content differs"):
            discord_reference_sidecar_module.publish_message_reference_resolution_audit(
                run_root=run_root,
                audit=audit,
            )

        self.assertTrue(replaced)

    def test_reference_sidecar_rechecks_existing_destination_after_fsync(
        self,
    ) -> None:
        run_root = self.workspace / "existing-destination-replacement"
        run_root.mkdir()
        audit = {
            "schema_version": 1,
            "kind": "discord_message_reference_resolution_audit",
            "counts": {},
        }
        descriptor = (
            discord_reference_sidecar_module.publish_message_reference_resolution_audit(
                run_root=run_root,
                audit=audit,
            )
        )
        artifact_path = run_root / descriptor["path"]
        real_fsync = os.fsync
        replaced = False

        def replace_before_fsync(fd: int) -> None:
            nonlocal replaced
            if not replaced:
                replaced = True
                artifact_path.unlink()
                artifact_path.write_bytes(b"foreign-content\n")
            real_fsync(fd)

        with patch.object(
            discord_reference_sidecar_module.os,
            "fsync",
            side_effect=replace_before_fsync,
        ), self.assertRaisesRegex(ValueError, "content differs|changed while reading"):
            discord_reference_sidecar_module.publish_message_reference_resolution_audit(
                run_root=run_root,
                audit=audit,
            )

        self.assertTrue(replaced)

    def test_reference_sidecar_fstat_failure_cleans_owned_temp(self) -> None:
        run_root = self.workspace / "temp-fstat-failure"
        run_root.mkdir()
        audit = {
            "schema_version": 1,
            "kind": "discord_message_reference_resolution_audit",
            "counts": {},
        }
        real_open = os.open
        real_fstat = os.fstat
        temporary_fd: int | None = None
        injected = False

        def record_temp_open(
            path: object,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal temporary_fd
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            if isinstance(path, str) and path.startswith("."):
                temporary_fd = descriptor
            return descriptor

        def fail_first_temp_fstat(fd: int) -> os.stat_result:
            nonlocal injected
            if temporary_fd is not None and fd == temporary_fd and not injected:
                injected = True
                raise OSError("injected temp fstat failure")
            return real_fstat(fd)

        with patch.object(
            discord_reference_sidecar_module.os,
            "open",
            side_effect=record_temp_open,
        ), patch.object(
            discord_reference_sidecar_module.os,
            "fstat",
            side_effect=fail_first_temp_fstat,
        ), self.assertRaisesRegex(OSError, "injected temp fstat failure"):
            discord_reference_sidecar_module.publish_message_reference_resolution_audit(
                run_root=run_root,
                audit=audit,
            )

        self.assertTrue(injected)
        audit_directory = (
            run_root
            / discord_reference_sidecar_module.MESSAGE_REFERENCE_RESOLUTION_AUDIT_DIRECTORY
        )
        self.assertEqual(list(audit_directory.iterdir()), [])

    def test_reference_sidecar_close_failure_still_cleans_owned_temp(self) -> None:
        run_root = self.workspace / "temp-close-failure"
        run_root.mkdir()
        audit = {
            "schema_version": 1,
            "kind": "discord_message_reference_resolution_audit",
            "counts": {},
        }
        real_open = os.open
        real_close = os.close
        temporary_fd: int | None = None
        injected = False

        def record_temp_open(
            path: object,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal temporary_fd
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            if isinstance(path, str) and path.startswith("."):
                temporary_fd = descriptor
            return descriptor

        def fail_temp_close(fd: int) -> None:
            nonlocal injected
            if temporary_fd is not None and fd == temporary_fd and not injected:
                injected = True
                real_close(fd)
                raise OSError("injected temp close failure")
            real_close(fd)

        with patch.object(
            discord_reference_sidecar_module.os,
            "open",
            side_effect=record_temp_open,
        ), patch.object(
            discord_reference_sidecar_module.os,
            "close",
            side_effect=fail_temp_close,
        ), self.assertRaisesRegex(OSError, "injected temp close failure"):
            discord_reference_sidecar_module.publish_message_reference_resolution_audit(
                run_root=run_root,
                audit=audit,
            )

        self.assertTrue(injected)
        audit_directory = (
            run_root
            / discord_reference_sidecar_module.MESSAGE_REFERENCE_RESOLUTION_AUDIT_DIRECTORY
        )
        self.assertFalse(
            any(path.name.startswith(".") for path in audit_directory.iterdir())
        )

    def test_reference_sidecar_child_fstat_failure_closes_directory_fd(self) -> None:
        run_root = self.workspace / "child-fstat-failure"
        run_root.mkdir()
        audit = {
            "schema_version": 1,
            "kind": "discord_message_reference_resolution_audit",
            "counts": {},
        }
        real_open = os.open
        real_fstat = os.fstat
        child_fd: int | None = None
        injected = False

        def record_directory_open(
            path: object,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal child_fd
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            if (
                path
                == discord_reference_sidecar_module.MESSAGE_REFERENCE_RESOLUTION_AUDIT_DIRECTORY
                and dir_fd is not None
            ):
                child_fd = descriptor
            return descriptor

        def fail_child_fstat(fd: int) -> os.stat_result:
            nonlocal injected
            if child_fd is not None and fd == child_fd and not injected:
                injected = True
                raise OSError("injected child fstat failure")
            return real_fstat(fd)

        with patch.object(
            discord_reference_sidecar_module.os,
            "open",
            side_effect=record_directory_open,
        ), patch.object(
            discord_reference_sidecar_module.os,
            "fstat",
            side_effect=fail_child_fstat,
        ), self.assertRaisesRegex(OSError, "injected child fstat failure"):
            discord_reference_sidecar_module.publish_message_reference_resolution_audit(
                run_root=run_root,
                audit=audit,
            )

        self.assertTrue(injected)
        self.assertIsNotNone(child_fd)
        assert child_fd is not None
        try:
            with self.assertRaises(OSError):
                os.fstat(child_fd)
        finally:
            try:
                os.close(child_fd)
            except OSError:
                pass

    def test_reference_sidecar_rejects_in_place_rewrite_while_reading(self) -> None:
        result = self._collect("reference-in-place-rewrite")
        checkpoint = json.loads((result.run_root / "checkpoint.json").read_text())
        descriptor = result.manifest["message_reference_resolution_audit"]
        artifact_path = result.run_root / descriptor["path"]
        real_read = os.read
        rewritten = False

        def rewrite_after_read(fd: int, size: int) -> bytes:
            nonlocal rewritten
            content = real_read(fd, size)
            if not rewritten:
                rewritten = True
                artifact_path.write_bytes(b"x" * max(1, len(content) + 1))
            return content

        with patch.object(
            discord_reference_sidecar_module.os,
            "read",
            side_effect=rewrite_after_read,
        ), self.assertRaisesRegex(ValueError, "changed while reading"):
            discord_reference_sidecar_module.verify_published_message_reference_resolution_audit(
                run_root=result.run_root,
                checkpoint=checkpoint,
                run_id=checkpoint["run_id"],
                request_sha256=checkpoint["request_sha256"],
                descriptor=descriptor,
            )

        self.assertTrue(rewritten)

    def test_reference_sidecar_rejects_root_exchange_during_final_publish_read(
        self,
    ) -> None:
        run_root = self.workspace / "publish-final-root-exchange"
        run_root.mkdir()
        moved_root = run_root.with_name(f"{run_root.name}-moved")
        audit = {
            "schema_version": 1,
            "kind": "discord_message_reference_resolution_audit",
            "counts": {},
        }
        real_read = os.read
        nonempty_reads = 0
        exchanged = False

        def exchange_during_final_read(fd: int, size: int) -> bytes:
            nonlocal nonempty_reads, exchanged
            content = real_read(fd, size)
            if content:
                nonempty_reads += 1
                if nonempty_reads == 2:
                    exchanged = True
                    run_root.rename(moved_root)
                    run_root.mkdir()
            return content

        with patch.object(
            discord_reference_sidecar_module.os,
            "read",
            side_effect=exchange_during_final_read,
        ), self.assertRaisesRegex(ValueError, "root binding changed"):
            discord_reference_sidecar_module.publish_message_reference_resolution_audit(
                run_root=run_root,
                audit=audit,
            )

        self.assertTrue(exchanged)
        self.assertEqual(list(run_root.iterdir()), [])
        self.assertTrue(
            any(
                path.is_file()
                for path in (
                    moved_root
                    / discord_reference_sidecar_module.MESSAGE_REFERENCE_RESOLUTION_AUDIT_DIRECTORY
                ).iterdir()
            )
        )

    def test_reference_sidecar_rejects_existing_root_exchange_on_final_read(
        self,
    ) -> None:
        run_root = self.workspace / "existing-final-root-exchange"
        run_root.mkdir()
        moved_root = run_root.with_name(f"{run_root.name}-moved")
        audit = {
            "schema_version": 1,
            "kind": "discord_message_reference_resolution_audit",
            "counts": {},
        }
        discord_reference_sidecar_module.publish_message_reference_resolution_audit(
            run_root=run_root,
            audit=audit,
        )
        real_read = os.read
        nonempty_reads = 0
        exchanged = False

        def exchange_during_final_read(fd: int, size: int) -> bytes:
            nonlocal nonempty_reads, exchanged
            content = real_read(fd, size)
            if content:
                nonempty_reads += 1
                if nonempty_reads == 2:
                    exchanged = True
                    run_root.rename(moved_root)
                    run_root.mkdir()
            return content

        with patch.object(
            discord_reference_sidecar_module.os,
            "read",
            side_effect=exchange_during_final_read,
        ), self.assertRaisesRegex(ValueError, "root binding changed"):
            discord_reference_sidecar_module.publish_message_reference_resolution_audit(
                run_root=run_root,
                audit=audit,
            )

        self.assertTrue(exchanged)
        self.assertEqual(list(run_root.iterdir()), [])

    def test_reference_sidecar_verify_rejects_run_root_exchange_during_read(
        self,
    ) -> None:
        result = self._collect("reference-verify-root-exchange")
        checkpoint = json.loads((result.run_root / "checkpoint.json").read_text())
        descriptor = result.manifest["message_reference_resolution_audit"]
        original_root = result.run_root
        moved_root = original_root.with_name(f"{original_root.name}-moved")
        real_read = os.read
        exchanged = False

        def exchange_after_first_read(fd: int, size: int) -> bytes:
            nonlocal exchanged
            content = real_read(fd, size)
            if not exchanged:
                exchanged = True
                original_root.rename(moved_root)
                original_root.mkdir()
                (original_root / "pages").mkdir()
                (original_root / "pages/external-secret.txt").write_text(
                    "must-not-be-read"
                )
            return content

        with patch.object(
            discord_reference_sidecar_module.os,
            "read",
            side_effect=exchange_after_first_read,
        ), self.assertRaisesRegex(ValueError, "root binding changed"):
            discord_reference_sidecar_module.verify_published_message_reference_resolution_audit(
                run_root=original_root,
                checkpoint=checkpoint,
                run_id=checkpoint["run_id"],
                request_sha256=checkpoint["request_sha256"],
                descriptor=descriptor,
            )

        self.assertTrue(exchanged)

    def test_reference_sidecar_link_race_adopts_same_and_rejects_different(
        self,
    ) -> None:
        audit = {
            "schema_version": 1,
            "kind": "discord_message_reference_resolution_audit",
            "counts": {},
        }
        for same_content in (True, False):
            with self.subTest(same_content=same_content):
                run_root = self.workspace / f"link-race-{same_content}"
                run_root.mkdir()
                real_link = os.link
                injected = False

                def inject_destination_then_collide(
                    source: object,
                    destination: object,
                    *args: object,
                    **kwargs: object,
                ) -> None:
                    nonlocal injected
                    if not injected:
                        injected = True
                        if same_content:
                            real_link(source, destination, *args, **kwargs)
                        else:
                            destination_fd = kwargs["dst_dir_fd"]
                            fd = os.open(
                                destination,
                                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                0o600,
                                dir_fd=destination_fd,
                            )
                            try:
                                os.write(fd, b"attacker-content\n")
                            finally:
                                os.close(fd)
                    real_link(source, destination, *args, **kwargs)

                context = (
                    self.assertRaisesRegex(ValueError, "content differs")
                    if not same_content
                    else contextlib.nullcontext()
                )
                with patch.object(
                    discord_reference_sidecar_module.os,
                    "link",
                    side_effect=inject_destination_then_collide,
                ), context:
                    discord_reference_sidecar_module.publish_message_reference_resolution_audit(
                        run_root=run_root,
                        audit=audit,
                    )
                self.assertTrue(injected)

    def test_reference_sidecar_fsyncs_parent_immediately_after_directory_create(
        self,
    ) -> None:
        run_root = self.workspace / "mkdir-fsync"
        run_root.mkdir()
        audit = {
            "schema_version": 1,
            "kind": "discord_message_reference_resolution_audit",
            "counts": {},
        }
        events: list[tuple[str, int | None]] = []
        real_mkdir = os.mkdir
        real_fsync = os.fsync

        def record_mkdir(
            path: object,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> None:
            events.append(("mkdir", dir_fd))
            real_mkdir(path, mode, dir_fd=dir_fd)

        def record_fsync(fd: int) -> None:
            events.append(("fsync", fd))
            real_fsync(fd)

        with patch.object(
            discord_reference_sidecar_module.os,
            "mkdir",
            side_effect=record_mkdir,
        ), patch.object(
            discord_reference_sidecar_module.os,
            "fsync",
            side_effect=record_fsync,
        ):
            discord_reference_sidecar_module.publish_message_reference_resolution_audit(
                run_root=run_root,
                audit=audit,
            )

        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[0][0], "mkdir")
        self.assertEqual(events[1], ("fsync", events[0][1]))

    def test_reference_sidecar_rejects_synced_relevant_node_and_status_tamper(
        self,
    ) -> None:
        for case in (
            "missing_relevant_node",
            "status_and_diagnostic",
            "diagnostic_code",
        ):
            with self.subTest(case=case):
                result = self._collect(f"reference-tamper-{case}")
                checkpoint = json.loads(
                    (result.run_root / "checkpoint.json").read_text()
                )
                stream = checkpoint["streams"]["messages_300"]
                descriptor = stream["page_states"][0]["message_evidence"]
                evidence_path = result.run_root / descriptor["path"]
                rows = [
                    json.loads(line)
                    for line in evidence_path.read_text().splitlines()
                ]
                root_row = next(
                    row
                    for row in rows
                    if any(
                        node.get("kind") == "root"
                        and node.get("message_id") == "30"
                        for node in row.get("nodes", [])
                        if isinstance(node, dict)
                    )
                )
                if case == "missing_relevant_node":
                    original_nodes = list(root_row["nodes"])
                    root_row["nodes"] = [
                        node
                        for node in original_nodes
                        if node.get("kind") != "referenced_message"
                    ]
                    descriptor["nodes"] -= (
                        len(original_nodes) - len(root_row["nodes"])
                    )
                elif case == "status_and_diagnostic":
                    root_row["status"] = "complete"
                    root_row["diagnostics"] = []
                    descriptor["partial_messages"] = 0
                    descriptor["diagnostics"] = 0
                    descriptor["diagnostics_by_severity"]["error"] = 0
                else:
                    diagnostic = next(
                        item
                        for item in root_row["diagnostics"]
                        if item.get("code") == "referenced_message_unknown"
                    )
                    diagnostic["code"] = "attacker_reclassified_error"
                content = b"".join(
                    discord_collector_module._canonical_json_bytes(row)
                    for row in rows
                )
                evidence_path.write_bytes(content)
                descriptor["sha256"] = hashlib.sha256(content).hexdigest()

                with self.assertRaisesRegex(
                    ValueError,
                    "differs from raw extraction",
                ) as raised:
                    build_message_reference_resolution_audit(
                        run_root=result.run_root,
                        checkpoint=checkpoint,
                        run_id=checkpoint["run_id"],
                        request_sha256=checkpoint["request_sha256"],
                    )
                self.assertNotIn("raw-body-secret", str(raised.exception))
                self.assertNotIn("https://", str(raised.exception))

    def test_float_reply_types_fail_closed_in_reference_sidecar(self) -> None:
        messages = self._reply_fixture()
        root_nested = messages[0]["referenced_message"]
        assert isinstance(root_nested, dict)
        root_nested["type"] = 19.0
        nested_reference = root_nested["message_reference"]
        assert isinstance(nested_reference, dict)
        nested_reference["type"] = 0.0
        messages[1]["type"] = 19.0
        top_reference = messages[1]["message_reference"]
        assert isinstance(top_reference, dict)
        top_reference["type"] = 0.0

        with self.assertRaisesRegex(
            ValueError,
            "raw diagnostics do not match resolution edges",
        ):
            self._collect("reference-float-types", messages=messages)

    def test_deleted_reference_is_covered_but_absent_source_remains_unresolved(
        self,
    ) -> None:
        deleted_messages = self._reply_fixture()
        deleted_messages[1]["referenced_message"] = None
        deleted = self._collect(
            "reference-deleted",
            messages=deleted_messages,
        )
        deleted_counts = deleted.manifest[
            "message_reference_resolution_audit"
        ]["counts"]
        self.assertEqual(deleted_counts["deleted"], 1)
        self.assertEqual(deleted_counts["unresolved"], 0)
        self.assertEqual(deleted_counts["effective_errors"], 0)
        self.assertEqual(
            deleted_counts["effective_diagnostic_codes_by_severity"]["info"],
            {"referenced_message_deleted": 1},
        )
        self.assertEqual(deleted.manifest["status"], "complete")

        absent_messages = self._reply_fixture()
        del absent_messages[1]
        absent = self._collect(
            "reference-absent",
            messages=absent_messages,
        )
        absent_counts = absent.manifest[
            "message_reference_resolution_audit"
        ]["counts"]
        self.assertEqual(absent_counts["deleted"], 0)
        self.assertEqual(absent_counts["unresolved"], 1)
        self.assertEqual(absent_counts["effective_errors"], 1)
        self.assertEqual(
            absent.manifest["message_evidence"]["effective_status"],
            "partial",
        )
        self.assertEqual(absent.manifest["status"], "partial")


if __name__ == "__main__":
    unittest.main()
