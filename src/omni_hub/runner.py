from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from .audit import AuditLogger
from .models import OperationResult, OperationSpec, OperationStatus, RiskLevel
from .operation_receipts import (
    OperationReceiptStore,
    ReceiptConflict,
    UncommittedReceipt,
    canonical_operation_spec_sha256,
)
from .policy import PolicyEngine
from .registry import OperationRegistry


class OperationRunner:
    def __init__(
        self,
        registry: OperationRegistry,
        policy: PolicyEngine | None = None,
        audit: AuditLogger | None = None,
        *,
        sandbox_enabled: bool = False,
        receipts: OperationReceiptStore | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy or PolicyEngine()
        self.audit = audit or AuditLogger()
        self.sandbox_enabled = sandbox_enabled
        self.receipts = receipts

    def _receipts(self) -> OperationReceiptStore:
        if self.receipts is None:
            self.receipts = OperationReceiptStore(
                _default_receipt_path(self.audit.path)
            )
        return self.receipts

    def run(self, spec: OperationSpec, *, approved: bool = False) -> OperationResult:
        # v0.18-C: ensure every Operation carries a trace_id so audit /
        # proposal / claim / preference / fts5 / skill rows can stitch
        # back to a single Command later.  In-place mutation is OK — the
        # spec is owned by the call site, not yet shared cross-thread.
        if not spec.trace_id:
            spec.trace_id = str(uuid4())

        receipt_store: OperationReceiptStore | None = None
        receipt_sha = ""
        external_send = RiskLevel.parse(spec.risk_level) == RiskLevel.EXTERNAL_SEND
        if spec.idempotency_key:
            try:
                receipt_store = self._receipts()
                receipt_sha = canonical_operation_spec_sha256(spec)
                replay = receipt_store.acquire(
                    spec.name,
                    spec.idempotency_key,
                    receipt_sha,
                    external_send=external_send,
                    reserve=False,
                )
                if replay is not None:
                    self.audit.record(
                        "operation_replayed",
                        spec,
                        result=replay,
                    )
                    return replay
            except (ReceiptConflict, UncommittedReceipt, ValueError, TypeError) as exc:
                return self._receipt_failure(spec, exc)

        decision = self.policy.evaluate(spec)
        self.audit.record("policy_evaluated", spec, decision=decision)

        if decision.requires_approval and not approved:
            result = OperationResult(
                operation_id=spec.operation_id,
                status=OperationStatus.WAITING_APPROVAL,
                policy_reason=decision.reason,
                trace_id=spec.trace_id,
            )
            event = self.audit.record(
                "operation_waiting_approval",
                spec,
                decision=decision,
                result=result,
            )
            result.audit_id = event.event_id
            return result

        if decision.requires_sandbox and not self.sandbox_enabled:
            result = OperationResult(
                operation_id=spec.operation_id,
                status=OperationStatus.BLOCKED,
                policy_reason="sandbox is required but not enabled",
                trace_id=spec.trace_id,
            )
            event = self.audit.record(
                "operation_blocked",
                spec,
                decision=decision,
                result=result,
            )
            result.audit_id = event.event_id
            return result

        # Resolve the handler before reserving a new receipt.  Unknown
        # operations must not strand a permanent "started" record.  The
        # non-reserving preflight above still gives existing replay/collision
        # receipts priority over registry changes.
        try:
            handler = self.registry.get(spec.name)
        except KeyError as exc:
            result = OperationResult(
                operation_id=spec.operation_id,
                status=OperationStatus.FAILED,
                error=str(exc),
                trace_id=spec.trace_id,
            )
            event = self.audit.record("operation_failed", spec, result=result)
            result.audit_id = event.event_id
            return result

        if receipt_store is not None and spec.idempotency_key:
            try:
                replay = receipt_store.acquire(
                    spec.name,
                    spec.idempotency_key,
                    receipt_sha,
                    external_send=external_send,
                    reserve=True,
                )
                if replay is not None:
                    self.audit.record(
                        "operation_replayed",
                        spec,
                        decision=decision,
                        result=replay,
                    )
                    return replay
            except (ReceiptConflict, UncommittedReceipt, ValueError, TypeError) as exc:
                return self._receipt_failure(spec, exc)

        # v0.18-A: when dry_run is set, audit uses a distinct event_kind so
        # log readers can filter previews out from real writes.
        start_event_kind = "operation_previewed" if spec.dry_run else "operation_started"
        self.audit.record(start_event_kind, spec, decision=decision)

        try:
            output = handler(spec)
        except Exception as exc:
            result = OperationResult(
                operation_id=spec.operation_id,
                status=OperationStatus.FAILED,
                error=str(exc),
                trace_id=spec.trace_id,
            )
            if receipt_store is not None and not external_send:
                try:
                    receipt_store.commit(
                        spec.name,
                        spec.idempotency_key or "",
                        receipt_sha,
                        result,
                    )
                except Exception as receipt_exc:
                    result.error = (
                        f"{result.error}; idempotency receipt commit failed: "
                        f"{receipt_exc}"
                    )
            event = self.audit.record("operation_failed", spec, result=result)
            result.audit_id = event.event_id
            return result

        result = OperationResult(
            operation_id=spec.operation_id,
            status=OperationStatus.SUCCEEDED,
            output=output,
            trace_id=spec.trace_id,
        )
        if receipt_store is not None:
            try:
                receipt_store.commit(
                    spec.name,
                    spec.idempotency_key or "",
                    receipt_sha,
                    result,
                )
            except Exception as exc:
                failed = OperationResult(
                    operation_id=spec.operation_id,
                    status=OperationStatus.FAILED,
                    error=f"idempotency receipt commit failed: {exc}",
                    trace_id=spec.trace_id,
                )
                event = self.audit.record("operation_failed", spec, result=failed)
                failed.audit_id = event.event_id
                return failed
        finish_event_kind = (
            "operation_preview_succeeded" if spec.dry_run else "operation_succeeded"
        )
        event = self.audit.record(finish_event_kind, spec, result=result)
        result.audit_id = event.event_id
        return result

    def _receipt_failure(
        self,
        spec: OperationSpec,
        error: Exception,
    ) -> OperationResult:
        result = OperationResult(
            operation_id=spec.operation_id,
            status=OperationStatus.FAILED,
            error=str(error),
            trace_id=spec.trace_id,
        )
        event = self.audit.record("operation_failed", spec, result=result)
        result.audit_id = event.event_id
        return result


def _default_receipt_path(audit_path: Path | str) -> Path:
    """Map every standard audit location to the workspace receipt namespace."""

    path = Path(audit_path)
    parent = path.parent
    if parent.name == "audit" and parent.parent.name == ".omni":
        return parent.parent / "operation-receipts.sqlite3"
    return parent / "operation-receipts.sqlite3"
