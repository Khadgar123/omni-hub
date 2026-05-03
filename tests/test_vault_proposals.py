from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.audit import AuditLogger
from omni_hub.builtins import build_default_registry
from omni_hub.models import OperationSpec, OperationStatus, RiskLevel
from omni_hub.runner import OperationRunner
from omni_hub.vault import VaultReader


NOTE_FIXTURE = """---
omni_type: captured_url
source_url: "https://example.com"
---

# 万象中枢架构

万象中枢连接 OpenAI、GitHub、Obsidian 和飞书。
它需要自动化工作流、审批和审计。

参考 [[Graphiti]] 和 [Temporal](https://temporal.io)。

#ai #automation
"""


class VaultProposalTests(unittest.TestCase):
    def test_vault_reader_lists_and_reads_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            note_path = Path(tmpdir) / "vault" / "00_Inbox" / "note.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text(NOTE_FIXTURE, encoding="utf-8")

            reader = VaultReader(tmpdir)
            notes = reader.list_notes()
            document = reader.read_note("vault/00_Inbox/note.md")

            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0].title, "万象中枢架构")
            self.assertIn("Graphiti", document.wiki_links)

    def test_propose_knowledge_operation_writes_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            note_path = Path(tmpdir) / "vault" / "00_Inbox" / "note.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text(NOTE_FIXTURE, encoding="utf-8")

            runner = OperationRunner(
                build_default_registry(tmpdir),
                audit=AuditLogger(Path(tmpdir) / "audit.jsonl"),
            )

            result = runner.run(
                OperationSpec(
                    name="propose_knowledge",
                    action="write_proposal",
                    payload={"path": "vault/00_Inbox/note.md"},
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )

            self.assertEqual(result.status, OperationStatus.SUCCEEDED)
            self.assertTrue(
                (Path(tmpdir) / result.output["proposal_json_path"]).exists()
            )
            entity_names = {entity["name"] for entity in result.output["entities"]}
            self.assertIn("OpenAI", entity_names)
            self.assertIn("Graphiti", entity_names)

    def test_vault_read_blocks_workspace_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reader = VaultReader(tmpdir)

            with self.assertRaises(PermissionError):
                reader.read_note("../outside.md")


if __name__ == "__main__":
    unittest.main()
