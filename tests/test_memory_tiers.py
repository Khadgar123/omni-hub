"""Three-tier memory tests — core / recall / archival (P2-4)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.memory import MemoryStore
from omni_hub.testing import cli_runner as _run_cli


class CoreMemoryTests(unittest.TestCase):
    def test_remember_upserts_by_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            store.remember_core("user.name", "HH")
            store.remember_core("user.name", "HH (Hong)")
            entries = store.list_core()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["value"], "HH (Hong)")

    def test_forget_removes_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            store.remember_core("k1", "v1")
            store.remember_core("k2", "v2")
            self.assertTrue(store.forget_core("k1"))
            entries = {e["key"] for e in store.list_core()}
            self.assertEqual(entries, {"k2"})

    def test_forget_unknown_key_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            self.assertFalse(store.forget_core("no-such-key"))

    def test_empty_key_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            with self.assertRaises(ValueError):
                store.remember_core("   ", "anything")


class RecallMemoryTests(unittest.TestCase):
    def test_promote_appends_recall_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            store.promote_to_recall("Reduced p99 by 30%", source_kind="preference")
            rows = store.list_recall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source_kind"], "preference")

    def test_search_marks_accessed_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            store.promote_to_recall("Buffer cache 92% [1]")
            store.promote_to_recall("Unrelated content here")
            hits = store.recall_search("buffer cache")
            self.assertEqual(len(hits), 1)
            # accessed_count must increment after a search.
            again = store.recall_search("buffer cache")
            self.assertEqual(again[0]["accessed_count"], hits[0]["accessed_count"] + 1)

    def test_empty_query_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            store.promote_to_recall("anything")
            self.assertEqual(store.recall_search("  "), [])


class ArchivalMemoryTests(unittest.TestCase):
    def test_archival_search_forwards_to_full_search(self) -> None:
        from omni_hub.proposals import (
            EntityProposal,
            Proposal,
            RelationProposal,
            PENDING,
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            # Seed archival data through digest_proposal (standard path).
            proposal = Proposal(
                kind="knowledge",
                state=PENDING,
                title="Karpathy LLM Wiki",
                summary="A compiled, file-first knowledge layer for agents.",
                source_path="vault/00_Inbox/karpathy.md",
                payload={
                    "entities": [EntityProposal("LLM Wiki", "concept", "Karpathy proposal", 0.9).to_dict()],
                    "relations": [RelationProposal("LLM Wiki", "evolves", "context", "gist quote", 0.8).to_dict()],
                },
            )
            store.digest_proposal(proposal)

            hits = store.archival_search("Karpathy")
            self.assertGreaterEqual(len(hits), 1)
            self.assertTrue(any(h.title.lower() == "llm wiki" or "karpathy" in h.title.lower() for h in hits))

            # Empty query returns empty list (matches CLI semantics).
            self.assertEqual(store.archival_search(""), [])


class StatsExposesTierCountsTests(unittest.TestCase):
    def test_stats_includes_tier_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            store.remember_core("k", "v")
            store.promote_to_recall("recall content")
            stats = store.stats()
            self.assertEqual(stats["core_memory"], 1)
            self.assertEqual(stats["recall_memory"], 1)


class MemoryTierCliTests(unittest.TestCase):
    def test_remember_then_recall_core_via_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            r1 = _run_cli(workspace, [
                "memory-remember", "--key", "user.timezone", "--value", "Asia/Shanghai",
            ])
            self.assertEqual(r1["status"], "succeeded")

            r2 = _run_cli(workspace, ["memory-recall", "--tier", "core"])
            self.assertEqual(r2["status"], "succeeded")
            entries = r2["output"]["results"]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["value"], "Asia/Shanghai")

    def test_promote_then_recall_search_via_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _run_cli(workspace, [
                "memory-promote-recall",
                "--content", "Buffer cache hit rate rose to 92%",
                "--source-kind", "preference",
                "--source-id", "task-42",
                "--score", "0.9",
            ])
            result = _run_cli(workspace, [
                "memory-recall", "--tier", "recall",
                "--query", "buffer cache",
            ])
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(len(result["output"]["results"]), 1)

    def test_unknown_tier_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            result = _run_cli(workspace, ["memory-recall", "--tier", "core"])
            # core with no entries → empty list, status succeeded
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["output"]["results"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
