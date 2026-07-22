from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import unittest

from omni_hub.discord_message_evidence import extract_message_evidence
from omni_hub.discord_reference_resolution import (
    canonical_reference_resolution_bytes,
    reference_resolution_sha256,
    resolve_local_references,
)


_DISCORD_EPOCH_MS = 1_420_070_400_000


def snowflake_at(value: str, increment: int = 0) -> str:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    milliseconds = int(timestamp.timestamp() * 1000)
    return str(((milliseconds - _DISCORD_EPOCH_MS) << 22) + increment)


def message(
    timestamp: str,
    *,
    increment: int,
    channel_id: str = "100000000000000001",
    content: str = "",
) -> dict[str, object]:
    return {
        "id": snowflake_at(timestamp, increment),
        "channel_id": channel_id,
        "type": 0,
        "timestamp": timestamp,
        "edited_timestamp": None,
        "author": {
            "id": "200000000000000001",
            "username": "author",
        },
        "content": content,
        "attachments": [],
        "embeds": [],
        "components": [],
    }


def reply_without_nested(
    timestamp: str,
    *,
    increment: int,
    target: dict[str, object],
    channel_id: str = "100000000000000001",
    reference_type: int = 0,
) -> dict[str, object]:
    result = message(
        timestamp,
        increment=increment,
        channel_id=channel_id,
    )
    result["type"] = 19
    result["message_reference"] = {
        "type": reference_type,
        "channel_id": target["channel_id"],
        "message_id": target["id"],
    }
    return result


def with_nested(
    source: dict[str, object],
    target: dict[str, object] | None,
) -> dict[str, object]:
    result = deepcopy(source)
    result["referenced_message"] = deepcopy(target)
    return result


def record(
    root: dict[str, object],
    *,
    stream_suffix: str,
    claimed_message_sha256: str | None = None,
    claimed_evidence_sha256: str | None = None,
) -> dict[str, object]:
    pointer = "/payload/0"
    evidence = asdict(
        extract_message_evidence(
            root,
            stream=f"messages_{root['channel_id']}_{stream_suffix}",
            evidence_path=f"pages/{stream_suffix}/000001.json",
            evidence_sha256="a" * 64,
            json_pointer=pointer,
        )
    )
    result: dict[str, object] = {
        "message": deepcopy(root),
        "evidence": evidence,
    }
    if claimed_message_sha256 is not None:
        result["message_sha256"] = claimed_message_sha256
    if claimed_evidence_sha256 is not None:
        result["evidence_sha256"] = claimed_evidence_sha256
    return result


class DiscordReferenceResolutionTests(unittest.TestCase):
    def test_nested_unknown_binds_verified_top_level_reference_evidence(self) -> None:
        b = message("2026-01-01T00:00:00.000+00:00", increment=1)
        a_nested = reply_without_nested(
            "2026-01-01T00:01:00.000+00:00",
            increment=2,
            target=b,
        )
        a_top = with_nested(a_nested, b)
        root = with_nested(
            reply_without_nested(
                "2026-01-01T00:02:00.000+00:00",
                increment=3,
                target=a_nested,
            ),
            a_nested,
        )

        audit = resolve_local_references(
            [
                record(root, stream_suffix="root"),
                record(a_top, stream_suffix="a"),
            ]
        ).to_mapping()

        self.assertEqual(
            audit["counts"],
            {
                "raw_errors": 1,
                "unique_edges": 1,
                "occurrences": 1,
                "local_resolved": 1,
                "deleted": 0,
                "unresolved": 0,
                "effective_errors": 0,
            },
        )
        self.assertEqual(audit["edges"][0]["outcome"], "local_resolved")
        self.assertEqual(audit["edges"][0]["reason_code"], "verified_local_binding")
        self.assertEqual(audit["edges"][0]["resolution_depth"], 1)

    def test_top_level_null_is_deleted_but_absent_stays_unresolved(self) -> None:
        b = message("2026-01-02T00:00:00.000+00:00", increment=1)
        a_nested = reply_without_nested(
            "2026-01-02T00:01:00.000+00:00",
            increment=2,
            target=b,
        )
        root = with_nested(
            reply_without_nested(
                "2026-01-02T00:02:00.000+00:00",
                increment=3,
                target=a_nested,
            ),
            a_nested,
        )

        deleted = resolve_local_references(
            [
                record(root, stream_suffix="deleted-root"),
                record(with_nested(a_nested, None), stream_suffix="deleted-a"),
            ]
        ).to_mapping()
        absent = resolve_local_references(
            [
                record(root, stream_suffix="absent-root"),
                record(a_nested, stream_suffix="absent-a"),
                record(b, stream_suffix="present-b"),
            ]
        ).to_mapping()

        self.assertEqual(deleted["counts"]["deleted"], 1)
        self.assertEqual(deleted["counts"]["effective_errors"], 0)
        self.assertEqual(deleted["edges"][0]["reason_code"], "verified_deleted")
        self.assertEqual(len(deleted["edges"][0]["bindings"]), 1)
        self.assertEqual(absent["counts"]["unresolved"], 1)
        self.assertEqual(absent["counts"]["effective_errors"], 1)
        self.assertEqual(
            absent["edges"][0]["reason_code"],
            "top_level_reference_absent",
        )

    def test_missing_top_level_source_and_identity_conflict_are_blockers(self) -> None:
        b = message("2026-01-03T00:00:00.000+00:00", increment=1)
        wrong_b = message(
            "2026-01-03T00:00:01.000+00:00",
            increment=2,
            channel_id="100000000000000002",
        )
        a_nested = reply_without_nested(
            "2026-01-03T00:01:00.000+00:00",
            increment=3,
            target=b,
        )
        root = with_nested(
            reply_without_nested(
                "2026-01-03T00:02:00.000+00:00",
                increment=4,
                target=a_nested,
            ),
            a_nested,
        )
        conflicting_a = with_nested(a_nested, wrong_b)

        missing = resolve_local_references(
            [record(root, stream_suffix="missing")]
        ).to_mapping()
        conflict = resolve_local_references(
            [
                record(root, stream_suffix="conflict-root"),
                record(conflicting_a, stream_suffix="conflict-a"),
            ]
        ).to_mapping()

        self.assertEqual(
            missing["edges"][0]["reason_code"], "top_level_source_missing"
        )
        self.assertEqual(
            conflict["edges"][0]["reason_code"], "top_level_target_conflict"
        )
        self.assertEqual(missing["counts"]["effective_errors"], 1)
        self.assertEqual(conflict["counts"]["effective_errors"], 1)

    def test_multilevel_references_resolve_to_a_fixed_point(self) -> None:
        c = message("2026-01-04T00:00:00.000+00:00", increment=1)
        b_nested = reply_without_nested(
            "2026-01-04T00:01:00.000+00:00",
            increment=2,
            target=c,
        )
        b_top = with_nested(b_nested, c)
        a_nested = reply_without_nested(
            "2026-01-04T00:02:00.000+00:00",
            increment=3,
            target=b_nested,
        )
        a_top = with_nested(a_nested, b_nested)
        root = with_nested(
            reply_without_nested(
                "2026-01-04T00:03:00.000+00:00",
                increment=4,
                target=a_nested,
            ),
            a_nested,
        )

        audit = resolve_local_references(
            [
                record(root, stream_suffix="fixed-root"),
                record(a_top, stream_suffix="fixed-a"),
                record(b_top, stream_suffix="fixed-b"),
            ]
        ).to_mapping()

        self.assertEqual(audit["counts"]["raw_errors"], 2)
        self.assertEqual(audit["counts"]["unique_edges"], 2)
        self.assertEqual(audit["counts"]["local_resolved"], 2)
        self.assertEqual(audit["counts"]["effective_errors"], 0)
        depths = {
            edge["source_message_id"]: edge["resolution_depth"]
            for edge in audit["edges"]
        }
        self.assertEqual(depths[b_nested["id"]], 1)
        self.assertEqual(depths[a_nested["id"]], 2)
        parent = next(
            edge
            for edge in audit["edges"]
            if edge["source_message_id"] == a_nested["id"]
        )
        self.assertEqual(len(parent["dependency_edges"]), 1)

    def test_forged_extra_referenced_node_is_rejected_as_tamper(self) -> None:
        b = message("2026-01-05T00:00:00.000+00:00", increment=1)
        a_nested = reply_without_nested(
            "2026-01-05T00:01:00.000+00:00",
            increment=2,
            target=b,
        )
        root = with_nested(
            reply_without_nested(
                "2026-01-05T00:02:00.000+00:00",
                increment=3,
                target=a_nested,
            ),
            a_nested,
        )
        supplied = record(root, stream_suffix="tampered")
        evidence = supplied["evidence"]
        assert isinstance(evidence, dict)
        nodes = list(evidence["nodes"])
        evidence["nodes"] = nodes
        forged = deepcopy(nodes[-1])
        forged["json_pointer"] = "/payload/0/referenced_message/forged"
        nodes.append(forged)

        with self.assertRaisesRegex(ValueError, "evidence.*tamper"):
            resolve_local_references([supplied])

    def test_cycles_and_depth_overflow_fail_closed(self) -> None:
        a_seed = message("2026-01-06T00:00:00.000+00:00", increment=1)
        b_seed = message("2026-01-06T00:01:00.000+00:00", increment=2)
        a_missing = reply_without_nested(
            "2026-01-06T00:00:00.000+00:00",
            increment=1,
            target=b_seed,
        )
        b_missing = reply_without_nested(
            "2026-01-06T00:01:00.000+00:00",
            increment=2,
            target=a_seed,
        )
        cycle = resolve_local_references(
            [
                record(with_nested(a_missing, b_missing), stream_suffix="cycle-a"),
                record(with_nested(b_missing, a_missing), stream_suffix="cycle-b"),
            ]
        ).to_mapping()

        c = message("2026-01-06T01:00:00.000+00:00", increment=3)
        depth_b = reply_without_nested(
            "2026-01-06T01:01:00.000+00:00",
            increment=4,
            target=c,
        )
        depth_a = reply_without_nested(
            "2026-01-06T01:02:00.000+00:00",
            increment=5,
            target=depth_b,
        )
        depth_root = with_nested(
            reply_without_nested(
                "2026-01-06T01:03:00.000+00:00",
                increment=6,
                target=depth_a,
            ),
            depth_a,
        )
        overflow = resolve_local_references(
            [
                record(depth_root, stream_suffix="depth-root"),
                record(with_nested(depth_a, depth_b), stream_suffix="depth-a"),
                record(with_nested(depth_b, c), stream_suffix="depth-b"),
            ],
            max_depth=1,
        ).to_mapping()

        self.assertEqual(cycle["counts"]["effective_errors"], 2)
        self.assertEqual(
            {edge["reason_code"] for edge in cycle["edges"]},
            {"reference_cycle"},
        )
        self.assertEqual(overflow["counts"]["local_resolved"], 1)
        self.assertEqual(overflow["counts"]["unresolved"], 1)
        self.assertIn(
            "resolution_depth_exceeded",
            {edge["reason_code"] for edge in overflow["edges"]},
        )

    def test_snapshot_type_one_and_root_omissions_are_out_of_scope(self) -> None:
        b = message("2026-01-07T00:00:00.000+00:00", increment=1)
        a_type_one = reply_without_nested(
            "2026-01-07T00:01:00.000+00:00",
            increment=2,
            target=b,
            reference_type=1,
        )
        type_one_root = with_nested(
            reply_without_nested(
                "2026-01-07T00:02:00.000+00:00",
                increment=3,
                target=a_type_one,
            ),
            a_type_one,
        )
        snapshot_child = reply_without_nested(
            "2026-01-07T00:03:00.000+00:00",
            increment=4,
            target=b,
        )
        snapshot_root = message(
            "2026-01-07T00:04:00.000+00:00",
            increment=5,
        )
        snapshot_root["message_snapshots"] = [{"message": snapshot_child}]
        root_unknown = reply_without_nested(
            "2026-01-07T00:05:00.000+00:00",
            increment=6,
            target=b,
        )

        audit = resolve_local_references(
            [
                record(type_one_root, stream_suffix="type-one"),
                record(snapshot_root, stream_suffix="snapshot"),
                record(root_unknown, stream_suffix="root-unknown"),
            ]
        ).to_mapping()

        self.assertEqual(audit["counts"]["raw_errors"], 0)
        self.assertEqual(audit["edges"], [])

    def test_counts_distinguish_411_edges_from_432_occurrences(self) -> None:
        records: list[dict[str, object]] = []
        timestamp = "2026-01-08T00:00:00.000+00:00"
        for index in range(411):
            b = message(timestamp, increment=1_000 + index)
            a = reply_without_nested(
                timestamp,
                increment=2_000 + index,
                target=b,
            )
            records.append(
                record(with_nested(a, b), stream_suffix=f"matrix-a-{index}")
            )
            first_root = with_nested(
                reply_without_nested(
                    timestamp,
                    increment=3_000 + index,
                    target=a,
                ),
                a,
            )
            records.append(record(first_root, stream_suffix=f"matrix-r-{index}"))
            if index < 21:
                second_root = with_nested(
                    reply_without_nested(
                        timestamp,
                        increment=4_000 + index,
                        target=a,
                    ),
                    a,
                )
                records.append(
                    record(second_root, stream_suffix=f"matrix-extra-{index}")
                )

        counts = resolve_local_references(records).to_mapping()["counts"]

        self.assertEqual(counts["raw_errors"], 432)
        self.assertEqual(counts["unique_edges"], 411)
        self.assertEqual(counts["occurrences"], 432)
        self.assertEqual(counts["local_resolved"], 432)
        self.assertEqual(counts["effective_errors"], 0)

    def test_input_is_not_mutated_and_sidecar_is_deterministic_and_redacted(
        self,
    ) -> None:
        secret_content = "DO-NOT-COPY-MESSAGE-CONTENT"
        secret_url = "https://signed.example/private?secret=DO-NOT-COPY"
        b = message(
            "2026-01-09T00:00:00.000+00:00",
            increment=1,
            content=secret_content,
        )
        b["attachments"] = [
            {
                "id": "300000000000000001",
                "filename": "chart.png",
                "url": secret_url,
            }
        ]
        a = reply_without_nested(
            "2026-01-09T00:01:00.000+00:00",
            increment=2,
            target=b,
        )
        root = with_nested(
            reply_without_nested(
                "2026-01-09T00:02:00.000+00:00",
                increment=3,
                target=a,
            ),
            a,
        )
        supplied = [
            record(root, stream_suffix="immutable-root"),
            record(with_nested(a, b), stream_suffix="immutable-a"),
        ]
        before = deepcopy(supplied)

        first = resolve_local_references(supplied)
        second = resolve_local_references(list(reversed(supplied)))
        content = canonical_reference_resolution_bytes(first)

        self.assertEqual(supplied, before)
        self.assertEqual(content, canonical_reference_resolution_bytes(second))
        self.assertEqual(
            reference_resolution_sha256(first),
            hashlib.sha256(content).hexdigest(),
        )
        self.assertNotIn(secret_content.encode(), content)
        self.assertNotIn(secret_url.encode(), content)
        with self.assertRaises(TypeError):
            canonical_reference_resolution_bytes({"content": secret_content})

    def test_pure_mapping_inputs_are_order_independent_and_exact_duplicates_dedupe(
        self,
    ) -> None:
        b = message("2026-01-10T00:00:00.000+00:00", increment=1)
        a = reply_without_nested(
            "2026-01-10T00:01:00.000+00:00",
            increment=2,
            target=b,
        )
        root = with_nested(
            reply_without_nested(
                "2026-01-10T00:02:00.000+00:00",
                increment=3,
                target=a,
            ),
            a,
        )
        a_top = with_nested(a, b)

        first = resolve_local_references([root, a_top])
        second = resolve_local_references([a_top, root, deepcopy(root)])

        self.assertEqual(
            canonical_reference_resolution_bytes(first),
            canonical_reference_resolution_bytes(second),
        )
        self.assertEqual(first.to_mapping()["counts"]["occurrences"], 1)

    def test_claimed_hash_and_missing_unknown_diagnostic_are_rejected(self) -> None:
        b = message("2026-01-11T00:00:00.000+00:00", increment=1)
        a = reply_without_nested(
            "2026-01-11T00:01:00.000+00:00",
            increment=2,
            target=b,
        )
        root = with_nested(
            reply_without_nested(
                "2026-01-11T00:02:00.000+00:00",
                increment=3,
                target=a,
            ),
            a,
        )
        wrong_hash = record(
            root,
            stream_suffix="wrong-hash",
            claimed_evidence_sha256="0" * 64,
        )
        missing_diagnostic = record(root, stream_suffix="missing-diagnostic")
        evidence = missing_diagnostic["evidence"]
        assert isinstance(evidence, dict)
        evidence["diagnostics"] = []

        with self.assertRaisesRegex(ValueError, "evidence hash mismatch"):
            resolve_local_references([wrong_hash])
        with self.assertRaisesRegex(ValueError, "evidence tamper"):
            resolve_local_references([missing_diagnostic])

    def test_missing_target_evidence_is_tamper_and_conflicting_duplicates_block(
        self,
    ) -> None:
        b = message("2026-01-12T00:00:00.000+00:00", increment=1)
        a = reply_without_nested(
            "2026-01-12T00:01:00.000+00:00",
            increment=2,
            target=b,
        )
        root = with_nested(
            reply_without_nested(
                "2026-01-12T00:02:00.000+00:00",
                increment=3,
                target=a,
            ),
            a,
        )
        missing_b = record(with_nested(a, b), stream_suffix="missing-b")
        evidence = missing_b["evidence"]
        assert isinstance(evidence, dict)
        evidence["nodes"] = tuple(
            node
            for node in evidence["nodes"]
            if node["kind"] != "referenced_message"
        )
        with self.assertRaisesRegex(ValueError, "evidence tamper"):
            resolve_local_references(
                [record(root, stream_suffix="missing-b-root"), missing_b]
            )
        conflict = resolve_local_references(
            [
                record(root, stream_suffix="duplicate-root"),
                record(with_nested(a, b), stream_suffix="duplicate-map"),
                record(with_nested(a, None), stream_suffix="duplicate-null"),
            ]
        ).to_mapping()

        self.assertEqual(
            conflict["edges"][0]["reason_code"], "top_level_source_conflict"
        )
        self.assertEqual(conflict["counts"]["effective_errors"], 1)

    def test_missing_deep_expected_reference_node_fails_closed(self) -> None:
        c = message("2026-01-12T01:00:00.000+00:00", increment=11)
        b = with_nested(
            reply_without_nested(
                "2026-01-12T01:01:00.000+00:00",
                increment=12,
                target=c,
            ),
            c,
        )
        a = reply_without_nested(
            "2026-01-12T01:02:00.000+00:00",
            increment=13,
            target=b,
        )
        root = with_nested(
            reply_without_nested(
                "2026-01-12T01:03:00.000+00:00",
                increment=14,
                target=a,
            ),
            a,
        )
        a_top = record(with_nested(a, b), stream_suffix="missing-c")
        evidence = a_top["evidence"]
        assert isinstance(evidence, dict)
        evidence["nodes"] = tuple(
            node for node in evidence["nodes"] if node["message_id"] != c["id"]
        )

        with self.assertRaisesRegex(ValueError, "evidence tamper"):
            resolve_local_references(
                [record(root, stream_suffix="missing-c-root"), a_top]
            )

    def test_status_and_deleted_diagnostic_cannot_be_forged(self) -> None:
        b = message("2026-01-12T02:00:00.000+00:00", increment=21)
        a = reply_without_nested(
            "2026-01-12T02:01:00.000+00:00",
            increment=22,
            target=b,
        )
        root = with_nested(
            reply_without_nested(
                "2026-01-12T02:02:00.000+00:00",
                increment=23,
                target=a,
            ),
            a,
        )
        forged_complete = record(root, stream_suffix="forged-complete")
        forged_evidence = forged_complete["evidence"]
        assert isinstance(forged_evidence, dict)
        forged_evidence["status"] = "complete"

        deleted = record(with_nested(a, None), stream_suffix="forged-deleted")
        deleted_evidence = deleted["evidence"]
        assert isinstance(deleted_evidence, dict)
        deleted_evidence["diagnostics"] = tuple(
            diagnostic
            for diagnostic in deleted_evidence["diagnostics"]
            if diagnostic["code"] != "referenced_message_deleted"
        )

        with self.assertRaisesRegex(ValueError, "evidence tamper"):
            resolve_local_references([forged_complete])
        with self.assertRaisesRegex(ValueError, "evidence tamper"):
            resolve_local_references(
                [record(root, stream_suffix="forged-deleted-root"), deleted]
            )

    def test_zero_depth_is_a_fail_closed_audit_not_an_api_error(self) -> None:
        b = message("2026-01-13T00:00:00.000+00:00", increment=1)
        a = reply_without_nested(
            "2026-01-13T00:01:00.000+00:00",
            increment=2,
            target=b,
        )
        root = with_nested(
            reply_without_nested(
                "2026-01-13T00:02:00.000+00:00",
                increment=3,
                target=a,
            ),
            a,
        )

        audit = resolve_local_references(
            [
                record(root, stream_suffix="depth-zero-root"),
                record(with_nested(a, b), stream_suffix="depth-zero-a"),
            ],
            max_depth=0,
        ).to_mapping()

        self.assertEqual(audit["counts"]["unresolved"], 1)
        self.assertEqual(
            audit["edges"][0]["reason_code"], "resolution_depth_exceeded"
        )

    def test_invalid_nested_reply_identity_fails_closed(self) -> None:
        b = message("2026-01-14T00:00:00.000+00:00", increment=1)
        a = reply_without_nested(
            "2026-01-14T00:01:00.000+00:00",
            increment=2,
            target=b,
        )
        reference = a["message_reference"]
        assert isinstance(reference, dict)
        reference["message_id"] = "invalid"
        root = with_nested(
            reply_without_nested(
                "2026-01-14T00:02:00.000+00:00",
                increment=3,
                target=a,
            ),
            a,
        )

        with self.assertRaisesRegex(ValueError, "candidate identity"):
            resolve_local_references([record(root, stream_suffix="invalid-nested")])

    def test_non_integer_message_and_reference_types_never_enter_reply_resolution(
        self,
    ) -> None:
        variants = (
            ("float_message_type", "message_type", 19.0),
            ("float_reference_type", "reference_type", 0.0),
            ("bool_message_type", "message_type", True),
            ("bool_reference_type", "reference_type", False),
        )
        for variant, field, invalid_value in variants:
            with self.subTest(variant=variant):
                b = message("2026-01-15T00:00:00.000+00:00", increment=1)
                a = reply_without_nested(
                    "2026-01-15T00:01:00.000+00:00",
                    increment=2,
                    target=b,
                )
                if field == "message_type":
                    a["type"] = invalid_value
                else:
                    reference = a["message_reference"]
                    assert isinstance(reference, dict)
                    reference["type"] = invalid_value
                root = with_nested(
                    reply_without_nested(
                        "2026-01-15T00:02:00.000+00:00",
                        increment=3,
                        target=a,
                    ),
                    a,
                )

                audit = resolve_local_references(
                    [
                        record(root, stream_suffix=f"float-root-{variant}"),
                        record(
                            with_nested(a, b),
                            stream_suffix=f"float-a-{variant}",
                        ),
                    ]
                ).to_mapping()

                self.assertEqual(audit["counts"]["raw_errors"], 0)
                self.assertEqual(audit["edges"], [])


if __name__ == "__main__":
    unittest.main()
