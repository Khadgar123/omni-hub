from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from omni_hub.discord_blogger_evidence import iter_verified_blogger_evidence
from omni_hub.discord_sharding import canonical_json_sha256
from tests.test_discord_blogger_corpus import _fixture, _write_json


def _enrich_closure_snapshot(root: Path, closure_path: Path) -> None:
    capture = closure_path.parent
    raw_path = capture / "raw/head/10/000001.json"
    raw = json.loads(raw_path.read_bytes())
    current = raw["response"]["messages"][1]
    current["webhook_id"] = "900"
    current["application_id"] = "901"
    current["embeds"] = [
        {"image": {"url": "https://media.invalid/current-embed.png"}}
    ]
    current["components"] = [
        {
            "type": 11,
            "media": {"url": "https://media.invalid/current-component.png"},
        }
    ]
    current["type"] = 19
    current["message_reference"] = {"message_id": "101", "channel_id": "10"}
    current["referenced_message"] = {
        "id": "101",
        "channel_id": "10",
        "timestamp": "2026-07-01T01:00:00+00:00",
        "edited_timestamp": None,
        "author": {"id": "902", "bot": True},
        "content": "nested body",
        "attachments": [
            {
                "id": "1019",
                "filename": "nested.png",
                "url": "https://media.invalid/nested.png",
            }
        ],
        "embeds": [],
        "components": [],
    }
    raw_sha = _write_json(raw_path, raw)

    evidence_path = capture / "evidence/head/10.json"
    target_evidence = json.loads(evidence_path.read_bytes())
    target_evidence["raw_pages"][0]["sha256"] = raw_sha
    target_evidence_sha = _write_json(evidence_path, target_evidence)

    head_path = capture / "head-catchup.json"
    head = json.loads(head_path.read_bytes())
    head["targets"][0]["evidence_sha256"] = target_evidence_sha
    head_sha = _write_json(head_path, head)

    closure = json.loads(closure_path.read_bytes())
    closure["input_file_sha256"]["head_catchup"] = head_sha
    closure["input_canonical_sha256"]["head_catchup"] = canonical_json_sha256(
        head
    )
    _write_json(closure_path, closure)


class VerifiedBloggerEvidenceTests(unittest.TestCase):
    def test_sqlite_uniqueness_ledger_persists_no_body_or_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            closure = _fixture(root)
            ledger_dir = root / "kept-ledger"

            class _KeptTemporaryDirectory:
                def __init__(self, *, prefix: str) -> None:
                    del prefix

                def __enter__(self) -> str:
                    ledger_dir.mkdir()
                    return str(ledger_dir)

                def __exit__(self, *_args: object) -> None:
                    return None

            with patch(
                "omni_hub.discord_blogger_evidence.tempfile.TemporaryDirectory",
                _KeptTemporaryDirectory,
            ):
                list(
                    iter_verified_blogger_evidence(
                        export_root=root,
                        closure_audit=closure.relative_to(root),
                        target_ids=("10",),
                    )
                )

            database = ledger_dir / "snapshots.sqlite3"
            database_bytes = database.read_bytes()
            with sqlite3.connect(database) as conn:
                columns = {
                    row[1]
                    for row in conn.execute(
                        "PRAGMA table_info(blogger_snapshot_provenance)"
                    )
                }

        self.assertNotIn("raw_message_json", columns)
        self.assertNotIn(b'"content":"current"', database_bytes)
        self.assertNotIn(b"https://media.invalid/x", database_bytes)

    def test_closure_snapshot_supersedes_baseline_and_keeps_both_provenances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            closure = _fixture(root)
            envelopes = list(
                iter_verified_blogger_evidence(
                    export_root=root,
                    closure_audit=closure.relative_to(root),
                    target_ids=("10",),
                )
            )

        self.assertEqual(
            [envelope.message.message_id for envelope in envelopes],
            ["101", "102", "105"],
        )
        current = envelopes[1]
        self.assertEqual(current.message.content, "current")
        self.assertEqual(
            [row.source_kind for row in current.snapshot_provenance],
            ["baseline", "closure"],
        )
        self.assertEqual(
            [row.current for row in current.snapshot_provenance],
            [False, True],
        )
        self.assertEqual(len(current.evidence.media), 1)
        self.assertTrue(
            current.evidence.media[0].source.evidence_path.endswith(
                "raw/head/10/000001.json"
            )
        )

    def test_reextracts_rich_root_and_nested_delivery_and_all_media_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            closure = _fixture(root)
            _enrich_closure_snapshot(root, closure)
            current = next(
                envelope
                for envelope in iter_verified_blogger_evidence(
                    export_root=root,
                    closure_audit=closure.relative_to(root),
                    target_ids=("10",),
                )
                if envelope.message.message_id == "102"
            )

        self.assertEqual(
            [node.kind for node in current.evidence.nodes],
            ["root", "referenced_message"],
        )
        root_delivery = current.evidence.nodes[0].attribution
        nested_delivery = current.evidence.nodes[1].attribution
        self.assertEqual(
            (
                root_delivery.kind,
                root_delivery.author_id,
                root_delivery.webhook_id,
                root_delivery.application_id,
            ),
            ("webhook", "900", "900", "901"),
        )
        self.assertEqual(current.message.webhook_id, "900")
        self.assertEqual(current.message.application_id, "901")
        self.assertEqual(nested_delivery.kind, "bot_user")
        self.assertEqual(
            {occurrence.kind for occurrence in current.evidence.media},
            {"attachment", "embed", "component"},
        )
        self.assertTrue(
            any(
                occurrence.node_key == current.evidence.nodes[1].node_key
                for occurrence in current.evidence.media
            )
        )


if __name__ == "__main__":
    unittest.main()
