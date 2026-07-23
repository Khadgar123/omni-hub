from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.audit import AuditLogger
from omni_hub.builtins import build_default_registry
from omni_hub.models import (
    OperationResult,
    OperationSpec,
    OperationStatus,
    RiskLevel,
)
from omni_hub.runner import OperationRunner
from omni_hub.operation_receipts import (
    OperationReceiptStore,
    canonical_operation_spec_sha256,
)
from omni_hub.policy import PolicyConfig, PolicyEngine
from omni_hub.registry import OperationRegistry


class OperationRunnerTests(unittest.TestCase):
    def test_committed_replay_precedes_changed_policy_and_missing_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = OperationReceiptStore(Path(tmpdir) / "receipts.sqlite3")
            registry = OperationRegistry()
            registry.register("once", lambda spec: {"committed": True})
            first_runner = OperationRunner(
                registry,
                audit=AuditLogger(Path(tmpdir) / "first-audit.jsonl"),
                receipts=store,
            )
            make_spec = lambda: OperationSpec(
                name="once",
                action="write",
                payload={"value": 1},
                risk_level=RiskLevel.LOCAL_WRITE,
                idempotency_key="priority-key",
            )
            first = first_runner.run(make_spec())
            self.assertEqual(first.status, OperationStatus.SUCCEEDED)

            changed_runner = OperationRunner(
                OperationRegistry(),
                policy=PolicyEngine(
                    PolicyConfig(auto_approve_until=RiskLevel.READ_ONLY)
                ),
                audit=AuditLogger(Path(tmpdir) / "changed-audit.jsonl"),
                receipts=store,
            )
            replay = changed_runner.run(make_spec())
            self.assertEqual(replay.status, OperationStatus.SUCCEEDED)
            self.assertEqual(replay.output, {"committed": True})

    def test_collision_precedes_approval_and_registry_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = OperationReceiptStore(Path(tmpdir) / "receipts.sqlite3")
            registry = OperationRegistry()
            registry.register("once", lambda spec: {"committed": True})
            first_runner = OperationRunner(
                registry,
                audit=AuditLogger(Path(tmpdir) / "first-audit.jsonl"),
                receipts=store,
            )
            first_runner.run(
                OperationSpec(
                    name="once",
                    action="write",
                    payload={"value": 1},
                    risk_level=RiskLevel.LOCAL_WRITE,
                    idempotency_key="priority-key",
                )
            )
            changed_runner = OperationRunner(
                OperationRegistry(),
                policy=PolicyEngine(
                    PolicyConfig(auto_approve_until=RiskLevel.READ_ONLY)
                ),
                audit=AuditLogger(Path(tmpdir) / "changed-audit.jsonl"),
                receipts=store,
            )
            collision = changed_runner.run(
                OperationSpec(
                    name="once",
                    action="write",
                    payload={"value": 2},
                    risk_level=RiskLevel.LOCAL_WRITE,
                    idempotency_key="priority-key",
                )
            )
            self.assertEqual(collision.status, OperationStatus.FAILED)
            self.assertIn("idempotency key collision", collision.error)

    def test_concurrent_commit_between_preflight_and_reserve_replays(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = OperationReceiptStore(Path(tmpdir) / "receipts.sqlite3")
            spec = OperationSpec(
                name="once",
                action="write",
                payload={"value": 1},
                risk_level=RiskLevel.LOCAL_WRITE,
                idempotency_key="race-key",
            )
            spec_hash = canonical_operation_spec_sha256(spec)
            committed = OperationResult(
                operation_id="other-runner",
                status=OperationStatus.SUCCEEDED,
                output={"winner": "other"},
            )

            class CommitDuringPolicy(PolicyEngine):
                def evaluate(self, evaluated_spec):
                    store.begin(
                        "once", "race-key", spec_hash, external_send=False
                    )
                    store.commit("once", "race-key", spec_hash, committed)
                    return super().evaluate(evaluated_spec)

            registry = OperationRegistry()
            registry.register(
                "once",
                lambda evaluated_spec: self.fail(
                    "handler must not run after concurrent receipt commit"
                ),
            )
            runner = OperationRunner(
                registry,
                policy=CommitDuringPolicy(),
                audit=AuditLogger(Path(tmpdir) / "audit.jsonl"),
                receipts=store,
            )
            result = runner.run(spec)
            self.assertEqual(result.status, OperationStatus.SUCCEEDED)
            self.assertEqual(result.output, {"winner": "other"})
    def test_same_idempotency_key_and_spec_replays_without_calling_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            calls = []
            registry = OperationRegistry()

            def handler(spec):
                calls.append(spec.payload)
                return {"call_count": len(calls)}

            registry.register("once", handler)
            runner = OperationRunner(
                registry,
                audit=AuditLogger(Path(tmpdir) / "audit.jsonl"),
                receipts=OperationReceiptStore(Path(tmpdir) / "receipts.sqlite3"),
            )
            first = runner.run(
                OperationSpec(
                    name="once",
                    action="write",
                    payload={"value": 1},
                    risk_level=RiskLevel.LOCAL_WRITE,
                    idempotency_key="once-1",
                )
            )
            replay = runner.run(
                OperationSpec(
                    name="once",
                    action="write",
                    payload={"value": 1},
                    risk_level=RiskLevel.LOCAL_WRITE,
                    idempotency_key="once-1",
                )
            )
            self.assertEqual(first.status, OperationStatus.SUCCEEDED)
            self.assertEqual(replay.output, first.output)
            self.assertEqual(len(calls), 1)

    def test_same_idempotency_key_with_different_spec_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            calls = []
            registry = OperationRegistry()
            registry.register("once", lambda spec: calls.append(spec.payload) or {"ok": True})
            runner = OperationRunner(
                registry,
                audit=AuditLogger(Path(tmpdir) / "audit.jsonl"),
                receipts=OperationReceiptStore(Path(tmpdir) / "receipts.sqlite3"),
            )
            runner.run(
                OperationSpec(
                    name="once", action="write", payload={"value": 1},
                    risk_level=RiskLevel.LOCAL_WRITE, idempotency_key="key",
                )
            )
            collision = runner.run(
                OperationSpec(
                    name="once", action="write", payload={"value": 2},
                    risk_level=RiskLevel.LOCAL_WRITE, idempotency_key="key",
                )
            )
            self.assertEqual(collision.status, OperationStatus.FAILED)
            self.assertIn("idempotency", collision.error)
            self.assertEqual(len(calls), 1)

    def test_external_send_exception_is_not_silently_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            calls = []
            registry = OperationRegistry()

            def uncertain_send(spec):
                calls.append(spec.payload)
                raise RuntimeError("connection dropped after send")

            registry.register("send", uncertain_send)
            runner = OperationRunner(
                registry,
                policy=PolicyEngine(
                    PolicyConfig(external_write_allowlist={"remote:send"})
                ),
                audit=AuditLogger(Path(tmpdir) / "audit.jsonl"),
                receipts=OperationReceiptStore(Path(tmpdir) / "receipts.sqlite3"),
            )
            make_spec = lambda: OperationSpec(
                name="send", connector="remote", action="send",
                payload={"request_sha256": "a" * 64},
                risk_level=RiskLevel.EXTERNAL_SEND,
                idempotency_key="send-key",
            )
            first = runner.run(make_spec())
            retry = runner.run(make_spec())
            self.assertEqual(first.status, OperationStatus.FAILED)
            self.assertEqual(retry.status, OperationStatus.FAILED)
            self.assertIn("already recorded", retry.error)
            self.assertEqual(len(calls), 1)

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
