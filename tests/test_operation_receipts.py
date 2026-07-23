from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omni_hub.models import OperationResult, OperationSpec, OperationStatus, RiskLevel
from omni_hub.operation_receipts import (
    OperationReceiptStore,
    ReceiptConflict,
    UncommittedReceipt,
    canonical_operation_spec_sha256,
)


class OperationReceiptStoreTests(unittest.TestCase):
    def test_committed_result_replays_and_spec_collision_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OperationReceiptStore(Path(tmp) / "receipts.sqlite3")
            spec = OperationSpec(
                name="write",
                action="apply",
                payload={"b": 2, "a": 1},
                risk_level=RiskLevel.LOCAL_WRITE,
                idempotency_key="same-key",
            )
            spec_hash = canonical_operation_spec_sha256(spec)
            self.assertIsNone(
                store.acquire(
                    "write",
                    "same-key",
                    spec_hash,
                    external_send=False,
                    reserve=True,
                )
            )
            result = OperationResult(
                operation_id=spec.operation_id,
                status=OperationStatus.SUCCEEDED,
                output={"ok": True},
                trace_id="trace-1",
            )
            store.commit("write", "same-key", spec_hash, result)
            replay = store.acquire(
                "write",
                "same-key",
                spec_hash,
                external_send=False,
                reserve=True,
            )
            assert replay is not None
            self.assertEqual(replay.output, {"ok": True})

            changed = OperationSpec(
                name="write",
                action="apply",
                payload={"a": 9},
                risk_level=RiskLevel.LOCAL_WRITE,
                idempotency_key="same-key",
            )
            with self.assertRaises(ReceiptConflict):
                store.acquire(
                    "write",
                    "same-key",
                    canonical_operation_spec_sha256(changed),
                    external_send=False,
                    reserve=True,
                )

    def test_external_send_attempt_is_separate_and_blocks_ambiguous_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OperationReceiptStore(Path(tmp) / "receipts.sqlite3")
            spec = OperationSpec(
                name="send",
                action="send",
                payload={"request_sha256": "a" * 64},
                risk_level=RiskLevel.EXTERNAL_SEND,
                idempotency_key="send-1",
            )
            spec_hash = canonical_operation_spec_sha256(spec)
            store.acquire(
                "send",
                "send-1",
                spec_hash,
                external_send=True,
                reserve=True,
            )
            with self.assertRaises(UncommittedReceipt):
                store.acquire(
                    "send",
                    "send-1",
                    spec_hash,
                    external_send=True,
                    reserve=False,
                )
            self.assertTrue(store.has_send_attempt("send", "send-1", spec_hash))


if __name__ == "__main__":
    unittest.main()
