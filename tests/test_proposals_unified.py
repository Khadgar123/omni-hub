"""Tests for the unified Proposal[T] model + propose-list/approve/reject CLI.

The original test_vault_proposals.py and test_memory.py exercise the
knowledge-proposal flow end-to-end; this file focuses on the new
SQLite-backed ProposalStore and the approval CLI introduced in Φ1-T1.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.cli import main
from omni_hub.proposals import (
    APPROVED,
    PENDING,
    REJECTED,
    Proposal,
    ProposalStore,
    duplicate_proposal,
    stale_proposal,
)


class _Record:
    def __init__(self, source_path: str, title: str, summary: str, updated_at: str = "") -> None:
        self.source_path = source_path
        self.title = title
        self.summary = summary
        self.updated_at = updated_at


def _run_cli(workspace: Path, argv: list[str]) -> dict:
    buffer = StringIO()
    original = os.getcwd()
    try:
        os.chdir(workspace)
        with redirect_stdout(buffer):
            exit_code = main(argv)
    finally:
        os.chdir(original)
    payload = json.loads(buffer.getvalue())
    payload["__exit"] = exit_code
    return payload


class ProposalStoreTests(unittest.TestCase):
    def test_store_and_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProposalStore(tmp)
            proposal = Proposal(
                kind="knowledge",
                title="hello",
                summary="world",
                source_path="vault/x.md",
                payload={"entities": [], "relations": []},
            )
            store.store(proposal, write_card=False)
            loaded = store.load(proposal.proposal_id)
            self.assertEqual(loaded.title, "hello")
            self.assertEqual(loaded.kind, "knowledge")
            self.assertEqual(loaded.state, PENDING)

    def test_list_filters_by_state_and_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProposalStore(tmp)
            store.store(Proposal(kind="knowledge", title="a"), write_card=False)
            store.store(Proposal(kind="duplicate", title="b"), write_card=False)
            store.store(Proposal(kind="duplicate", title="c"), write_card=False)
            self.assertEqual(len(store.list()), 3)
            self.assertEqual(len(store.list(kind="duplicate")), 2)
            self.assertEqual(len(store.list(state="approved")), 0)

    def test_approve_transitions_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProposalStore(tmp)
            proposal = Proposal(kind="knowledge", title="x")
            store.store(proposal, write_card=False)
            approved = store.approve(proposal.proposal_id, reason="ok")
            self.assertEqual(approved.state, APPROVED)
            self.assertEqual(approved.reason, "ok")
            self.assertIsNotNone(approved.decided_at)
            self.assertEqual(store.counts_by_state()["approved"], 1)

    def test_reject_transitions_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProposalStore(tmp)
            proposal = Proposal(kind="duplicate")
            store.store(proposal, write_card=False)
            rejected = store.reject(proposal.proposal_id, reason="not a dup")
            self.assertEqual(rejected.state, REJECTED)

    def test_load_unknown_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProposalStore(tmp)
            with self.assertRaises(FileNotFoundError):
                store.load("nonexistent-id")


class RedundancyFactoryTests(unittest.TestCase):
    def test_duplicate_factory_marks_kind_and_paths(self) -> None:
        rec_a = _Record("a", "T", "S")
        rec_b = _Record("b", "T", "S")
        proposal = duplicate_proposal([rec_a, rec_b])
        self.assertEqual(proposal.kind, "duplicate")
        self.assertEqual(proposal.suggested_action, "merge_proposal")
        self.assertEqual(set(proposal.source_paths), {"a", "b"})

    def test_stale_factory_records_freshness(self) -> None:
        rec = _Record("old.md", "Old", "Old", "2020-01-01")
        proposal = stale_proposal(rec, freshness_days=365)
        self.assertEqual(proposal.kind, "stale")
        self.assertIn("365 days", proposal.summary)


class ProposalCliTests(unittest.TestCase):
    def test_list_approve_reject_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            # Seed two proposals directly through the store.
            store = ProposalStore(workspace)
            p1 = Proposal(kind="duplicate", title="one")
            p2 = Proposal(kind="stale", title="two")
            store.store(p1, write_card=False)
            store.store(p2, write_card=False)

            listed = _run_cli(workspace, ["propose-list"])
            self.assertEqual(listed["status"], "succeeded")
            self.assertEqual(listed["output"]["count"], 2)

            filtered = _run_cli(workspace, ["propose-list", "--kind", "duplicate"])
            self.assertEqual(filtered["output"]["count"], 1)

            approved = _run_cli(workspace, [
                "propose-approve", "--id", p1.proposal_id, "--reason", "good dup",
            ])
            self.assertEqual(approved["status"], "succeeded")
            self.assertEqual(approved["output"]["state"], "approved")
            self.assertTrue(approved["audit_id"])

            rejected = _run_cli(workspace, [
                "propose-reject", "--id", p2.proposal_id, "--reason", "still fresh",
            ])
            self.assertEqual(rejected["output"]["state"], "rejected")

            # Listing pending should now be empty.
            pending = _run_cli(workspace, ["propose-list", "--state", "pending"])
            self.assertEqual(pending["output"]["count"], 0)

    def test_approve_unknown_id_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ProposalStore(Path(tmp))  # init schema
            res = _run_cli(Path(tmp), [
                "propose-approve", "--id", "does-not-exist",
            ])
            self.assertEqual(res["status"], "failed")
            self.assertIn("does-not-exist", res["error"])


class RedundancyToProposalStoreTests(unittest.TestCase):
    def test_redundancy_scan_persists_into_proposal_store(self) -> None:
        import sqlite3
        from omni_hub.harness import redundancy

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db = workspace / ".omni" / "memory.sqlite3"
            db.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "CREATE TABLE documents (source_path TEXT PRIMARY KEY, "
                    "title TEXT, summary TEXT, updated_at TEXT)"
                )
                conn.executemany(
                    "INSERT INTO documents VALUES (?, ?, ?, ?)",
                    [
                        ("a", "Same Title", "Same body.", "2026-05-20"),
                        ("b", "Same Title", "Same body.", "2026-05-20"),
                    ],
                )

            redundancy.scan(
                db_path=db,
                prefer_backend="local",
                write_to=None,                # skip jsonl mirror
            )

            store = ProposalStore(workspace, create=False)
            proposals = store.list(kind="duplicate")
            self.assertGreaterEqual(len(proposals), 1)
            self.assertEqual(proposals[0].state, "pending")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
