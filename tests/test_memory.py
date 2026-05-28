from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.audit import AuditLogger
from omni_hub.builtins import build_default_registry
from omni_hub.memory import MemoryStore
from omni_hub.models import OperationSpec, OperationStatus, RiskLevel
from omni_hub.proposals import ProposalStore
from omni_hub.runner import OperationRunner


NOTE_FIXTURE = """# 万象中枢记忆层

万象中枢需要把 OpenAI、Graphiti、Mem0、n8n 和 Obsidian 连接起来。
Proposal layer 先生成实体和关系，再进入 SQLite memory。

#memory #ai
"""


class MemoryStoreTests(unittest.TestCase):
    def test_read_only_memory_store_does_not_create_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(tmpdir, create=False)

            self.assertEqual(
                store.stats(),
                {"documents": 0, "entities": 0, "relations": 0},
            )
            self.assertEqual(store.search("anything"), [])
            self.assertFalse((Path(tmpdir) / ".omni" / "memory.sqlite3").exists())

    def test_digest_proposal_into_memory_and_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            note_path = Path(tmpdir) / "vault" / "00_Inbox" / "memory.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text(NOTE_FIXTURE, encoding="utf-8")

            runner = OperationRunner(
                build_default_registry(tmpdir),
                audit=AuditLogger(Path(tmpdir) / "audit.jsonl"),
            )

            proposal_result = runner.run(
                OperationSpec(
                    name="propose_knowledge",
                    action="write_proposal",
                    payload={"path": "vault/00_Inbox/memory.md"},
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )
            self.assertEqual(proposal_result.status, OperationStatus.SUCCEEDED)

            digest_result = runner.run(
                OperationSpec(
                    name="digest_proposal",
                    action="digest_proposal",
                    payload={"proposal": proposal_result.output["proposal_id"]},
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )
            self.assertEqual(digest_result.status, OperationStatus.SUCCEEDED)
            self.assertEqual(digest_result.output["document_count"], 1)
            self.assertGreaterEqual(digest_result.output["entity_count"], 4)

            search_result = runner.run(
                OperationSpec(
                    name="search_memory",
                    action="search",
                    payload={"query": "Graphiti", "limit": 5},
                    risk_level=RiskLevel.READ_ONLY,
                )
            )
            self.assertEqual(search_result.status, OperationStatus.SUCCEEDED)
            titles = {item["title"] for item in search_result.output["results"]}
            self.assertIn("Graphiti", titles)

    def test_digest_is_idempotent_for_same_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            note_path = Path(tmpdir) / "vault" / "00_Inbox" / "memory.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text(NOTE_FIXTURE, encoding="utf-8")

            runner = OperationRunner(
                build_default_registry(tmpdir),
                audit=AuditLogger(Path(tmpdir) / "audit.jsonl"),
            )
            proposal_result = runner.run(
                OperationSpec(
                    name="propose_knowledge",
                    action="write_proposal",
                    payload={"path": "vault/00_Inbox/memory.md"},
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )

            for _ in range(2):
                runner.run(
                    OperationSpec(
                        name="digest_proposal",
                        action="digest_proposal",
                        payload={"proposal": proposal_result.output["proposal_json_path"]},
                        risk_level=RiskLevel.LOCAL_WRITE,
                    )
                )

            stats = MemoryStore(tmpdir).stats()
            self.assertEqual(stats["documents"], 1)

    def test_proposal_store_loads_saved_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            note_path = Path(tmpdir) / "vault" / "00_Inbox" / "memory.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text(NOTE_FIXTURE, encoding="utf-8")

            runner = OperationRunner(
                build_default_registry(tmpdir),
                audit=AuditLogger(Path(tmpdir) / "audit.jsonl"),
            )
            proposal_result = runner.run(
                OperationSpec(
                    name="propose_knowledge",
                    action="write_proposal",
                    payload={"path": "vault/00_Inbox/memory.md"},
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )

            proposal = ProposalStore(tmpdir).load(proposal_result.output["proposal_id"])

            self.assertEqual(proposal.proposal_id, proposal_result.output["proposal_id"])
            self.assertEqual(proposal.source_path, "vault/00_Inbox/memory.md")


class MemoryStorePragmaTests(unittest.TestCase):
    """P0-3 regression: MemoryStore must apply busy_timeout on every connection,
    not only at schema init.  Previously _connect() didn't set the pragma,
    so search/stats calls could SQLITE_BUSY under writer contention."""

    def test_every_connection_has_busy_timeout(self) -> None:
        from omni_hub.memory import MemoryStore
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            # Two independent connections — both must have busy_timeout set.
            for _ in range(2):
                with store._connect() as conn:
                    timeout_ms = conn.execute(
                        "PRAGMA busy_timeout"
                    ).fetchone()[0]
                    self.assertEqual(int(timeout_ms), 30000)

    def test_schema_init_enables_wal(self) -> None:
        from omni_hub.memory import MemoryStore
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            with store._connect() as conn:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                self.assertEqual(mode.lower(), "wal")


if __name__ == "__main__":
    unittest.main()
