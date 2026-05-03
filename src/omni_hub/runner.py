from __future__ import annotations

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
        decision = self.policy.evaluate(spec)
        self.audit.record("policy_evaluated", spec, decision=decision)

        if decision.requires_approval and not approved:
            result = OperationResult(
                operation_id=spec.operation_id,
                status=OperationStatus.WAITING_APPROVAL,
                policy_reason=decision.reason,
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
            )
            event = self.audit.record("operation_failed", spec, result=result)
            result.audit_id = event.event_id
            return result

        self.audit.record("operation_started", spec, decision=decision)

        try:
            output = handler(spec)
        except Exception as exc:
            result = OperationResult(
                operation_id=spec.operation_id,
                status=OperationStatus.FAILED,
                error=str(exc),
            )
            event = self.audit.record("operation_failed", spec, result=result)
            result.audit_id = event.event_id
            return result

        result = OperationResult(
            operation_id=spec.operation_id,
            status=OperationStatus.SUCCEEDED,
            output=output,
        )
        event = self.audit.record("operation_succeeded", spec, result=result)
        result.audit_id = event.event_id
        return result
