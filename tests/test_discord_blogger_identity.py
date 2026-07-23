from __future__ import annotations

import unittest
from typing import Mapping

from omni_hub.discord_blogger_contract import canonical_json_sha256
from omni_hub.discord_blogger_corpus import BloggerMessage
from omni_hub.discord_blogger_identity import (
    build_target_identity_registry,
    resolve_message_owner,
)


_PARENT = "1514503993567744030"
_THREAD = "1516770209279512618"
_AUTHOR = "1600000000000000900"


def _message(
    *,
    author_id: str = _AUTHOR,
    timestamp: str = "2026-07-21T00:00:00+00:00",
    channel_id: str = _THREAD,
    webhook_id: str | None = None,
    application_id: str | None = None,
) -> BloggerMessage:
    return BloggerMessage(
        message_id="1600000000000000001",
        channel_id=channel_id,
        author_id=author_id,
        timestamp=timestamp,
        edited_timestamp=None,
        content="private body must never enter the registry",
        reply_message_id=None,
        snapshot_ref="private/source.json#/0",
        snapshot_sha256="a" * 64,
        media_occurrence_refs=(),
        webhook_id=webhook_id,
        application_id=application_id,
    )


def _inventory() -> dict[str, object]:
    return {
        "artifact_kind": "discord-blogger-target-inventory-v1",
        "schema_version": 1,
        "targets": [
            {
                "target_id": _PARENT,
                "kind": "GUILD_FORUM (15)",
                "parent_id": None,
                "count_semantics": "family_rollup",
            },
            {
                "target_id": _THREAD,
                "kind": "GUILD_PUBLIC_THREAD (11)",
                "parent_id": _PARENT,
                "count_semantics": "exact_thread",
            },
        ],
    }


def _review(
    *,
    decision: str,
    identity_type: str,
    author_id: str = _AUTHOR,
    valid_from: str = "2026-07-01T00:00:00+00:00",
    valid_to: str | None = None,
    performance_owner_id: str | None = _AUTHOR,
    aggregation_owner_id: str | None = None,
    target_type: str = "single_author_analyst",
    aggregation_scope: str = "message_owner",
    target_id: str = _THREAD,
    webhook_id: str | None = None,
    application_id: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "candidate_id": canonical_json_sha256(
            {
                "target_id": target_id,
                "author_id": author_id,
                "valid_from": valid_from,
            }
        ),
        "record_type": "identity_review",
        "target_id": target_id,
        "author_id": author_id,
        "webhook_id": webhook_id,
        "application_id": application_id,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "decision": decision,
        "identity_type": identity_type,
        "target_type": target_type,
        "aggregation_scope": aggregation_scope,
        "performance_owner_id": performance_owner_id,
        "aggregation_owner_id": aggregation_owner_id,
        "reviewer": "reviewer-id-1",
        "reviewed_at": "2026-07-22T12:00:00+00:00",
        "evidence_sha256": "b" * 64,
        "candidate_pack_sha256": "c" * 64,
        "reviewed_labels_sha256": "d" * 64,
    }


def _frozen_pack(
    *rows: dict[str, object],
) -> tuple[Mapping[str, object], ...]:
    manifest: dict[str, object] = {
        "schema_version": 1,
        "record_type": "manifest",
        "reviewed_row_count": len(rows),
        "reviewed_rows_sha256": canonical_json_sha256(list(rows)),
        "candidate_pack_sha256": "c" * 64,
        "reviewed_labels_sha256": "d" * 64,
    }
    return (manifest, *rows)


def _build_registry(
    *,
    messages: tuple[BloggerMessage, ...],
    inventory: Mapping[str, object],
    reviewed_overrides: tuple[Mapping[str, object], ...],
    expected_reviewed_pack_sha256: str | None = None,
) -> dict[str, object]:
    expected = (
        canonical_json_sha256(list(reviewed_overrides))
        if expected_reviewed_pack_sha256 is None
        else expected_reviewed_pack_sha256
    )
    return build_target_identity_registry(
        messages=messages,
        inventory=inventory,
        reviewed_overrides=reviewed_overrides,
        expected_reviewed_pack_sha256=expected,
    )


class TargetIdentityRegistryTests(unittest.TestCase):
    def test_resolves_all_six_reviewed_identity_types_by_exact_ids(self) -> None:
        cases = (
            (
                "accepted", "owner", "single_author_analyst",
                "message_owner", _AUTHOR, None,
            ),
            (
                "accepted", "team", "multi_author_team",
                "target_rollup_only", None, _AUTHOR,
            ),
            (
                "accepted", "proxy", "signal_delivery_channel",
                "target_rollup_only", None, _AUTHOR,
            ),
            (
                "accepted", "community", "community_chat",
                "no_performance", None, None,
            ),
            (
                "rejected", "unknown", "unknown",
                "no_performance", None, None,
            ),
            (
                "conflicting", "conflict", "unknown",
                "no_performance", None, None,
            ),
        )
        for (
            decision,
            identity_type,
            target_type,
            aggregation_scope,
            performance_owner_id,
            aggregation_owner_id,
        ) in cases:
            with self.subTest(identity_type=identity_type):
                review = _review(
                    decision=decision,
                    identity_type=identity_type,
                    performance_owner_id=performance_owner_id,
                    aggregation_owner_id=aggregation_owner_id,
                    target_type=target_type,
                    aggregation_scope=aggregation_scope,
                )
                registry = _build_registry(
                    messages=(_message(),),
                    inventory=_inventory(),
                    reviewed_overrides=_frozen_pack(review),
                )
                resolution = resolve_message_owner(
                    message=_message(), registry=registry
                )
                self.assertEqual(resolution.identity_type, identity_type)
                self.assertEqual(
                    resolution.verified,
                    identity_type in {"owner", "team", "proxy", "community"},
                )
                self.assertEqual(
                    resolution.performance_owner_id, performance_owner_id
                )
                self.assertEqual(
                    resolution.aggregation_owner_id, aggregation_owner_id
                )
                self.assertEqual(resolution.target_type, target_type)
                self.assertEqual(
                    resolution.aggregation_scope, aggregation_scope
                )
                self.assertEqual(
                    resolution.author_eligible,
                    identity_type != "community",
                )
                self.assertLessEqual(
                    len(
                        [
                            value
                            for value in (resolution.performance_owner_id,)
                            if value is not None
                        ]
                    ),
                    1,
                )

    def test_validity_windows_are_half_open_and_do_not_use_stale_binding(self) -> None:
        before = _review(
            decision="accepted",
            identity_type="owner",
            valid_from="2026-07-01T00:00:00+00:00",
            valid_to="2026-07-15T00:00:00+00:00",
        )
        after = _review(
            decision="accepted",
            identity_type="team",
            valid_from="2026-07-15T00:00:00+00:00",
            target_type="multi_author_team",
            aggregation_scope="target_rollup_only",
            performance_owner_id=None,
            aggregation_owner_id=_AUTHOR,
        )
        registry = _build_registry(
            messages=(
                _message(timestamp="2026-07-14T23:59:59+00:00"),
                _message(timestamp="2026-07-15T00:00:00+00:00"),
            ),
            inventory=_inventory(),
            reviewed_overrides=_frozen_pack(before, after),
        )

        self.assertEqual(
            resolve_message_owner(
                message=_message(timestamp="2026-07-14T23:59:59+00:00"),
                registry=registry,
            ).identity_type,
            "owner",
        )
        self.assertEqual(
            resolve_message_owner(
                message=_message(timestamp="2026-07-15T00:00:00+00:00"),
                registry=registry,
            ).identity_type,
            "team",
        )

    def test_single_observed_author_stays_unknown_and_author_eligible_without_review(self) -> None:
        registry = _build_registry(
            messages=(_message(),),
            inventory=_inventory(),
            reviewed_overrides=_frozen_pack(),
        )
        target = next(
            row for row in registry["targets"] if row["target_id"] == _THREAD
        )
        resolution = resolve_message_owner(message=_message(), registry=registry)

        self.assertEqual(target["default_identity_type"], "unknown")
        self.assertTrue(target["author_eligible"])
        self.assertEqual(resolution.identity_type, "unknown")
        self.assertFalse(resolution.verified)
        self.assertIsNone(resolution.performance_owner_id)
        self.assertNotIn("private body", repr(registry))

    def test_public_thread_is_exact_while_parent_is_rollup_only(self) -> None:
        review = _review(decision="accepted", identity_type="owner")
        registry = _build_registry(
            messages=(_message(),),
            inventory=_inventory(),
            reviewed_overrides=_frozen_pack(review),
        )
        resolution = resolve_message_owner(message=_message(), registry=registry)

        self.assertEqual(resolution.target_id, _THREAD)
        self.assertEqual(resolution.parent_rollup_target_ids, (_PARENT,))
        self.assertEqual(resolution.performance_owner_id, _AUTHOR)
        parent = next(
            row for row in registry["targets"] if row["target_id"] == _PARENT
        )
        self.assertEqual(parent["identity_semantics"], "rollup_only")
        self.assertEqual(parent["bindings"], [])

    def test_nickname_or_wrong_author_id_never_matches_reviewed_binding(self) -> None:
        registry = _build_registry(
            messages=(_message(),),
            inventory=_inventory(),
            reviewed_overrides=(
                *_frozen_pack(
                    _review(decision="accepted", identity_type="owner")
                ),
            ),
        )
        resolution = resolve_message_owner(
            message=_message(author_id="1600000000000000999"),
            registry=registry,
        )

        self.assertEqual(resolution.identity_type, "unknown")
        self.assertFalse(resolution.verified)
        self.assertIsNone(resolution.performance_owner_id)

    def test_delivery_ids_must_match_the_exact_reviewed_binding(self) -> None:
        review = _review(
            decision="accepted",
            identity_type="owner",
            webhook_id="1600000000000000910",
            application_id="1600000000000000920",
        )
        registry = _build_registry(
            messages=(
                _message(
                    webhook_id="1600000000000000910",
                    application_id="1600000000000000920",
                ),
            ),
            inventory=_inventory(),
            reviewed_overrides=_frozen_pack(review),
        )

        exact = resolve_message_owner(
            message=_message(
                webhook_id="1600000000000000910",
                application_id="1600000000000000920",
            ),
            registry=registry,
        )
        wrong = resolve_message_owner(
            message=_message(
                webhook_id="1600000000000000911",
                application_id="1600000000000000920",
            ),
            registry=registry,
        )

        self.assertTrue(exact.verified)
        self.assertFalse(wrong.verified)
        self.assertIsNone(wrong.performance_owner_id)

    def test_overlapping_accepted_rows_fail_closed_as_conflict(self) -> None:
        registry = _build_registry(
            messages=(_message(),),
            inventory=_inventory(),
            reviewed_overrides=_frozen_pack(
                _review(decision="accepted", identity_type="owner"),
                {
                    **_review(decision="accepted", identity_type="owner"),
                    "candidate_id": "e" * 64,
                },
            ),
        )
        resolution = resolve_message_owner(message=_message(), registry=registry)

        self.assertEqual(resolution.identity_type, "conflict")
        self.assertFalse(resolution.verified)
        self.assertIsNone(resolution.performance_owner_id)

    def test_registry_binds_the_exact_frozen_review_pack_sha(self) -> None:
        reviews = _frozen_pack(
            _review(decision="accepted", identity_type="owner")
        )
        registry = _build_registry(
            messages=(_message(),),
            inventory=_inventory(),
            reviewed_overrides=reviews,
        )

        self.assertEqual(
            registry["reviewed_pack_sha256"],
            canonical_json_sha256(list(reviews)),
        )

    def test_registry_rejects_a_row_subset_from_the_frozen_review_pack(
        self,
    ) -> None:
        first = _review(decision="accepted", identity_type="owner")
        second = {
            **_review(decision="accepted", identity_type="owner"),
            "candidate_id": "e" * 64,
            "author_id": "1600000000000000901",
        }
        complete = _frozen_pack(first, second)
        truncated = (complete[0], complete[1])

        with self.assertRaisesRegex(ValueError, "complete frozen review pack"):
            _build_registry(
                messages=(_message(),),
                inventory=_inventory(),
                reviewed_overrides=truncated,
            )

    def test_registry_rejects_recomputed_manifest_without_detached_pack_sha(
        self,
    ) -> None:
        first = _review(decision="accepted", identity_type="owner")
        second = {
            **_review(decision="accepted", identity_type="owner"),
            "candidate_id": "e" * 64,
            "author_id": "1600000000000000901",
        }
        complete = _frozen_pack(first, second)
        subset = _frozen_pack(first)

        with self.assertRaisesRegex(ValueError, "detached SHA-256"):
            _build_registry(
                messages=(_message(),),
                inventory=_inventory(),
                reviewed_overrides=subset,
                expected_reviewed_pack_sha256=canonical_json_sha256(
                    list(complete)
                ),
            )

    def test_mixed_target_types_fail_closed_at_the_target_boundary(self) -> None:
        second_author = "1600000000000000901"
        registry = _build_registry(
            messages=(_message(), _message(author_id=second_author)),
            inventory=_inventory(),
            reviewed_overrides=_frozen_pack(
                _review(decision="accepted", identity_type="owner"),
                _review(
                    decision="accepted",
                    identity_type="community",
                    author_id=second_author,
                    performance_owner_id=None,
                    target_type="community_chat",
                    aggregation_scope="no_performance",
                ),
            ),
        )
        target = next(
            row for row in registry["targets"] if row["target_id"] == _THREAD
        )
        parent = next(
            row for row in registry["targets"] if row["target_id"] == _PARENT
        )
        resolution = resolve_message_owner(
            message=_message(), registry=registry
        )

        self.assertEqual(target["target_type"], "unknown")
        self.assertEqual(parent["target_type"], "unknown")
        self.assertEqual(resolution.identity_type, "conflict")
        self.assertEqual(resolution.target_type, "unknown")
        self.assertIsNone(resolution.performance_owner_id)
        self.assertTrue(resolution.author_eligible)

    def test_registry_carries_all_six_reviewed_target_types(self) -> None:
        target_types = (
            "single_author_analyst",
            "multi_author_team",
            "signal_delivery_channel",
            "community_chat",
            "news_or_aggregation",
            "unknown",
        )
        target_ids = tuple(str(1700000000000000000 + index) for index in range(6))
        inventory = {
            "artifact_kind": "discord-blogger-target-inventory-v1",
            "schema_version": 1,
            "targets": [
                {
                    "target_id": target_id,
                    "kind": "GUILD_TEXT (0)",
                    "parent_id": None,
                    "count_semantics": "exact_channel",
                }
                for target_id in target_ids
            ],
        }
        configurations = (
            ("accepted", "owner", "message_owner", _AUTHOR, None),
            ("accepted", "team", "target_rollup_only", None, _AUTHOR),
            ("accepted", "proxy", "target_rollup_only", None, _AUTHOR),
            ("accepted", "community", "no_performance", None, None),
            ("accepted", "community", "no_performance", None, None),
            ("rejected", "unknown", "no_performance", None, None),
        )
        reviews = [
            _review(
                decision=decision,
                identity_type=identity_type,
                target_type=target_type,
                aggregation_scope=scope,
                performance_owner_id=performance_owner,
                aggregation_owner_id=aggregation_owner,
                target_id=target_id,
            )
            for target_id, target_type, (
                decision,
                identity_type,
                scope,
                performance_owner,
                aggregation_owner,
            ) in zip(target_ids, target_types, configurations, strict=True)
        ]
        registry = _build_registry(
            messages=tuple(
                _message(channel_id=target_id) for target_id in target_ids
            ),
            inventory=inventory,
            reviewed_overrides=_frozen_pack(*reviews),
        )

        self.assertEqual(
            {
                row["target_id"]: row["target_type"]
                for row in registry["targets"]
            },
            dict(zip(target_ids, target_types, strict=True)),
        )


if __name__ == "__main__":
    unittest.main()
