from __future__ import annotations

from datetime import UTC, datetime
import unittest

from omni_hub.discord_message_evidence import extract_message_evidence


DISCORD_EPOCH_MS = 1_420_070_400_000


def snowflake_at(timestamp: str, increment: int = 0) -> str:
    instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    milliseconds = int(instant.timestamp() * 1000)
    return str(((milliseconds - DISCORD_EPOCH_MS) << 22) | increment)


def full_message(
    timestamp: str,
    *,
    increment: int = 0,
    channel_id: str = "100",
    author: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": snowflake_at(timestamp, increment),
        "channel_id": channel_id,
        "timestamp": timestamp,
        "edited_timestamp": None,
        "author": author or {"id": "300", "username": "analyst", "bot": False},
        "content": "",
        "attachments": [],
        "embeds": [],
        "components": [],
    }


class DiscordMessageEvidenceTests(unittest.TestCase):
    def test_top_level_media_references_provenance_and_legacy_keys(self) -> None:
        message = full_message("2026-01-01T00:00:00.123000+00:00")
        message_id = str(message["id"])
        message.update(
            {
                "content": "plan https://example.com/chart?x=1.",
                "attachments": [
                    {
                        "id": "400",
                        "filename": "chart.png",
                        "size": 4,
                        "content_type": "image/png",
                        "url": "https://cdn.example/chart.png",
                        "proxy_url": "https://proxy.example/chart.png",
                    }
                ],
                "embeds": [
                    {
                        "url": "https://example.com/article",
                        "provider": {"url": "https://provider.example"},
                        "author": {
                            "url": "https://author.example",
                            "icon_url": "https://cdn.example/author.png",
                            "proxy_icon_url": "https://proxy.example/author.png",
                        },
                        "footer": {
                            "icon_url": "https://cdn.example/footer.png",
                            "proxy_icon_url": "https://proxy.example/footer.png",
                        },
                        "image": {"url": "https://cdn.example/image.png"},
                        "thumbnail": {"url": "https://cdn.example/thumb.png"},
                        "video": {"url": "https://cdn.example/video.mp4"},
                    }
                ],
            }
        )

        evidence = extract_message_evidence(
            message,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
            evidence_sha256="a" * 64,
            json_pointer="/payload/0",
        )

        self.assertEqual(evidence.status, "complete")
        self.assertEqual(evidence.nodes[0].attribution.kind, "human_candidate")
        self.assertEqual(evidence.nodes[0].timestamp.status, "valid")
        media_by_field = {item.field: item for item in evidence.media}
        self.assertEqual(
            media_by_field["attachment"].logical_key,
            f"{message_id}:attachment:400",
        )
        self.assertEqual(
            media_by_field["image"].logical_key,
            f"{message_id}:embed:0:image",
        )
        self.assertEqual(
            media_by_field["thumbnail"].logical_key,
            f"{message_id}:embed:0:thumbnail",
        )
        self.assertEqual(
            media_by_field["video"].logical_key,
            f"{message_id}:embed:0:video",
        )
        self.assertEqual(
            media_by_field["author_icon"].json_pointer,
            "/payload/0/embeds/0/author/icon_url",
        )
        self.assertEqual(
            media_by_field["footer_icon"].source.evidence_sha256,
            "a" * 64,
        )
        references = {(item.kind, item.value) for item in evidence.references}
        self.assertIn(("content_url", "https://example.com/chart?x=1"), references)
        self.assertIn(("embed_link", "https://example.com/article"), references)
        self.assertIn(("embed_link", "https://provider.example"), references)
        self.assertIn(("embed_link", "https://author.example"), references)
        content_ref = next(
            item for item in evidence.references if item.kind == "content_url"
        )
        self.assertEqual(content_ref.json_pointer, "/payload/0/content")
        self.assertEqual(content_ref.source.stream, "messages_100")

    def test_referenced_message_and_idless_snapshot_keep_separate_attribution(self) -> None:
        outer = full_message(
            "2026-01-02T00:00:00.123000+00:00",
            author={"id": "700", "username": "delivery-hook"},
        )
        outer["webhook_id"] = "700"
        referenced = full_message(
            "2026-01-01T23:59:00.123000+00:00",
            author={"id": "301", "username": "signal-bot", "bot": True},
        )
        referenced_id = str(referenced["id"])
        referenced["attachments"] = [
            {
                "id": "401",
                "filename": "reference.png",
                "url": "https://cdn.example/reference.png",
            }
        ]
        outer["message_reference"] = {
            "type": 1,
            "message_id": referenced_id,
            "channel_id": "100",
        }
        outer["referenced_message"] = referenced
        outer["message_snapshots"] = [
            {
                "message": {
                    "type": 0,
                    "content": "snapshot only",
                    "timestamp": referenced["timestamp"],
                    "edited_timestamp": None,
                    "attachments": [
                        {
                            "id": "402",
                            "filename": "snapshot.mp4",
                            "url": "https://cdn.example/snapshot.mp4",
                        }
                    ],
                    "embeds": [
                        {"image": {"url": "https://cdn.example/snapshot.png"}}
                    ],
                    "components": [],
                }
            }
        ]

        evidence = extract_message_evidence(
            outer,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
            json_pointer="/payload/0",
        )

        self.assertEqual(evidence.status, "complete")
        self.assertEqual([node.kind for node in evidence.nodes], ["root", "referenced_message", "snapshot"])
        self.assertEqual(evidence.nodes[0].attribution.kind, "webhook")
        self.assertEqual(evidence.nodes[1].attribution.kind, "bot_user")
        snapshot = evidence.nodes[2]
        expected_snapshot_key = (
            f"snapshot:100:{outer['id']}:/message_snapshots/0/message"
        )
        self.assertEqual(snapshot.node_key, expected_snapshot_key)
        self.assertEqual(snapshot.attribution.kind, "snapshot_unattributed")
        self.assertIsNone(snapshot.attribution.author_id)
        self.assertEqual(snapshot.timestamp.status, "valid_reference")
        media_by_key = {item.logical_key: item for item in evidence.media}
        self.assertIn(f"{referenced_id}:attachment:401", media_by_key)
        snapshot_attachment_key = f"{expected_snapshot_key}:attachment:402"
        self.assertIn(snapshot_attachment_key, media_by_key)
        self.assertEqual(
            media_by_key[snapshot_attachment_key].json_pointer,
            "/payload/0/message_snapshots/0/message/attachments/0",
        )
        self.assertTrue(
            any(
                item.node_key == expected_snapshot_key and item.field == "image"
                for item in evidence.media
            )
        )

    def test_message_id_cycle_is_explicitly_partial(self) -> None:
        root = full_message("2026-01-03T00:00:00.123000+00:00")
        repeated = dict(root)
        root["message_reference"] = {
            "type": 0,
            "message_id": root["id"],
            "channel_id": root["channel_id"],
        }
        root["referenced_message"] = repeated

        evidence = extract_message_evidence(
            root,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
        )

        self.assertEqual(evidence.status, "partial")
        self.assertEqual(len(evidence.nodes), 1)
        self.assertIn(
            "message_cycle_detected",
            {diagnostic.code for diagnostic in evidence.diagnostics},
        )

    def test_depth_limit_is_explicitly_partial(self) -> None:
        root = full_message("2026-01-04T00:00:00.123000+00:00", increment=1)
        child = full_message("2026-01-04T00:00:00.124000+00:00", increment=2)
        grandchild = full_message("2026-01-04T00:00:00.125000+00:00", increment=3)
        root["referenced_message"] = child
        child["referenced_message"] = grandchild

        evidence = extract_message_evidence(
            root,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
            max_depth=1,
        )

        self.assertEqual(evidence.status, "partial")
        self.assertEqual(len(evidence.nodes), 2)
        depth_error = next(
            diagnostic
            for diagnostic in evidence.diagnostics
            if diagnostic.code == "message_depth_exceeded"
        )
        self.assertEqual(depth_error.severity, "error")
        self.assertEqual(
            depth_error.json_pointer,
            "/referenced_message/referenced_message",
        )

    def test_components_v2_media_aliases_and_links_are_recursive(self) -> None:
        message = full_message("2026-01-05T00:00:00.123000+00:00")
        message_id = str(message["id"])
        message["attachments"] = [
            {
                "id": "401",
                "filename": "one.pdf",
                "url": "https://cdn.example/one.pdf",
                "proxy_url": "https://proxy.example/one.pdf",
            }
        ]
        shared_external_url = "https://media.example/shared.png"
        message["components"] = [
            {
                "type": 9,
                "components": [{"type": 10, "content": "section"}],
                "accessory": {
                    "type": 11,
                    "media": {
                        "url": shared_external_url,
                        "proxy_url": "https://proxy.example/thumb.png",
                    },
                },
            },
            {
                "type": 17,
                "components": [
                    {
                        "type": 12,
                        "items": [
                            {
                                "media": {
                                    "url": "attachment://one.pdf",
                                    "attachment_id": "401",
                                }
                            },
                            {"media": {"url": shared_external_url}},
                        ],
                    },
                    {"type": 13, "file": {"url": "attachment://one.pdf"}},
                    {
                        "type": 2,
                        "style": 5,
                        "label": "source",
                        "url": "https://example.com/action",
                    },
                ],
            },
        ]

        evidence = extract_message_evidence(
            message,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
        )

        self.assertEqual(evidence.status, "complete")
        components = [item for item in evidence.media if item.kind == "component"]
        self.assertEqual(len(components), 4)
        attachment_key = f"{message_id}:attachment:401"
        attachment_aliases = [
            item for item in components if item.logical_key == attachment_key
        ]
        self.assertEqual(len(attachment_aliases), 2)
        self.assertEqual(
            {item.resolution for item in attachment_aliases},
            {"attachment_id", "attachment_filename"},
        )
        self.assertTrue(
            all(item.url == "https://cdn.example/one.pdf" for item in attachment_aliases)
        )
        external = [item for item in components if item.observed_url == shared_external_url]
        self.assertEqual(len(external), 2)
        self.assertNotEqual(external[0].logical_key, external[1].logical_key)
        self.assertNotIn(shared_external_url, external[0].logical_key)
        thumbnail = next(item for item in components if item.field == "thumbnail")
        self.assertEqual(
            thumbnail.json_pointer,
            "/components/0/accessory/media",
        )
        self.assertIn(
            ("component_link", "https://example.com/action"),
            {(item.kind, item.value) for item in evidence.references},
        )

    def test_component_attachment_resolution_is_fail_closed(self) -> None:
        message = full_message("2026-01-06T00:00:00.123000+00:00")
        message_id = str(message["id"])
        message["attachments"] = [
            {
                "id": "401",
                "filename": "duplicate.png",
                "url": "https://cdn.example/one.png",
            },
            {
                "id": "402",
                "filename": "duplicate.png",
                "url": "https://cdn.example/two.png",
            },
        ]
        message["components"] = [
            {"type": 13, "file": {"url": "attachment://duplicate.png"}},
            {"type": 13, "file": {"url": "attachment://missing.png"}},
            {
                "type": 11,
                "media": {
                    "url": "attachment://duplicate.png",
                    "attachment_id": "999",
                    "proxy_url": "https://proxy.example/id-999.png",
                },
            },
        ]

        evidence = extract_message_evidence(
            message,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
        )

        self.assertEqual(evidence.status, "partial")
        components = [item for item in evidence.media if item.kind == "component"]
        ambiguous = next(
            item for item in components if item.resolution == "ambiguous_attachment_filename"
        )
        unresolved = next(
            item for item in components if item.resolution == "unresolved_attachment_reference"
        )
        by_id = next(
            item for item in components if item.resolution == "attachment_id_unlisted"
        )
        self.assertFalse(ambiguous.downloadable)
        self.assertFalse(unresolved.downloadable)
        self.assertTrue(by_id.downloadable)
        self.assertEqual(by_id.logical_key, f"{message_id}:attachment:999")
        self.assertEqual(by_id.url, "https://proxy.example/id-999.png")
        self.assertEqual(
            {item.code for item in evidence.diagnostics},
            {"component_attachment_ambiguous", "component_attachment_unresolved"},
        )

    def test_stickers_and_poll_emoji_have_stable_identity_and_provenance(self) -> None:
        message = full_message("2026-01-07T00:00:00.123000+00:00")
        message["sticker_items"] = [
            {"id": "501", "name": "png", "format_type": 1},
            {"id": "502", "name": "apng", "format_type": 2},
            {"id": "503", "name": "lottie", "format_type": 3},
            {"id": "504", "name": "gif", "format_type": 4},
        ]
        message["stickers"] = [
            {"id": "501", "name": "png-full", "format_type": 1, "tags": "chart"}
        ]
        message["poll"] = {
            "question": {"text": "direction?"},
            "answers": [
                {
                    "answer_id": 1,
                    "poll_media": {
                        "text": "up",
                        "emoji": {"id": "601", "name": "up", "animated": True},
                    },
                },
                {
                    "answer_id": 2,
                    "poll_media": {
                        "text": "down",
                        "emoji": {"id": None, "name": "🔥"},
                    },
                },
            ],
        }

        evidence = extract_message_evidence(
            message,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
        )

        self.assertEqual(evidence.status, "complete")
        stickers = [item for item in evidence.media if item.kind == "sticker"]
        self.assertEqual(len(stickers), 5)
        sticker_urls = {item.logical_key: item.url for item in stickers}
        self.assertEqual(
            sticker_urls["sticker:501"],
            "https://cdn.discordapp.com/stickers/501.png",
        )
        self.assertEqual(
            sticker_urls["sticker:502"],
            "https://cdn.discordapp.com/stickers/502.png",
        )
        self.assertEqual(
            sticker_urls["sticker:503"],
            "https://cdn.discordapp.com/stickers/503.json",
        )
        self.assertEqual(
            sticker_urls["sticker:504"],
            "https://media.discordapp.net/stickers/504.gif",
        )
        self.assertEqual(
            sum(item.logical_key == "sticker:501" for item in stickers),
            2,
        )
        poll_emoji = next(item for item in evidence.media if item.kind == "emoji")
        self.assertEqual(poll_emoji.logical_key, "emoji:601")
        self.assertEqual(
            poll_emoji.url,
            "https://cdn.discordapp.com/emojis/601.webp?animated=true",
        )
        unicode_emoji = next(
            item for item in evidence.references if item.kind == "unicode_emoji"
        )
        self.assertEqual(unicode_emoji.value, "🔥")
        self.assertEqual(
            unicode_emoji.json_pointer,
            "/poll/answers/1/poll_media/emoji",
        )

    def test_webhook_delivery_identity_mismatch_is_partial(self) -> None:
        message = full_message(
            "2026-01-08T00:00:00.123000+00:00",
            author={"id": "701", "username": "mutable-display-name"},
        )
        message["webhook_id"] = "700"

        evidence = extract_message_evidence(
            message,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
        )

        self.assertEqual(evidence.nodes[0].attribution.kind, "webhook")
        self.assertEqual(evidence.nodes[0].attribution.author_id, "701")
        self.assertEqual(evidence.nodes[0].attribution.webhook_id, "700")
        self.assertEqual(evidence.status, "partial")
        self.assertIn(
            "webhook_author_id_mismatch",
            {diagnostic.code for diagnostic in evidence.diagnostics},
        )

    def test_referenced_message_identity_must_match_reference(self) -> None:
        root = full_message("2026-01-09T00:00:00.123000+00:00")
        expected_id = snowflake_at("2026-01-08T23:59:00.123000+00:00")
        referenced = full_message("2026-01-08T23:58:00.123000+00:00")
        root["message_reference"] = {
            "type": 0,
            "message_id": expected_id,
            "channel_id": "100",
        }
        root["referenced_message"] = referenced

        evidence = extract_message_evidence(
            root,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
        )

        self.assertEqual(evidence.status, "partial")
        self.assertIn(
            "referenced_message_identity_mismatch",
            {diagnostic.code for diagnostic in evidence.diagnostics},
        )

    def test_full_message_timestamp_and_edit_are_strict(self) -> None:
        message = full_message("2026-01-10T00:00:00.123000+00:00")
        message["timestamp"] = "2026-01-10T00:00:00.125001+00:00"
        message["edited_timestamp"] = "2026-01-10T00:00:00.124000+00:00"

        evidence = extract_message_evidence(
            message,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
        )

        self.assertEqual(evidence.status, "partial")
        self.assertEqual(evidence.nodes[0].timestamp.status, "mismatch")
        self.assertGreater(evidence.nodes[0].timestamp.delta_ms or 0, 1)
        self.assertEqual(
            {diagnostic.code for diagnostic in evidence.diagnostics},
            {
                "full_message_timestamp_snowflake_mismatch",
                "edited_timestamp_before_created",
            },
        )

    def test_snapshot_reference_timestamp_mismatch_is_warning_only(self) -> None:
        root = full_message("2026-01-11T00:00:00.123000+00:00")
        reference_id = snowflake_at("2026-01-10T23:00:00.123000+00:00")
        root["message_reference"] = {
            "type": 1,
            "message_id": reference_id,
            "channel_id": "100",
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

        evidence = extract_message_evidence(
            root,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
        )

        self.assertEqual(evidence.status, "complete")
        snapshot = next(node for node in evidence.nodes if node.kind == "snapshot")
        self.assertEqual(snapshot.timestamp.status, "warning_mismatch")
        warning = next(
            diagnostic
            for diagnostic in evidence.diagnostics
            if diagnostic.code == "snapshot_timestamp_reference_mismatch"
        )
        self.assertEqual(warning.severity, "warning")

    def test_malformed_message_snapshot_elements_are_explicitly_partial(self) -> None:
        root = full_message("2026-01-11T01:00:00.123000+00:00")
        root["message_snapshots"] = [
            None,
            {},
            {"message": None},
            {"message": "not-an-object"},
        ]

        evidence = extract_message_evidence(
            root,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
            json_pointer="/payload/0",
        )

        diagnostics = [
            diagnostic
            for diagnostic in evidence.diagnostics
            if diagnostic.code == "message_snapshot_invalid"
        ]
        self.assertEqual(evidence.status, "partial")
        self.assertEqual(len(diagnostics), 4)
        self.assertEqual(
            [diagnostic.json_pointer for diagnostic in diagnostics],
            [
                f"/payload/0/message_snapshots/{index}"
                for index in range(4)
            ],
        )
        self.assertTrue(
            all(diagnostic.severity == "error" for diagnostic in diagnostics)
        )
        self.assertTrue(
            all(
                diagnostic.node_key == evidence.nodes[0].node_key
                for diagnostic in diagnostics
            )
        )

    def test_embed_attachment_aliases_reuse_attachment_identity(self) -> None:
        message = full_message("2026-01-12T00:00:00.123000+00:00")
        message_id = str(message["id"])
        message["attachments"] = [
            {
                "id": "401",
                "filename": "chart.png",
                "url": "https://cdn.example/chart.png",
                "proxy_url": "https://proxy.example/chart.png",
            }
        ]
        message["embeds"] = [
            {
                "image": {"url": "attachment://chart.png"},
                "thumbnail": {
                    "url": "attachment://different-name.png",
                    "attachment_id": "401",
                },
                "author": {"icon_url": "attachment://chart.png"},
            }
        ]

        evidence = extract_message_evidence(
            message,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
        )

        self.assertEqual(evidence.status, "complete")
        embeds = [item for item in evidence.media if item.kind == "embed"]
        self.assertEqual(len(embeds), 3)
        self.assertEqual(
            {item.logical_key for item in embeds},
            {f"{message_id}:attachment:401"},
        )
        self.assertEqual(
            {item.resolution for item in embeds},
            {"attachment_id", "attachment_filename"},
        )
        self.assertTrue(
            all(item.url == "https://cdn.example/chart.png" for item in embeds)
        )

    def test_duplicate_attachment_id_is_partial_and_alias_resolution_is_stable(
        self,
    ) -> None:
        message = full_message("2026-01-12T01:00:00.123000+00:00")
        message["attachments"] = [
            {
                "id": "401",
                "filename": "first.png",
                "url": "https://cdn.example/first.png",
            },
            {
                "id": "401",
                "filename": "second.png",
                "url": "https://cdn.example/second.png",
            },
        ]
        message["embeds"] = [
            {
                "image": {
                    "attachment_id": "401",
                    "url": "https://proxy.example/duplicate.png",
                }
            }
        ]

        evidence = extract_message_evidence(
            message,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
        )

        self.assertEqual(evidence.status, "partial")
        duplicate = next(
            diagnostic
            for diagnostic in evidence.diagnostics
            if diagnostic.code == "attachment_id_duplicate"
        )
        self.assertEqual(duplicate.severity, "error")
        self.assertEqual(duplicate.json_pointer, "/attachments/1/id")
        alias = next(item for item in evidence.media if item.kind == "embed")
        self.assertEqual(alias.resolution, "attachment_id")
        self.assertEqual(alias.url, "https://cdn.example/first.png")

    def test_logical_keys_do_not_depend_on_history_or_pin_envelope_position(
        self,
    ) -> None:
        message = full_message("2026-01-12T02:00:00.123000+00:00")
        message["components"] = [
            {
                "type": 11,
                "media": {"url": "https://cdn.example/root-component.png"},
            }
        ]
        message["message_snapshots"] = [
            {
                "message": {
                    "timestamp": "2026-01-12T01:59:00.123000+00:00",
                    "edited_timestamp": None,
                    "content": "snapshot",
                    "attachments": [
                        {
                            "id": "901",
                            "filename": "snapshot.png",
                            "url": "https://cdn.example/snapshot.png",
                        }
                    ],
                    "embeds": [],
                    "components": [],
                }
            }
        ]

        history = extract_message_evidence(
            message,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
            json_pointer="/payload/3",
        )
        pinned = extract_message_evidence(
            message,
            stream="pins_100",
            evidence_path="pages/pins_100/000004.json",
            json_pointer="/payload/items/7/message",
        )

        self.assertEqual(
            [node.node_key for node in history.nodes],
            [node.node_key for node in pinned.nodes],
        )
        self.assertEqual(
            {item.logical_key for item in history.media},
            {item.logical_key for item in pinned.media},
        )
        self.assertNotEqual(
            {item.source.json_pointer for item in history.media},
            {item.source.json_pointer for item in pinned.media},
        )

    def test_missing_and_deleted_referenced_messages_are_not_conflated(self) -> None:
        timestamp = "2026-01-13T00:00:00.123000+00:00"
        missing = full_message(timestamp)
        missing["type"] = 19
        missing["message_reference"] = {
            "type": 0,
            "message_id": snowflake_at("2026-01-12T23:59:00.123000+00:00"),
            "channel_id": "100",
        }
        deleted = dict(missing)
        deleted["referenced_message"] = None

        missing_evidence = extract_message_evidence(
            missing,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
        )
        deleted_evidence = extract_message_evidence(
            deleted,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
        )

        self.assertEqual(missing_evidence.status, "partial")
        self.assertIn(
            "referenced_message_unknown",
            {diagnostic.code for diagnostic in missing_evidence.diagnostics},
        )
        self.assertEqual(deleted_evidence.status, "complete")
        deleted_diagnostic = next(
            diagnostic
            for diagnostic in deleted_evidence.diagnostics
            if diagnostic.code == "referenced_message_deleted"
        )
        self.assertEqual(deleted_diagnostic.severity, "info")

    def test_full_message_without_author_is_partial_but_snapshot_is_not(self) -> None:
        message = full_message("2026-01-14T00:00:00.123000+00:00")
        del message["author"]

        evidence = extract_message_evidence(
            message,
            stream="messages_100",
            evidence_path="pages/messages_100/000001.json",
        )

        self.assertEqual(evidence.nodes[0].attribution.kind, "unknown")
        self.assertEqual(evidence.status, "partial")
        self.assertIn(
            "delivery_author_invalid",
            {diagnostic.code for diagnostic in evidence.diagnostics},
        )


if __name__ == "__main__":
    unittest.main()
