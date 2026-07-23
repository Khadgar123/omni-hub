from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from omni_hub.discord_blogger_contract import (
    canonical_json_bytes,
    canonical_json_sha256,
)
from omni_hub.discord_blogger_corpus import BloggerMessage
from omni_hub.discord_blogger_evidence import (
    SnapshotProvenance,
    VerifiedMessageEnvelope,
)
from omni_hub.discord_blogger_identity_review import (
    build_identity_review_pack,
    freeze_identity_review_pack,
)
from omni_hub.discord_message_evidence import extract_message_evidence


_TARGET = "1516770209279512618"
_AUTHOR = "1600000000000000900"


def _envelope(
    message_id: str,
    timestamp: str,
    *,
    webhook_id: str | None = None,
    application_id: str | None = None,
) -> VerifiedMessageEnvelope:
    raw: dict[str, object] = {
        "id": message_id,
        "channel_id": _TARGET,
        "timestamp": timestamp,
        "edited_timestamp": None,
        "author": {"id": _AUTHOR, "username": "display-name-must-not-persist"},
        "content": "SECRET https://media.invalid/signed?token=secret",
        "attachments": [
            {
                "id": f"{message_id}9",
                "filename": "secret.png",
                "url": "https://media.invalid/secret.png",
            }
        ],
        "embeds": [],
        "components": [],
    }
    if webhook_id is not None:
        raw["webhook_id"] = webhook_id
    if application_id is not None:
        raw["application_id"] = application_id
    snapshot_sha = hashlib.sha256(canonical_json_bytes(raw)).hexdigest()
    message = BloggerMessage(
        message_id=message_id,
        channel_id=_TARGET,
        author_id=_AUTHOR,
        timestamp=timestamp,
        edited_timestamp=None,
        content=str(raw["content"]),
        reply_message_id=None,
        snapshot_ref="private/raw.json#/0",
        snapshot_sha256=snapshot_sha,
        media_occurrence_refs=("private/evidence.jsonl#/1/media/0",),
    )
    evidence = extract_message_evidence(
        raw,
        stream=f"messages_{_TARGET}",
        evidence_path="private/raw.json",
        evidence_sha256=snapshot_sha,
        json_pointer="/0",
    )
    return VerifiedMessageEnvelope(
        message=message,
        evidence=evidence,
        snapshot_provenance=(
            SnapshotProvenance(
                source_kind="baseline",
                snapshot_ref="private/raw.json#/0",
                snapshot_sha256=snapshot_sha,
                evidence_path="private/raw.json",
                evidence_sha256=snapshot_sha,
                current=True,
            ),
        ),
    )


def _candidate_pack() -> dict[str, object]:
    return dict(
        build_identity_review_pack(
            evidence=(
                _envelope(
                    "1600000000000000001",
                    "2026-07-01T00:00:00+00:00",
                    webhook_id=_AUTHOR,
                    application_id="1600000000000000901",
                ),
                _envelope(
                    "1600000000000000002",
                    "2026-07-02T00:00:00+00:00",
                    webhook_id=_AUTHOR,
                    application_id="1600000000000000901",
                ),
            ),
            inventory={
                "artifact_kind": "discord-blogger-target-inventory-v1",
                "schema_version": 1,
                "targets": [
                    {
                        "target_id": _TARGET,
                        "parent_id": "1514503993567744030",
                        "kind": "GUILD_PUBLIC_THREAD (11)",
                        "count_semantics": "exact_thread",
                    }
                ],
            },
        )
    )


def _labels(candidate: dict[str, object]) -> dict[str, object]:
    candidate_sha = hashlib.sha256(canonical_json_bytes(candidate)).hexdigest()
    row = candidate["candidates"][0]
    return {
        "schema_version": 1,
        "candidate_pack_sha256": candidate_sha,
        "labels": [
            {
                "candidate_id": row["candidate_id"],
                "decision": "accepted",
                "identity_type": "proxy",
                "target_type": "signal_delivery_channel",
                "aggregation_scope": "target_rollup_only",
                "performance_owner_id": None,
                "aggregation_owner_id": _AUTHOR,
                "reviewer": "reviewer-id-1",
                "reviewed_at": "2026-07-22T12:00:00+00:00",
                "evidence_sha256": row["evidence_sha256"],
            }
        ],
    }


class IdentityReviewPackTests(unittest.TestCase):
    def test_census_uses_exact_delivery_ids_and_redacted_commitments(self) -> None:
        candidate = _candidate_pack()
        row = candidate["candidates"][0]
        serialized = json.dumps(candidate, ensure_ascii=False, sort_keys=True)

        self.assertEqual(row["target_id"], _TARGET)
        self.assertEqual(row["author_id"], _AUTHOR)
        self.assertEqual(row["webhook_id"], _AUTHOR)
        self.assertEqual(row["application_id"], "1600000000000000901")
        self.assertEqual(row["observed_from"], "2026-07-01T00:00:00+00:00")
        self.assertEqual(row["observed_to"], "2026-07-02T00:00:00+00:00")
        self.assertEqual(row["valid_from"], "2026-07-01T00:00:00+00:00")
        self.assertIsNone(row["valid_to"])
        self.assertEqual(row["message_count"], 2)
        self.assertNotIn("display-name", serialized)
        self.assertNotIn("SECRET", serialized)
        self.assertNotIn("https://", serialized)
        self.assertNotIn("message_ids", serialized)

    def test_freeze_is_hash_bound_private_idempotent_and_no_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / ".omni/private/discord-blogger/identity"
            private.mkdir(parents=True)
            candidate = _candidate_pack()
            labels = _labels(candidate)
            candidate_path = private / "candidate.json"
            labels_path = private / "labels.json"
            output_path = private / "reviewed.json"
            candidate_path.write_bytes(canonical_json_bytes(candidate))
            labels_path.write_bytes(canonical_json_bytes(labels))
            candidate_path.chmod(0o600)
            labels_path.chmod(0o600)

            first = freeze_identity_review_pack(
                candidate_pack=candidate_path,
                reviewed_labels=labels_path,
                output_path=output_path,
            )
            second = freeze_identity_review_pack(
                candidate_pack=candidate_path,
                reviewed_labels=labels_path,
                output_path=output_path,
            )

            self.assertEqual(first, second)
            self.assertEqual(output_path.stat().st_mode & 0o777, 0o600)
            frozen = json.loads(output_path.read_bytes())
            self.assertIsInstance(frozen, list)
            self.assertEqual(len(frozen), 2)
            self.assertEqual(
                first["output_sha256"],
                canonical_json_sha256(frozen),
            )
            self.assertEqual(
                frozen[0]["reviewed_row_count"],
                1,
            )
            self.assertEqual(
                frozen[1]["candidate_pack_sha256"],
                labels["candidate_pack_sha256"],
            )
            self.assertEqual(frozen[1]["decision"], "accepted")

            changed_candidate = dict(candidate)
            changed_candidate["generated_at"] = "2026-07-23T00:00:00+00:00"
            candidate_path.write_bytes(canonical_json_bytes(changed_candidate))
            with self.assertRaisesRegex(ValueError, "candidate pack SHA-256"):
                freeze_identity_review_pack(
                    candidate_pack=candidate_path,
                    reviewed_labels=labels_path,
                    output_path=private / "drift.json",
                )

            candidate_path.write_bytes(canonical_json_bytes(candidate))
            changed_labels = _labels(candidate)
            changed_labels["labels"][0]["decision"] = "rejected"
            changed_labels["labels"][0]["identity_type"] = "unknown"
            changed_labels["labels"][0]["target_type"] = "unknown"
            changed_labels["labels"][0]["aggregation_scope"] = "no_performance"
            changed_labels["labels"][0]["performance_owner_id"] = None
            changed_labels["labels"][0]["aggregation_owner_id"] = None
            labels_path.write_bytes(canonical_json_bytes(changed_labels))
            with self.assertRaises(FileExistsError):
                freeze_identity_review_pack(
                    candidate_pack=candidate_path,
                    reviewed_labels=labels_path,
                    output_path=output_path,
                )

    def test_freeze_requires_a_review_row_for_every_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / ".omni/private/discord-blogger/identity"
            private.mkdir(parents=True)
            candidate = _candidate_pack()
            second = replace(
                _envelope(
                    "1600000000000000003",
                    "2026-07-03T00:00:00+00:00",
                ).message,
                author_id="1600000000000000999",
            )
            candidate["candidates"].append(
                {
                    **candidate["candidates"][0],
                    "candidate_id": "f" * 64,
                    "author_id": second.author_id,
                }
            )
            candidate["candidate_count"] = 2
            labels = _labels(candidate)
            candidate_path = private / "candidate.json"
            labels_path = private / "labels.json"
            candidate_path.write_bytes(canonical_json_bytes(candidate))
            labels_path.write_bytes(canonical_json_bytes(labels))
            candidate_path.chmod(0o600)
            labels_path.chmod(0o600)

            with self.assertRaisesRegex(ValueError, "every candidate"):
                freeze_identity_review_pack(
                    candidate_pack=candidate_path,
                    reviewed_labels=labels_path,
                    output_path=private / "reviewed.json",
                )

    def test_freeze_rejects_candidate_or_labels_without_private_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / ".omni/private/discord-blogger/identity"
            private.mkdir(parents=True)
            candidate = _candidate_pack()
            labels = _labels(candidate)
            candidate_path = private / "candidate.json"
            labels_path = private / "labels.json"
            candidate_path.write_bytes(canonical_json_bytes(candidate))
            labels_path.write_bytes(canonical_json_bytes(labels))
            candidate_path.chmod(0o644)
            labels_path.chmod(0o600)

            with self.assertRaisesRegex(ValueError, "0600"):
                freeze_identity_review_pack(
                    candidate_pack=candidate_path,
                    reviewed_labels=labels_path,
                    output_path=private / "reviewed.json",
                )

    def test_freeze_forces_mode_0600_under_restrictive_umask(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / ".omni/private/discord-blogger/identity"
            private.mkdir(parents=True)
            candidate = _candidate_pack()
            labels = _labels(candidate)
            candidate_path = private / "candidate.json"
            labels_path = private / "labels.json"
            output_path = private / "reviewed.json"
            candidate_path.write_bytes(canonical_json_bytes(candidate))
            labels_path.write_bytes(canonical_json_bytes(labels))
            candidate_path.chmod(0o600)
            labels_path.chmod(0o600)

            previous_umask = os.umask(0o777)
            try:
                first = freeze_identity_review_pack(
                    candidate_pack=candidate_path,
                    reviewed_labels=labels_path,
                    output_path=output_path,
                )
                second = freeze_identity_review_pack(
                    candidate_pack=candidate_path,
                    reviewed_labels=labels_path,
                    output_path=output_path,
                )
            finally:
                os.umask(previous_umask)

            self.assertEqual(first, second)
            self.assertEqual(output_path.stat().st_mode & 0o777, 0o600)

    def test_freeze_rejects_a_symlinked_private_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            private = root / ".omni/private/discord-blogger"
            private.mkdir(parents=True)
            (private / "identity").symlink_to(
                outside, target_is_directory=True
            )
            candidate = _candidate_pack()
            labels = _labels(candidate)
            candidate_path = outside / "candidate.json"
            labels_path = outside / "labels.json"
            candidate_path.write_bytes(canonical_json_bytes(candidate))
            labels_path.write_bytes(canonical_json_bytes(labels))
            candidate_path.chmod(0o600)
            labels_path.chmod(0o600)

            with self.assertRaisesRegex(ValueError, "symbolic link"):
                freeze_identity_review_pack(
                    candidate_pack=private / "identity/candidate.json",
                    reviewed_labels=private / "identity/labels.json",
                    output_path=private / "identity/reviewed.json",
                )

    def test_freeze_rejects_lexical_private_prefix_escape(self) -> None:
        for escaped_field in ("candidate", "labels", "output"):
            with self.subTest(escaped_field=escaped_field):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    private = (
                        root / ".omni/private/discord-blogger/identity"
                    )
                    outside = root / "outside"
                    private.mkdir(parents=True)
                    outside.mkdir()
                    candidate = _candidate_pack()
                    labels = _labels(candidate)
                    candidate_path = private / "candidate.json"
                    labels_path = private / "labels.json"
                    output_path = private / "reviewed.json"
                    candidate_path.write_bytes(canonical_json_bytes(candidate))
                    labels_path.write_bytes(canonical_json_bytes(labels))
                    candidate_path.chmod(0o600)
                    labels_path.chmod(0o600)
                    escaped = (
                        root
                        / ".omni/private/../../outside"
                        / {
                            "candidate": "candidate.json",
                            "labels": "labels.json",
                            "output": "reviewed.json",
                        }[escaped_field]
                    )
                    if escaped_field == "candidate":
                        escaped.resolve().write_bytes(
                            canonical_json_bytes(candidate)
                        )
                        escaped.resolve().chmod(0o600)
                        candidate_path = escaped
                    elif escaped_field == "labels":
                        escaped.resolve().write_bytes(
                            canonical_json_bytes(labels)
                        )
                        escaped.resolve().chmod(0o600)
                        labels_path = escaped
                    else:
                        output_path = escaped

                    with self.assertRaisesRegex(
                        ValueError, "escape|unsafe"
                    ):
                        freeze_identity_review_pack(
                            candidate_pack=candidate_path,
                            reviewed_labels=labels_path,
                            output_path=output_path,
                        )

                    self.assertFalse((outside / "reviewed.json").exists())

    def test_freeze_rejects_parent_swap_after_path_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity = root / ".omni/private/discord-blogger/identity"
            outside = root / "outside"
            identity.mkdir(parents=True)
            outside.mkdir()
            candidate = _candidate_pack()
            labels = _labels(candidate)
            candidate_path = identity / "candidate.json"
            labels_path = identity / "labels.json"
            output_path = identity / "reviewed.json"
            candidate_path.write_bytes(canonical_json_bytes(candidate))
            labels_path.write_bytes(canonical_json_bytes(labels))
            candidate_path.chmod(0o600)
            labels_path.chmod(0o600)

            from omni_hub import discord_blogger_identity_review as review_module

            original = review_module._private_path
            calls = 0

            def swap_after_validation(
                value: Path, label: str, *, must_exist: bool = True
            ) -> Path:
                nonlocal calls
                result = original(value, label, must_exist=must_exist)
                calls += 1
                if calls == 3:
                    identity.rename(root / "retained")
                    identity.symlink_to(outside, target_is_directory=True)
                return result

            with patch.object(
                review_module,
                "_private_path",
                side_effect=swap_after_validation,
            ):
                with self.assertRaisesRegex(
                    ValueError, "symbolic link|unsafe"
                ):
                    freeze_identity_review_pack(
                        candidate_pack=candidate_path,
                        reviewed_labels=labels_path,
                        output_path=output_path,
                    )

            self.assertFalse((outside / "reviewed.json").exists())


if __name__ == "__main__":
    unittest.main()
