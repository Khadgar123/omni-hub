from __future__ import annotations

from uuid import uuid4

from .audit import AuditLogger
from .models import OperationResult, OperationSpec, OperationStatus
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
    ) -> None:
        self.registry = registry
        self.policy = policy or PolicyEngine()
        self.audit = audit or AuditLogger()
        self.sandbox_enabled = sandbox_enabled

    def run(self, spec: OperationSpec, *, approved: bool = False) -> OperationResult:
        # v0.18-C: ensure every Operation carries a trace_id so audit /
        # proposal / claim / preference / fts5 / skill rows can stitch
        # back to a single Command later.  In-place mutation is OK — the
        # spec is owned by the call site, not yet shared cross-thread.
        if not spec.trace_id:
            spec.trace_id = str(uuid4())

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
            event = self.audit.record("operation_failed", spec, result=result)
            result.audit_id = event.event_id
            return result

        result = OperationResult(
            operation_id=spec.operation_id,
            status=OperationStatus.SUCCEEDED,
            output=output,
            trace_id=spec.trace_id,
        )
        finish_event_kind = (
            "operation_preview_succeeded" if spec.dry_run else "operation_succeeded"
        )
        event = self.audit.record(finish_event_kind, spec, result=result)
        result.audit_id = event.event_id
        return result
