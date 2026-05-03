from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.audit import AuditLogger
from omni_hub.builtins import build_default_registry
from omni_hub.models import OperationSpec, OperationStatus, RiskLevel
from omni_hub.runner import OperationRunner


class OperationRunnerTests(unittest.TestCase):
    def test_runs_read_only_operation_and_writes_audit_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            runner = OperationRunner(
                build_default_registry(tmpdir),
                audit=AuditLogger(audit_path),
            )

            result = runner.run(
                OperationSpec(
                    name="summarize_text",
                    action="summarize",
                    payload={"text": "hello world", "max_chars": 5},
                    risk_level=RiskLevel.READ_ONLY,
                )
            )

            self.assertEqual(result.status, OperationStatus.SUCCEEDED)
            self.assertEqual(result.output["summary"], "hello...")
            self.assertTrue(audit_path.exists())

    def test_external_publish_waits_for_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = OperationRunner(
                build_default_registry(tmpdir),
                audit=AuditLogger(Path(tmpdir) / "audit.jsonl"),
            )

            result = runner.run(
                OperationSpec(
                    name="summarize_text",
                    connector="x",
                    action="publish",
                    payload={"text": "draft"},
                    risk_level=RiskLevel.EXTERNAL_PUBLISH,
                )
            )

            self.assertEqual(result.status, OperationStatus.WAITING_APPROVAL)

    def test_write_markdown_stays_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = OperationRunner(
                build_default_registry(tmpdir),
                audit=AuditLogger(Path(tmpdir) / "audit.jsonl"),
            )

            result = runner.run(
                OperationSpec(
                    name="write_markdown",
                    action="write",
                    payload={
                        "path": "vault/00_Inbox/test.md",
                        "title": "Test",
                        "body": "Body",
                    },
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )

            self.assertEqual(result.status, OperationStatus.SUCCEEDED)
            self.assertTrue((Path(tmpdir) / "vault/00_Inbox/test.md").exists())

    def test_write_markdown_blocks_workspace_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = OperationRunner(
                build_default_registry(tmpdir),
                audit=AuditLogger(Path(tmpdir) / "audit.jsonl"),
            )

            result = runner.run(
                OperationSpec(
                    name="write_markdown",
                    action="write",
                    payload={
                        "path": "../escaped.md",
                        "title": "Nope",
                        "body": "Body",
                    },
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )

            self.assertEqual(result.status, OperationStatus.FAILED)
            self.assertIn("outside the workspace", result.error)


if __name__ == "__main__":
    unittest.main()
