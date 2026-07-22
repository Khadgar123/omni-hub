from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from typing import Any, Mapping
from unittest.mock import patch

from omni_hub.audit import AuditLogger
from omni_hub.builtins import build_default_registry
from omni_hub.cli import build_parser
from omni_hub.connectors.discord import (
    DiscordAPIError,
    rfc2544_fake_ip_media_policy_descriptor,
)
from omni_hub.discord_collector import CollectionResult
from omni_hub.discord_collector import DiscordEvidenceCollector
from omni_hub.discord_media_recovery import (
    RESOLUTION_RETRY_TRIGGER,
    MediaResolutionContext,
    validate_resolution_attempt_history,
)
from omni_hub.models import OperationResult, OperationSpec, OperationStatus, RiskLevel
from omni_hub.runner import OperationRunner
from omni_hub.discord_blogger_corpus import BloggerMessage
from omni_hub.discord_sharding import (
    canonical_json_bytes,
    canonical_json_sha256,
    target_set_sha256,
)
from omni_hub.discord_trade_events import PROFILE_CHANNELS


_TOKEN = "task-four-secret-that-must-never-leak"
_MESSAGE_BODY = "private Discord message body"
_MEDIA_URL = "https://cdn.discordapp.com/private-evidence.png"


class _RecordingRunner:
    def __init__(self) -> None:
        self.specs: list[OperationSpec] = []

    def run(self, spec: OperationSpec, *, approved: bool = False) -> OperationResult:
        del approved
        self.specs.append(spec)
        return OperationResult(
            operation_id=spec.operation_id,
            status=OperationStatus.SUCCEEDED,
            output={},
        )


class _StrictTransport:
    base_url = "https://discord.com/api/v10"
    allow_rfc2544_fake_ip = False
    rfc2544_fake_ip_policy = None

    def __init__(self, routes: Mapping[tuple[str, str], object]) -> None:
        self.routes = dict(routes)
        self.calls: list[tuple[str, dict[str, object]]] = []

    @staticmethod
    def _key(path: str, params: Mapping[str, object] | None) -> tuple[str, str]:
        return path, json.dumps(dict(params or {}), sort_keys=True)

    def get_json(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
    ) -> object:
        normalized = dict(params or {})
        self.calls.append((path, normalized))
        key = self._key(path, normalized)
        if key not in self.routes:
            raise AssertionError(f"unexpected Discord request: {path} {normalized!r}")
        return self.routes.pop(key)

    def open_byte_stream(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("assets were disabled; byte transport must not be called")

    def assert_exhausted(self, testcase: unittest.TestCase) -> None:
        testcase.assertEqual(self.routes, {})


def _route(path: str, params: Mapping[str, object] | None = None) -> tuple[str, str]:
    return path, json.dumps(dict(params or {}), sort_keys=True)


class DiscordCLIRegistrationTests(unittest.TestCase):
    def test_blogger_inventory_is_a_local_write_operation(self) -> None:
        from omni_hub.cli import discord

        runner = _RecordingRunner()
        args = build_parser().parse_args(
            [
                "discord-blogger-inventory-build",
                "--export-root", "discord-exports/v2",
                "--closure-audit", "discord-exports/v2/closure/capture/closure-audit.json",
                "--targets", "discord-exports/v2/targets/pinned.json",
                "--output", "discord-exports/v2/derivatives/inventory.json",
            ]
        )

        self.assertIn("discord-blogger-inventory-build", discord.COMMANDS)
        with contextlib.redirect_stdout(io.StringIO()):
            discord.COMMANDS["discord-blogger-inventory-build"](
                args, runner=runner, workspace=Path.cwd()
            )
        spec = runner.specs[0]
        self.assertEqual(spec.name, "discord_blogger_inventory_build")
        self.assertEqual(spec.action, "build_blogger_target_inventory")
        self.assertEqual(spec.risk_level, RiskLevel.LOCAL_WRITE)
        self.assertEqual(
            spec.payload,
            {
                "export_root": "discord-exports/v2",
                "closure_audit": "discord-exports/v2/closure/capture/closure-audit.json",
                "targets": "discord-exports/v2/targets/pinned.json",
                "output": "discord-exports/v2/derivatives/inventory.json",
            },
        )

    def test_blogger_backtest_is_a_local_write_operation_with_fixed_inputs(self) -> None:
        from omni_hub.cli import discord
        from omni_hub.discord_backtest import CURATION_MANIFEST_SHA256

        runner = _RecordingRunner()
        args = build_parser().parse_args(
            [
                "discord-blogger-backtest-run",
                "--curation-manifest", "/private/tmp/curation.json",
                "--market-root", "/Users/example/market",
                "--output-dir", "derived/backtest",
                "--max-entry-wait-minutes", "60",
                "--timeout-seconds", "9",
            ]
        )

        self.assertIn("discord-blogger-backtest-run", discord.COMMANDS)
        with contextlib.redirect_stdout(io.StringIO()):
            discord.COMMANDS["discord-blogger-backtest-run"](
                args, runner=runner, workspace=Path.cwd()
            )
        spec = runner.specs[0]
        self.assertEqual(spec.name, "discord_blogger_backtest_run")
        self.assertEqual(spec.action, "run_blogger_backtest")
        self.assertEqual(spec.risk_level, RiskLevel.LOCAL_WRITE)
        self.assertEqual(spec.payload, {
            "curation_manifest": "/private/tmp/curation.json",
            "curation_manifest_sha256": CURATION_MANIFEST_SHA256,
            "market_root": "/Users/example/market",
            "output_dir": "derived/backtest",
            "fee_bps": 4.0,
            "slippage_bps": 4.0,
            "max_entry_wait_minutes": 60,
            "timeout_seconds": 9,
        })

    def test_workspace_option_runs_blogger_build_in_an_external_workspace(self) -> None:
        from omni_hub.cli import main

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "exports").mkdir()
            (workspace / "exports" / "closure.json").write_text(
                json.dumps({
                    "audit_kind": "discord-parent-family-closure-v1",
                    "input_file_sha256": {
                        "merge_audit": "a" * 64,
                        "head_catchup": "b" * 64,
                        "census": "c" * 64,
                    },
                }),
                encoding="utf-8",
            )
            message = BloggerMessage(
                message_id="1", channel_id=PROFILE_CHANNELS["coin-chief-v1"], author_id="author",
                timestamp="2026-07-20T10:00:00+00:00", edited_timestamp=None,
                content="BTC 做多 入场 100000", reply_message_id=None,
                snapshot_ref="evidence/page#/1", snapshot_sha256="c" * 64, media_occurrence_refs=(),
            )
            with patch(
                "omni_hub.discord_blogger_corpus.iter_verified_blogger_messages",
                return_value=iter((message,)),
            ), contextlib.redirect_stdout(io.StringIO()):
                result = main([
                    "--workspace", str(workspace),
                    "discord-blogger-events-build",
                    "--export-root", "exports",
                    "--closure-audit", "exports/closure.json",
                    "--output-dir", "derived/results",
                    "--asof", "2026-07-21T00:00:00+00:00",
                ])
            self.assertEqual(result, 0)
            manifest_path = workspace / "derived/results/event-manifest.json"
            self.assertTrue(manifest_path.is_file())
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(
                manifest["provenance"]["closure_audit"]["input_file_sha256"],
                {
                    "census": "c" * 64,
                    "head_catchup": "b" * 64,
                    "merge_audit": "a" * 64,
                },
            )

    def test_blogger_build_fails_if_closure_changes_after_consumption(self) -> None:
        from omni_hub.cli import main

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            exports = workspace / "exports"
            exports.mkdir()
            closure = exports / "closure.json"
            closure.write_text(
                json.dumps({
                    "audit_kind": "discord-parent-family-closure-v1",
                    "input_file_sha256": {
                        "merge_audit": "a" * 64,
                        "head_catchup": "b" * 64,
                        "census": "c" * 64,
                    },
                }),
                encoding="utf-8",
            )
            message = BloggerMessage(
                message_id="1", channel_id=PROFILE_CHANNELS["coin-chief-v1"], author_id="author",
                timestamp="2026-07-20T10:00:00+00:00", edited_timestamp=None,
                content="BTC 做多 入场 100000", reply_message_id=None,
                snapshot_ref="evidence/page#/1", snapshot_sha256="c" * 64, media_occurrence_refs=(),
            )

            def consume_then_mutate(**kwargs: object):
                self.assertEqual(kwargs["expected_closure_sha256"], __import__("hashlib").sha256(closure.read_bytes()).hexdigest())
                yield message
                closure.write_text(
                    json.dumps({
                        "audit_kind": "discord-parent-family-closure-v1",
                        "input_file_sha256": {
                            "merge_audit": "a" * 64,
                            "head_catchup": "b" * 64,
                            "census": "c" * 64,
                        },
                        "changed": True,
                    }),
                    encoding="utf-8",
                )

            with patch(
                "omni_hub.discord_blogger_corpus.iter_verified_blogger_messages",
                side_effect=consume_then_mutate,
            ), contextlib.redirect_stdout(io.StringIO()):
                result = main([
                    "--workspace", str(workspace),
                    "discord-blogger-events-build",
                    "--export-root", "exports",
                    "--closure-audit", "exports/closure.json",
                    "--output-dir", "derived/results",
                    "--asof", "2026-07-21T00:00:00+00:00",
                ])
            self.assertEqual(result, 1)
            self.assertFalse((workspace / "derived/results").exists())

    def test_blogger_events_build_is_a_local_write_operation(self) -> None:
        from omni_hub.cli import discord

        runner = _RecordingRunner()
        args = build_parser().parse_args(
            [
                "discord-blogger-events-build",
                "--export-root", "exports",
                "--closure-audit", "exports/closure.json",
                "--output-dir", "derived/results",
                "--asof", "2026-07-21T00:00:00+00:00",
            ]
        )
        self.assertIn("discord-blogger-events-build", discord.COMMANDS)
        with contextlib.redirect_stdout(io.StringIO()):
            discord.COMMANDS["discord-blogger-events-build"](
                args, runner=runner, workspace=Path.cwd()
            )
        spec = runner.specs[0]
        self.assertEqual(spec.name, "discord_blogger_events_build")
        self.assertEqual(spec.risk_level, RiskLevel.LOCAL_WRITE)
        self.assertEqual(spec.payload["closure_audit"], "exports/closure.json")
        self.assertEqual(spec.payload["asof"], "2026-07-21T00:00:00+00:00")

    def test_parser_and_commands_dispatch_with_exact_risks(self) -> None:
        from omni_hub.cli import discord

        runner = _RecordingRunner()
        probe_args = build_parser().parse_args(
            [
                "discord-probe",
                "--guild-id",
                "1",
                "--channel-id",
                "100",
                "--token-file",
                "/private/token-path",
            ]
        )
        collect_args = build_parser().parse_args(
            [
                "discord-collect",
                "--targets",
                "targets.json",
                "--output-dir",
                "exports",
                "--run-id",
                "resume-1",
                "--max-pages",
                "2",
                "--no-assets",
                "--allow-rfc2544-fake-ip",
                "--token-file",
                "/private/token-path",
            ]
        )

        self.assertIn("discord-probe", discord.COMMANDS)
        self.assertIn("discord-collect", discord.COMMANDS)
        with contextlib.redirect_stdout(io.StringIO()):
            discord.COMMANDS["discord-probe"](
                probe_args, runner=runner, workspace=Path.cwd()
            )
            discord.COMMANDS["discord-collect"](
                collect_args, runner=runner, workspace=Path.cwd()
            )

        probe, collect = runner.specs
        self.assertEqual(probe.name, "discord_probe")
        self.assertEqual(probe.risk_level, RiskLevel.READ_ONLY)
        self.assertEqual(collect.name, "discord_collect")
        self.assertEqual(collect.risk_level, RiskLevel.LOCAL_WRITE)
        self.assertEqual(collect.payload["run_id"], "resume-1")
        self.assertEqual(collect.payload["max_pages"], 2)
        self.assertFalse(collect.payload["download_assets"])
        self.assertTrue(collect.payload["allow_rfc2544_fake_ip"])
        self.assertEqual(
            collect.payload["rfc2544_fake_ip_policy"],
            rfc2544_fake_ip_media_policy_descriptor(),
        )
        self.assertNotIn(_TOKEN, repr(probe.payload))
        self.assertNotIn(_TOKEN, repr(collect.payload))

    def test_default_registry_exposes_both_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            names = build_default_registry(Path(temporary_directory)).list_names()

        self.assertIn("discord_probe", names)
        self.assertIn("discord_collect", names)
        self.assertIn("discord_blogger_events_build", names)
        self.assertIn("discord_blogger_backtest_run", names)
        self.assertIn("discord_blogger_inventory_build", names)


class DiscordOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_directory.name)
        self.token_file = self.workspace / "bot-token"
        self.token_file.write_text(_TOKEN + "\n", encoding="utf-8")
        self.token_file.chmod(0o600)
        self.audit_path = self.workspace / ".omni" / "audit" / "events.jsonl"

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def _runner(self) -> OperationRunner:
        return OperationRunner(
            build_default_registry(self.workspace),
            audit=AuditLogger(self.audit_path),
        )

    def test_blogger_inventory_handler_parses_private_family_blocker(self) -> None:
        target_id = "1514503993567744030"
        census_only_target_id = "1514503993567744031"
        export_root = self.workspace / "exports"
        target_relative = Path("targets/pinned.json")
        closure_relative = Path("closure/example/capture/closure-audit.json")
        merge_relative = Path("closure/example/merge-audit.json")
        target_snapshot = {
            "schema_version": 1,
            "guild_id": "1427104065959231640",
            "target_count": 2,
            "target_set_sha256": target_set_sha256(
                [target_id, census_only_target_id]
            ),
            "targets": [
                {
                    "id": target_id,
                    "name": "forum",
                    "kind": "GUILD_FORUM (15)",
                    "parent_id": None,
                    "source_labels": ["forum"],
                },
                {
                    "id": census_only_target_id,
                    "name": "census-only-forum",
                    "kind": "GUILD_FORUM (15)",
                    "parent_id": None,
                    "source_labels": ["census-only-forum"],
                },
            ],
        }
        target_bytes = canonical_json_bytes(target_snapshot)
        target_path = export_root / target_relative
        target_path.parent.mkdir(parents=True)
        target_path.write_bytes(target_bytes)
        merge = {
            "audit_kind": "discord-parent-family-merge-v1",
            "parent_snapshot_file_sha256": hashlib.sha256(target_bytes).hexdigest(),
            "parent_snapshot_sha256": canonical_json_sha256(target_snapshot),
            "parent_target_set_sha256": target_snapshot["target_set_sha256"],
            "static_target_ids": [target_id, census_only_target_id],
            "message_bearing_static_target_ids": [
                target_id,
                census_only_target_id,
            ],
            "required_head_catchup_target_ids": [
                target_id,
                census_only_target_id,
            ],
            "discovered_threads": [],
            "thread_parent_static_target_ids": [
                target_id,
                census_only_target_id,
            ],
            "private_archived_blocked_streams": [
                {
                    "index": 1,
                    "status": "blocked",
                    "stream": f"threads_{target_id}_private_archived",
                    "terminal_reason": "http_403",
                }
            ],
            "private_archived_incomplete_streams": [
                {
                    "index": 1,
                    "status": "blocked",
                    "stream": f"threads_{target_id}_private_archived",
                    "terminal_reason": "http_403",
                }
            ],
        }
        merge_bytes = canonical_json_bytes(merge)
        merge_path = export_root / merge_relative
        merge_path.parent.mkdir(parents=True)
        merge_path.write_bytes(merge_bytes)
        closure = {
            "audit_kind": "discord-parent-family-closure-v1",
            "input_file_sha256": {
                "merge_audit": hashlib.sha256(merge_bytes).hexdigest()
            },
            "authorized_scope_point_in_time_complete": False,
            "full_private_scope_point_in_time_complete": False,
            "private_archived_incomplete_count": 1,
            "private_archived_blocked_count": 2,
            "limitations": {
                "census_private_archived_403_parent_ids": [
                    census_only_target_id
                ]
            },
        }
        closure_path = export_root / closure_relative
        closure_path.parent.mkdir(parents=True)
        closure_path.write_bytes(canonical_json_bytes(closure))

        with patch(
            "omni_hub.discord_blogger_corpus.iter_verified_blogger_messages",
            return_value=iter(()),
        ):
            result = self._runner().run(
                OperationSpec(
                    name="discord_blogger_inventory_build",
                    action="build_blogger_target_inventory",
                    connector="discord",
                    payload={
                        "export_root": "exports",
                        "closure_audit": (Path("exports") / closure_relative).as_posix(),
                        "targets": (Path("exports") / target_relative).as_posix(),
                        "output": "derived/inventory.json",
                    },
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )

        self.assertEqual(result.status, OperationStatus.SUCCEEDED)
        inventory = json.loads((self.workspace / "derived/inventory.json").read_text())
        self.assertEqual(
            {row["count_semantics"] for row in inventory["targets"]},
            {"family_rollup"},
        )
        self.assertEqual(
            {row["scope_completeness"] for row in inventory["targets"]},
            {"known_scope_only"},
        )
        self.assertEqual(inventory["private_archived_blocked_parent_count"], 2)

        merge["private_archived_incomplete_streams"][0]["status"] = (
            "in_progress"
        )
        merge["private_archived_incomplete_streams"][0]["terminal_reason"] = (
            "network_error"
        )
        merge_bytes = canonical_json_bytes(merge)
        merge_path.write_bytes(merge_bytes)
        closure["input_file_sha256"]["merge_audit"] = hashlib.sha256(
            merge_bytes
        ).hexdigest()
        closure_path.write_bytes(canonical_json_bytes(closure))
        with patch(
            "omni_hub.discord_blogger_corpus.iter_verified_blogger_messages",
            return_value=iter(()),
        ):
            rejected = self._runner().run(
                OperationSpec(
                    name="discord_blogger_inventory_build",
                    action="build_blogger_target_inventory",
                    connector="discord",
                    payload={
                        "export_root": "exports",
                        "closure_audit": (
                            Path("exports") / closure_relative
                        ).as_posix(),
                        "targets": (
                            Path("exports") / target_relative
                        ).as_posix(),
                        "output": "derived/invalid-incomplete.json",
                    },
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )

        self.assertEqual(rejected.status, OperationStatus.FAILED)
        self.assertFalse(
            (self.workspace / "derived/invalid-incomplete.json").exists()
        )

    def test_probe_calls_inventory_and_one_channel_page_without_raw_output(self) -> None:
        transport = _StrictTransport(
            {
                _route("/users/@me"): {"id": "9", "username": "secret-name"},
                _route("/guilds/1"): {"id": "1", "name": "private guild"},
                _route("/guilds/1/channels"): [
                    {"id": "100", "type": 0, "name": "private-channel"}
                ],
                _route("/guilds/1/threads/active"): {
                    "threads": [
                        {
                            "id": "200",
                            "type": 11,
                            "guild_id": "1",
                            "parent_id": "100",
                        }
                    ],
                    "members": [],
                },
                _route("/channels/100/messages", {"limit": 100}): [
                    {
                        "id": "300",
                        "content": _MESSAGE_BODY,
                        "attachments": [{"url": _MEDIA_URL}],
                    }
                ],
                _route("/channels/100/messages/pins", {"limit": 50}): {
                    "items": [
                        {
                            "pinned_at": "2026-07-19T00:00:00+00:00",
                            "message": {"id": "300", "content": _MESSAGE_BODY},
                        }
                    ],
                    "has_more": False,
                },
            }
        )
        with patch(
            "omni_hub.connectors.discord.DiscordHTTPTransport",
            return_value=transport,
        ):
            result = self._runner().run(
                OperationSpec(
                    name="discord_probe",
                    action="probe",
                    connector="discord",
                    payload={
                        "guild_id": "1",
                        "channel_id": "100",
                        "token_file": str(self.token_file),
                    },
                    risk_level=RiskLevel.READ_ONLY,
                )
            )

        self.assertEqual(result.status, OperationStatus.SUCCEEDED)
        self.assertEqual(
            result.output,
            {
                "status": "ok",
                "identity_id": "9",
                "guild_id": "1",
                "guild_accessible": True,
                "channel_count": 1,
                "active_thread_count": 1,
                "channel_id": "100",
                "channel_found": True,
                "message_count": 1,
                "message_body_visible": True,
                "pins_shape_valid": True,
                "pin_count": 1,
            },
        )
        serialized = repr(result.to_dict())
        self.assertNotIn(_MESSAGE_BODY, serialized)
        self.assertNotIn(_MEDIA_URL, serialized)
        self.assertNotIn(_TOKEN, serialized)
        transport.assert_exhausted(self)

    def test_probe_rejects_unknown_or_foreign_channel_before_channel_requests(self) -> None:
        cases = [
            (
                "empty graph",
                [],
                {"threads": [], "members": []},
            ),
            (
                "foreign active thread",
                [{"id": "100", "type": 0, "guild_id": "1"}],
                {
                    "threads": [
                        {
                            "id": "999",
                            "type": 11,
                            "guild_id": "2",
                            "parent_id": "100",
                        }
                    ],
                    "members": [],
                },
            ),
            (
                "orphan active thread",
                [{"id": "100", "type": 0, "guild_id": "1"}],
                {
                    "threads": [
                        {
                            "id": "999",
                            "type": 11,
                            "guild_id": "1",
                            "parent_id": "777",
                        }
                    ],
                    "members": [],
                },
            ),
        ]
        for label, channels, active in cases:
            with self.subTest(label=label):
                transport = _StrictTransport(
                    {
                        _route("/users/@me"): {"id": "9"},
                        _route("/guilds/1"): {"id": "1"},
                        _route("/guilds/1/channels"): channels,
                        _route("/guilds/1/threads/active"): active,
                    }
                )
                with patch(
                    "omni_hub.connectors.discord.DiscordHTTPTransport",
                    return_value=transport,
                ):
                    result = self._runner().run(
                        OperationSpec(
                            name="discord_probe",
                            action="probe",
                            connector="discord",
                            payload={
                                "guild_id": "1",
                                "channel_id": "999",
                                "token_file": str(self.token_file),
                            },
                            risk_level=RiskLevel.READ_ONLY,
                        )
                    )

                self.assertEqual(result.status, OperationStatus.FAILED)
                self.assertIn("not found", str(result.error).lower())
                self.assertFalse(
                    any(path.startswith("/channels/") for path, _params in transport.calls)
                )
                transport.assert_exhausted(self)

    def test_collect_uses_current_collector_and_returns_only_artifact_summary(self) -> None:
        targets = self.workspace / "targets.json"
        targets.write_text(
            json.dumps(
                {
                    "guild_id": "1",
                    "targets": [
                        {
                            "id": "100",
                            "kind": "GUILD_TEXT (0)",
                            "name": "private-channel",
                            "parent_id": None,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        transport = _StrictTransport(
            {
                _route("/users/@me"): {"id": "9"},
                _route("/guilds/1"): {"id": "1"},
                _route("/guilds/1/channels"): [
                    {"id": "100", "guild_id": "1", "type": 0, "name": "private"}
                ],
                _route("/guilds/1/threads/active"): {
                    "threads": [],
                    "members": [],
                },
                _route(
                    "/channels/100/threads/archived/public", {"limit": 100}
                ): {"threads": [], "members": [], "has_more": False},
                _route(
                    "/channels/100/threads/archived/private", {"limit": 100}
                ): {"threads": [], "members": [], "has_more": False},
                _route(
                    "/channels/100/users/@me/threads/archived/private",
                    {"limit": 100},
                ): {"threads": [], "members": [], "has_more": False},
                _route("/channels/100/messages", {"limit": 100}): [
                    {
                        "id": "300",
                        "channel_id": "100",
                        "timestamp": "2015-01-01T00:00:00+00:00",
                        "edited_timestamp": None,
                        "author": {
                            "id": "9",
                            "username": "fixture-author",
                            "bot": False,
                        },
                        "content": _MESSAGE_BODY,
                        "attachments": [{"id": "400", "url": _MEDIA_URL}],
                        "embeds": [],
                        "components": [],
                    }
                ],
                _route(
                    "/channels/100/messages", {"before": "300", "limit": 100}
                ): [],
                _route("/channels/100/messages/pins", {"limit": 50}): {
                    "items": [],
                    "has_more": False,
                },
            }
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch(
            "omni_hub.connectors.discord.DiscordHTTPTransport",
            return_value=transport,
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = self._runner().run(
                OperationSpec(
                    name="discord_collect",
                    action="collect",
                    connector="discord",
                    payload={
                        "targets": "targets.json",
                        "output_dir": "exports",
                        "run_id": "resume-1",
                        "max_pages": None,
                        "download_assets": False,
                        "token_file": str(self.token_file),
                    },
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )

        self.assertEqual(result.status, OperationStatus.SUCCEEDED)
        self.assertEqual(result.output["status"], "partial")
        self.assertEqual(result.output["run_root"], "exports/runs/resume-1")
        self.assertEqual(
            result.output["manifest_path"], "exports/runs/resume-1/manifest.json"
        )
        self.assertEqual(
            result.output["checkpoint_path"], "exports/runs/resume-1/checkpoint.json"
        )
        self.assertGreater(result.output["stream_count"], 0)
        self.assertEqual(result.output["media_count"], 1)
        self.assertEqual(result.output["media_failed_count"], 0)
        self.assertEqual(result.output["error_count"], 0)
        serialized_result = repr(result.to_dict())
        for forbidden in (_TOKEN, _MESSAGE_BODY, _MEDIA_URL):
            self.assertNotIn(forbidden, serialized_result)
        transport.assert_exhausted(self)

        generated = [
            path
            for path in self.workspace.rglob("*")
            if path.is_file() and path.suffix in {".json", ".jsonl"}
        ]
        self.assertTrue(generated)
        all_json = "\n".join(path.read_text(encoding="utf-8") for path in generated)
        self.assertNotIn(_TOKEN, all_json)
        self.assertNotIn(_TOKEN, stdout.getvalue())
        self.assertNotIn(_TOKEN, stderr.getvalue())

    def test_collector_validation_error_is_redacted_in_runner_and_audit(self) -> None:
        targets = self.workspace / "targets.json"
        targets.write_text(
            json.dumps(
                {
                    "guild_id": "1",
                    "targets": [
                        {
                            "id": "100",
                            "kind": "GUILD_TEXT (0)",
                            "name": "synthetic",
                            "parent_id": None,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        signed_url = (
            "https://media.discordapp.net/external/item"
            "?expiry_key=synthetic-expiry&signature_key=synthetic-signature"
        )
        context = MediaResolutionContext(
            request_sha256="a" * 64,
            allow_rfc2544_fake_ip=False,
            policy_inputs_sha256=None,
            policy_descriptor=None,
        )

        def fail_with_sequence_gap(
            _collector: DiscordEvidenceCollector,
            **_kwargs: object,
        ) -> object:
            base = {
                "url": signed_url,
                "status": "failed",
                "http_content_type": None,
                "http_content_length": None,
                "actual_bytes": 0,
                "sha256": None,
                "blob_path": None,
                "failure_detail": "resolver_timeout",
                "policy_inputs_sha256": None,
            }
            first = {
                **base,
                "terminal_reason": "media_resolution_failed_transient",
                "resolution_retry_sequence": 1,
            }
            third = {
                **base,
                "terminal_reason": "media_resolution_retry_exhausted",
                "resolution_retry_sequence": 3,
                "retry_trigger": RESOLUTION_RETRY_TRIGGER,
                "retry_of_attempt_number": 1,
            }
            record = {
                **third,
                "attempt_history": [first, third],
            }
            validate_resolution_attempt_history(record, context=context)
            raise AssertionError("sequence gap must fail")

        transport = _StrictTransport({})
        with patch(
            "omni_hub.connectors.discord.DiscordHTTPTransport",
            return_value=transport,
        ), patch.object(
            DiscordEvidenceCollector,
            "collect",
            autospec=True,
            side_effect=fail_with_sequence_gap,
        ):
            result = self._runner().run(
                OperationSpec(
                    name="discord_collect",
                    action="collect",
                    connector="discord",
                    payload={
                        "targets": "targets.json",
                        "output_dir": "exports",
                        "run_id": "redacted-validation",
                        "download_assets": True,
                        "token_file": str(self.token_file),
                    },
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )

        self.assertEqual(result.status, OperationStatus.FAILED)
        persisted_audit = self.audit_path.read_text(encoding="utf-8")
        for serialized in (str(result.error), persisted_audit):
            for forbidden in (
                signed_url,
                "expiry_key",
                "synthetic-expiry",
                "signature_key",
                "synthetic-signature",
                "Authorization",
                _TOKEN,
            ):
                self.assertNotIn(forbidden, serialized)

    def test_path_escapes_fail_without_network_or_outside_write(self) -> None:
        outside = self.workspace.parent / f"outside-{os.getpid()}"
        cases = [
            {"targets": str(outside / "targets.json"), "output_dir": "exports"},
            {"targets": "../targets.json", "output_dir": "exports"},
            {"targets": "targets.json", "output_dir": "../outside"},
            {"targets": "targets.json", "output_dir": str(outside)},
        ]
        (self.workspace / "targets.json").write_text(
            json.dumps(
                {
                    "guild_id": "1",
                    "targets": [
                        {"id": "100", "kind": "text", "name": "target"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        transport = _StrictTransport({})
        with patch(
            "omni_hub.connectors.discord.DiscordHTTPTransport",
            return_value=transport,
        ):
            for payload in cases:
                with self.subTest(payload=payload):
                    result = self._runner().run(
                        OperationSpec(
                            name="discord_collect",
                            action="collect",
                            connector="discord",
                            payload={
                                **payload,
                                "run_id": "safe-run",
                                "download_assets": False,
                                "token_file": str(self.token_file),
                            },
                            risk_level=RiskLevel.LOCAL_WRITE,
                        )
                    )
                    self.assertEqual(result.status, OperationStatus.FAILED)
        self.assertEqual(transport.calls, [])
        self.assertFalse(outside.exists())

    def test_collect_rejects_non_integer_asset_limit_before_token_or_network(self) -> None:
        transport = _StrictTransport({})
        with patch(
            "omni_hub.connectors.discord.read_bot_token"
        ) as read_token, patch(
            "omni_hub.connectors.discord.DiscordHTTPTransport",
            return_value=transport,
        ) as transport_factory:
            for value in (True, 1.5, "7", 0, -1):
                with self.subTest(value=value):
                    result = self._runner().run(
                        OperationSpec(
                            name="discord_collect",
                            action="collect",
                            connector="discord",
                            payload={
                                "targets": "targets.json",
                                "output_dir": "exports",
                                "run_id": "invalid-asset-limit",
                                "download_assets": False,
                                "max_asset_bytes": value,
                                "token_file": str(self.token_file),
                            },
                            risk_level=RiskLevel.LOCAL_WRITE,
                        )
                    )
                    self.assertEqual(result.status, OperationStatus.FAILED)

        read_token.assert_not_called()
        transport_factory.assert_not_called()
        self.assertEqual(transport.calls, [])

    def test_target_symlink_and_unsafe_run_id_fail_without_network(self) -> None:
        real_target = self.workspace / "real-target.json"
        real_target.write_text(
            json.dumps(
                {
                    "guild_id": "1",
                    "targets": [{"id": "100", "kind": "text", "name": "target"}],
                }
            ),
            encoding="utf-8",
        )
        target_link = self.workspace / "target-link.json"
        target_link.symlink_to(real_target)
        transport = _StrictTransport({})
        with patch(
            "omni_hub.connectors.discord.DiscordHTTPTransport",
            return_value=transport,
        ):
            for targets, run_id in (("target-link.json", "safe"), ("real-target.json", "../bad")):
                with self.subTest(targets=targets, run_id=run_id):
                    result = self._runner().run(
                        OperationSpec(
                            name="discord_collect",
                            action="collect",
                            connector="discord",
                            payload={
                                "targets": targets,
                                "output_dir": "exports",
                                "run_id": run_id,
                                "download_assets": False,
                                "token_file": str(self.token_file),
                            },
                            risk_level=RiskLevel.LOCAL_WRITE,
                        )
                    )
                    self.assertEqual(result.status, OperationStatus.FAILED)
        self.assertEqual(transport.calls, [])

    def test_output_symlink_escape_fails_without_network_or_outside_write(self) -> None:
        (self.workspace / "targets.json").write_text(
            json.dumps(
                {
                    "guild_id": "1",
                    "targets": [{"id": "100", "kind": "text", "name": "target"}],
                }
            ),
            encoding="utf-8",
        )
        with tempfile.TemporaryDirectory() as outside_directory:
            outside = Path(outside_directory)
            (self.workspace / "exports-link").symlink_to(outside, target_is_directory=True)
            transport = _StrictTransport({})
            with patch(
                "omni_hub.connectors.discord.DiscordHTTPTransport",
                return_value=transport,
            ):
                result = self._runner().run(
                    OperationSpec(
                        name="discord_collect",
                        action="collect",
                        connector="discord",
                        payload={
                            "targets": "targets.json",
                            "output_dir": "exports-link",
                            "run_id": "safe-run",
                            "download_assets": False,
                            "token_file": str(self.token_file),
                        },
                        risk_level=RiskLevel.LOCAL_WRITE,
                    )
                )

            self.assertEqual(result.status, OperationStatus.FAILED)
            self.assertEqual(transport.calls, [])
            self.assertEqual(list(outside.iterdir()), [])

    def test_malformed_target_snapshot_fails_before_network(self) -> None:
        (self.workspace / "malformed.json").write_text(
            '{"guild_id":"not-a-snowflake","targets":[]}',
            encoding="utf-8",
        )
        transport = _StrictTransport({})
        with patch(
            "omni_hub.connectors.discord.DiscordHTTPTransport",
            return_value=transport,
        ):
            result = self._runner().run(
                OperationSpec(
                    name="discord_collect",
                    action="collect",
                    connector="discord",
                    payload={
                        "targets": "malformed.json",
                        "output_dir": "exports",
                        "run_id": "safe-run",
                        "download_assets": False,
                        "token_file": str(self.token_file),
                    },
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )

        self.assertEqual(result.status, OperationStatus.FAILED)
        self.assertIn("guild_id", str(result.error))
        self.assertEqual(transport.calls, [])

    def test_contained_symlink_components_fail_before_token_or_transport(self) -> None:
        real_targets = self.workspace / "real-targets"
        real_targets.mkdir()
        (real_targets / "targets.json").write_text(
            json.dumps(
                {
                    "guild_id": "1",
                    "targets": [{"id": "100", "kind": "text", "name": "target"}],
                }
            ),
            encoding="utf-8",
        )
        (self.workspace / "target-parent-link").symlink_to(
            real_targets, target_is_directory=True
        )

        real_output = self.workspace / "real-output"
        real_output.mkdir()
        (self.workspace / "output-link").symlink_to(
            real_output, target_is_directory=True
        )
        real_parent = self.workspace / "real-parent"
        real_parent.mkdir()
        (self.workspace / "output-parent-link").symlink_to(
            real_parent, target_is_directory=True
        )

        cases = [
            ("target parent", "target-parent-link/targets.json", "exports"),
            ("output final", "real-targets/targets.json", "output-link"),
            (
                "output parent",
                "real-targets/targets.json",
                "output-parent-link/new-child",
            ),
        ]
        for label, targets, output_dir in cases:
            with self.subTest(label=label), patch(
                "omni_hub.connectors.discord.read_bot_token",
                side_effect=AssertionError("token read occurred before path rejection"),
            ) as token_reader, patch(
                "omni_hub.connectors.discord.DiscordHTTPTransport",
                side_effect=AssertionError("transport constructed before path rejection"),
            ) as transport_class:
                result = self._runner().run(
                    OperationSpec(
                        name="discord_collect",
                        action="collect",
                        connector="discord",
                        payload={
                            "targets": targets,
                            "output_dir": output_dir,
                            "run_id": "safe-run",
                            "download_assets": False,
                            "token_file": str(self.token_file),
                        },
                        risk_level=RiskLevel.LOCAL_WRITE,
                    )
                )

            self.assertEqual(result.status, OperationStatus.FAILED)
            self.assertIn("symbolic link", str(result.error).lower())
            token_reader.assert_not_called()
            transport_class.assert_not_called()

    def test_collect_preflight_allows_dot_and_normal_subdirectories(self) -> None:
        targets_dir = self.workspace / "targets"
        targets_dir.mkdir()
        target_path = targets_dir / "snapshot.json"
        target_path.write_text(
            json.dumps(
                {
                    "guild_id": "1",
                    "targets": [{"id": "100", "kind": "text", "name": "target"}],
                }
            ),
            encoding="utf-8",
        )

        class StopAfterPreflight:
            def __init__(self, _token: str, **_kwargs: object) -> None:
                raise AssertionError("transport reached after successful preflight")

        for output_dir in (".", "normal/new-output"):
            with self.subTest(output_dir=output_dir), patch(
                "omni_hub.connectors.discord.DiscordHTTPTransport",
                StopAfterPreflight,
            ):
                result = self._runner().run(
                    OperationSpec(
                        name="discord_collect",
                        action="collect",
                        connector="discord",
                        payload={
                            "targets": "targets/snapshot.json",
                            "output_dir": output_dir,
                            "run_id": "safe-run",
                            "download_assets": False,
                            "token_file": str(self.token_file),
                        },
                        risk_level=RiskLevel.LOCAL_WRITE,
                    )
                )

            self.assertEqual(result.status, OperationStatus.FAILED)
            self.assertIn("successful preflight", str(result.error))

    def test_collect_generates_safe_run_id_inside_handler(self) -> None:
        captured: dict[str, object] = {}
        (self.workspace / "targets.json").write_text(
            json.dumps(
                {
                    "guild_id": "1",
                    "targets": [{"id": "100", "kind": "text", "name": "target"}],
                }
            ),
            encoding="utf-8",
        )

        class FakeCollector:
            def __init__(self, json_transport: object, **kwargs: object) -> None:
                captured["json_transport"] = json_transport
                captured.update(kwargs)

            def collect(self, **kwargs: object) -> CollectionResult:
                captured.update(kwargs)
                run_root = self_workspace / "exports" / "runs" / str(kwargs["run_id"])
                run_root.mkdir(parents=True)
                (run_root / "manifest.json").write_text("{}", encoding="utf-8")
                (run_root / "checkpoint.json").write_text("{}", encoding="utf-8")
                return CollectionResult(
                    run_root=run_root,
                    manifest={
                        "status": "complete",
                        "streams": {},
                        "media": {"records": 0, "complete": 0, "failed": 0},
                        "errors": 0,
                    },
                )

        self_workspace = self.workspace
        transport = _StrictTransport({})
        with patch(
            "omni_hub.connectors.discord.DiscordHTTPTransport",
            return_value=transport,
        ) as transport_factory, patch(
            "omni_hub.discord_collector.DiscordEvidenceCollector",
            FakeCollector,
        ):
            result = self._runner().run(
                OperationSpec(
                    name="discord_collect",
                    action="collect",
                    connector="discord",
                    payload={
                        "targets": "targets.json",
                        "output_dir": "exports",
                        "run_id": None,
                        "download_assets": False,
                        "allow_rfc2544_fake_ip": True,
                        "rfc2544_fake_ip_policy": (
                            rfc2544_fake_ip_media_policy_descriptor()
                        ),
                        "token_file": str(self.token_file),
                    },
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )

        self.assertEqual(result.status, OperationStatus.SUCCEEDED)
        generated_run_id = str(captured["run_id"])
        self.assertRegex(generated_run_id, r"^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{16}$")
        self.assertIs(captured["json_transport"], transport)
        self.assertIs(captured["byte_transport"], transport)
        self.assertTrue(captured["allow_rfc2544_fake_ip"])
        self.assertTrue(transport_factory.call_args.kwargs["allow_rfc2544_fake_ip"])
        self.assertNotIn(_TOKEN, repr(captured))

    def test_401_is_fatal_clear_and_redacted_from_audit(self) -> None:
        class UnauthorizedTransport:
            def get_json(self, path: str, params: object = None) -> object:
                del params
                raise DiscordAPIError(
                    f"invalid bot token {_TOKEN}",
                    status_code=401,
                    path=path,
                )

        with patch(
            "omni_hub.connectors.discord.DiscordHTTPTransport",
            return_value=UnauthorizedTransport(),
        ):
            result = self._runner().run(
                OperationSpec(
                    name="discord_probe",
                    action="probe",
                    connector="discord",
                    payload={"guild_id": "1", "token_file": str(self.token_file)},
                    risk_level=RiskLevel.READ_ONLY,
                )
            )

        self.assertEqual(result.status, OperationStatus.FAILED)
        self.assertIn("invalid bot token", str(result.error).lower())
        self.assertNotIn(_TOKEN, repr(result))
        self.assertNotIn(_TOKEN, self.audit_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
