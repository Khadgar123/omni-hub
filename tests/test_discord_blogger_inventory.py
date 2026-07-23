from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from omni_hub.discord_blogger_corpus import BloggerMessage
from omni_hub.discord_blogger_inventory import (
    build_blogger_target_inventory,
    publish_blogger_target_inventory,
)
from omni_hub.discord_sharding import target_set_sha256


_FORUM_ID = "1514503993567744030"
_EXPLICIT_THREAD_ID = "1516770209279512618"
_DYNAMIC_THREAD_ID = "1526994361362157672"


def _message(
    message_id: str,
    *,
    channel_id: str,
    content: str,
    media_count: int = 0,
) -> BloggerMessage:
    return BloggerMessage(
        message_id=message_id,
        channel_id=channel_id,
        author_id="900000000000000001",
        timestamp=f"2026-07-21T00:00:0{message_id[-1]}+00:00",
        edited_timestamp=None,
        content=content,
        reply_message_id=None,
        snapshot_ref="private/snapshot/ref",
        snapshot_sha256="a" * 64,
        media_occurrence_refs=tuple(
            f"private/media/ref/{index}" for index in range(media_count)
        ),
    )


def _target_snapshot() -> dict[str, object]:
    targets = [
        {
            "id": _FORUM_ID,
            "name": "Dr-Profit",
            "kind": "GUILD_FORUM (15)",
            "parent_id": None,
            "source_labels": ["dr-profit-利润医生"],
        },
        {
            "id": _EXPLICIT_THREAD_ID,
            "name": "Dr-Profit-VIP策略",
            "kind": "GUILD_PUBLIC_THREAD (11)",
            "parent_id": _FORUM_ID,
            "source_labels": ["Dr-Profit-VIP策略"],
        },
    ]
    return {
        "schema_version": 1,
        "guild_id": "1427104065959231640",
        "target_count": len(targets),
        "target_set_sha256": target_set_sha256(row["id"] for row in targets),
        "targets": targets,
    }


def _inventory() -> dict[str, object]:
    return build_blogger_target_inventory(
        messages=(
            _message(
                "160000000000000001",
                channel_id=_EXPLICIT_THREAD_ID,
                content="SECRET_EXPLICIT_BODY",
                media_count=1,
            ),
            _message(
                "160000000000000002",
                channel_id=_DYNAMIC_THREAD_ID,
                content="SECRET_DYNAMIC_BODY https://cdn.example/signed?token=secret",
                media_count=2,
            ),
        ),
        target_snapshot=_target_snapshot(),
        discovered_threads=(
            {
                "id": _EXPLICIT_THREAD_ID,
                "parent_id": _FORUM_ID,
                "owner_index": 1,
            },
            {
                "id": _DYNAMIC_THREAD_ID,
                "parent_id": _FORUM_ID,
                "owner_index": 1,
            },
        ),
        provenance={
            "closure_audit_sha256": "b" * 64,
            "target_snapshot_sha256": "c" * 64,
            "full_private_scope_complete": False,
            "private_archived_parent_blocker_count": 123,
        },
        private_archived_blocked_parent_ids=(_FORUM_ID,),
        family_parent_target_ids=(_FORUM_ID,),
    )


class BloggerTargetInventoryTests(unittest.TestCase):
    def test_four_explicit_threads_keep_exact_counts_beside_parent_rollup(
        self,
    ) -> None:
        thread_ids = [
            "1516770209279512618",
            "1516770209279512619",
            "1516770209279512620",
            "1516770209279512621",
        ]
        targets = [
            {
                "id": _FORUM_ID,
                "name": "forum",
                "kind": "GUILD_FORUM (15)",
                "parent_id": None,
                "source_labels": ["forum"],
            },
            *[
                {
                    "id": thread_id,
                    "name": f"thread-{index}",
                    "kind": "GUILD_PUBLIC_THREAD (11)",
                    "parent_id": _FORUM_ID,
                    "source_labels": [f"thread-{index}"],
                }
                for index, thread_id in enumerate(thread_ids)
            ],
        ]
        snapshot = {
            "schema_version": 1,
            "guild_id": "1427104065959231640",
            "target_count": len(targets),
            "target_set_sha256": target_set_sha256(
                row["id"] for row in targets
            ),
            "targets": targets,
        }
        result = build_blogger_target_inventory(
            messages=(
                _message(
                    "160000000000000001",
                    channel_id=thread_ids[0],
                    content="one",
                ),
                _message(
                    "160000000000000002",
                    channel_id=thread_ids[1],
                    content="two",
                ),
            ),
            target_snapshot=snapshot,
            discovered_threads=(),
            provenance={},
            private_archived_blocked_parent_ids=(),
            family_parent_target_ids=(_FORUM_ID,),
        )
        rows = {row["target_id"]: row for row in result["targets"]}

        self.assertEqual(rows[_FORUM_ID]["message_count"], 2)
        self.assertEqual(
            [rows[thread_id]["message_count"] for thread_id in thread_ids],
            [1, 1, 0, 0],
        )
        self.assertTrue(
            all(
                rows[thread_id]["count_semantics"] == "exact_thread"
                for thread_id in thread_ids
            )
        )

    def test_explicit_thread_rolls_up_without_duplicate_discovery_row(self) -> None:
        result = build_blogger_target_inventory(
            messages=(
                _message(
                    "160000000000000001",
                    channel_id=_EXPLICIT_THREAD_ID,
                    content="SECRET_EXPLICIT_BODY",
                ),
            ),
            target_snapshot=_target_snapshot(),
            discovered_threads=(),
            provenance={},
            private_archived_blocked_parent_ids=(_FORUM_ID,),
            family_parent_target_ids=(_FORUM_ID,),
        )
        rows = {row["target_id"]: row for row in result["targets"]}

        self.assertEqual(rows[_FORUM_ID]["message_count"], 1)
        self.assertEqual(rows[_EXPLICIT_THREAD_ID]["message_count"], 1)

    def test_family_rollup_does_not_erase_explicit_thread_exact_row(self) -> None:
        result = _inventory()
        rows = {row["target_id"]: row for row in result["targets"]}

        self.assertEqual(rows[_FORUM_ID]["message_count"], 2)
        self.assertEqual(rows[_FORUM_ID]["count_semantics"], "family_rollup")
        self.assertEqual(rows[_EXPLICIT_THREAD_ID]["message_count"], 1)
        self.assertEqual(
            rows[_EXPLICIT_THREAD_ID]["count_semantics"], "exact_thread"
        )
        self.assertEqual(
            rows[_EXPLICIT_THREAD_ID]["evidence_status"], "verified_messages"
        )
        self.assertEqual(rows[_FORUM_ID]["scope_completeness"], "known_scope_only")
        self.assertEqual(
            rows[_EXPLICIT_THREAD_ID]["scope_completeness"], "observed_scope"
        )
        self.assertEqual(
            rows[_EXPLICIT_THREAD_ID]["private_archived_scope_status"],
            "exact_thread_scope_complete",
        )

    def test_overlapping_views_preserve_one_authorized_corpus(self) -> None:
        result = _inventory()

        self.assertEqual(result["unique_authorized_message_count"], 2)
        self.assertEqual(result["per_target_message_sum"], 3)
        self.assertEqual(result["target_count"], 2)
        self.assertEqual(result["targets_with_verified_messages"], 2)
        self.assertEqual(result["targets_without_verified_messages"], 0)
        self.assertEqual(result["coverage_dimensions"]["media"], "not_asserted")
        for row in result["targets"]:
            self.assertNotIn("media_message_count", row)
            self.assertNotIn("media_occurrence_count", row)

    def test_inventory_is_redacted_and_contains_no_message_id_list(self) -> None:
        result = _inventory()
        serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)

        self.assertNotIn("SECRET_EXPLICIT_BODY", serialized)
        self.assertNotIn("SECRET_DYNAMIC_BODY", serialized)
        self.assertNotIn("https://", serialized)
        self.assertNotIn("snapshot/ref", serialized)
        self.assertNotIn("media/ref", serialized)
        self.assertNotIn("logical_key", serialized)
        self.assertNotIn("message_ids", serialized)

    def test_publisher_is_no_clobber_and_uses_private_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            output = Path("result/inventory.json")
            first = publish_blogger_target_inventory(
                workspace=workspace,
                output_path=output,
                inventory=_inventory(),
            )
            target = workspace / output

            self.assertEqual(first["output_path"], output.as_posix())
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            second = publish_blogger_target_inventory(
                workspace=workspace,
                output_path=output,
                inventory=_inventory(),
            )
            self.assertEqual(second["output_sha256"], first["output_sha256"])

    def test_publisher_detects_staging_name_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            output = Path("result/race.json")
            original_link = __import__("os").link

            def replace_stage(source: str, destination: str, **kwargs: object) -> None:
                stage = workspace / output.parent / source
                size = stage.stat().st_size
                stage.unlink()
                stage.write_bytes(b"x" * size)
                stage.chmod(0o600)
                original_link(source, destination, **kwargs)

            with patch(
                "omni_hub.discord_blogger_inventory.os.link",
                side_effect=replace_stage,
            ), self.assertRaisesRegex(ValueError, "publication changed"):
                publish_blogger_target_inventory(
                    workspace=workspace,
                    output_path=output,
                    inventory=_inventory(),
                )

    def test_discovered_explicit_target_must_be_a_thread(self) -> None:
        snapshot = _target_snapshot()
        snapshot["targets"][1]["kind"] = "GUILD_TEXT (0)"

        with self.assertRaisesRegex(ValueError, "explicit Thread"):
            build_blogger_target_inventory(
                messages=(),
                target_snapshot=snapshot,
                discovered_threads=(
                    {
                        "id": _EXPLICIT_THREAD_ID,
                        "parent_id": _FORUM_ID,
                        "owner_index": 1,
                    },
                ),
                provenance={},
                private_archived_blocked_parent_ids=(),
                family_parent_target_ids=(_FORUM_ID,),
            )

    def test_declared_family_without_discovered_threads_keeps_family_semantics(self) -> None:
        target_id = "1530000000000000001"
        targets = [
            {
                "id": target_id,
                "name": "text family",
                "kind": "GUILD_TEXT (0)",
                "parent_id": "1530000000000000002",
                "source_labels": ["text family"],
            }
        ]
        snapshot = {
            "schema_version": 1,
            "guild_id": "1427104065959231640",
            "target_count": 1,
            "target_set_sha256": target_set_sha256([target_id]),
            "targets": targets,
        }

        result = build_blogger_target_inventory(
            messages=(),
            target_snapshot=snapshot,
            discovered_threads=(),
            provenance={},
            private_archived_blocked_parent_ids=(target_id,),
            family_parent_target_ids=(target_id,),
        )

        row = result["targets"][0]
        self.assertEqual(row["count_semantics"], "family_rollup")
        self.assertEqual(row["scope_completeness"], "known_scope_only")
        self.assertEqual(
            row["private_archived_scope_status"],
            "unjoined_private_archives_not_enumerable",
        )


if __name__ == "__main__":
    unittest.main()
