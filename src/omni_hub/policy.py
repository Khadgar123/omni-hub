from __future__ import annotations

from dataclasses import dataclass, field

from .models import OperationSpec, RiskLevel


@dataclass(slots=True)
class PolicyConfig:
    auto_approve_until: RiskLevel = RiskLevel.LOCAL_WRITE
    external_write_allowlist: set[str] = field(default_factory=set)
    require_approval_from: RiskLevel = RiskLevel.EXTERNAL_PUBLISH
    require_sandbox_from: RiskLevel = RiskLevel.SANDBOX_EXECUTION


@dataclass(slots=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool
    requires_sandbox: bool
    reason: str


class PolicyEngine:
    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig()

    def evaluate(self, spec: OperationSpec) -> PolicyDecision:
        risk_level = RiskLevel.parse(spec.risk_level)
        explicit_approval = spec.approval_required
        explicit_sandbox = spec.sandbox_required

        requires_sandbox = (
            explicit_sandbox
            if explicit_sandbox is not None
            else risk_level >= self.config.require_sandbox_from
        )

        if explicit_approval is not None:
            requires_approval = explicit_approval
            reason = "explicit approval policy"
        elif risk_level >= self.config.require_approval_from:
            requires_approval = True
            reason = f"{risk_level.code} operations require human approval"
        elif risk_level == RiskLevel.EXTERNAL_SEND:
            key = f"{spec.connector}:{spec.action}"
            requires_approval = (
                spec.connector not in self.config.external_write_allowlist
                and key not in self.config.external_write_allowlist
            )
            reason = (
                "external send is allowlisted"
                if not requires_approval
                else "external send is not allowlisted"
            )
        else:
            requires_approval = risk_level > self.config.auto_approve_until
            reason = (
                "risk level is auto-approved"
                if not requires_approval
                else "risk level exceeds auto-approval threshold"
            )

        if risk_level >= RiskLevel.SANDBOX_EXECUTION:
            requires_sandbox = True

        return PolicyDecision(
            allowed=not requires_approval,
            requires_approval=requires_approval,
            requires_sandbox=requires_sandbox,
            reason=reason,
        )
