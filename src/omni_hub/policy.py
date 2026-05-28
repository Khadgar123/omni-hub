from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .models import OperationSpec, RiskLevel


@dataclass(slots=True)
class PolicyConfig:
    auto_approve_until: RiskLevel = RiskLevel.LOCAL_WRITE
    external_write_allowlist: set[str] = field(default_factory=set)
    require_approval_from: RiskLevel = RiskLevel.EXTERNAL_PUBLISH
    require_sandbox_from: RiskLevel = RiskLevel.SANDBOX_EXECUTION


PolicyDecisionKind = Literal[
    "allow",
    "require_approval",
    "require_sandbox",
    "deny",
]


@dataclass(slots=True)
class PolicyDecision:
    """v0.18-D: OPA-compat structured output.

    The legacy boolean fields (``allowed``/``requires_approval``/``requires_sandbox``)
    are preserved so existing callers keep working;  new code should read
    ``decision`` and ``violations`` instead.
    """

    decision: PolicyDecisionKind = "allow"
    reason: str = ""
    violations: list[str] = field(default_factory=list)
    budget_consumed: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""

    # ---- legacy boolean projection (kept for backward compatibility) -----
    allowed: bool = True
    requires_approval: bool = False
    requires_sandbox: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PolicyEngine:
    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig()

    def evaluate(self, spec: OperationSpec) -> PolicyDecision:
        risk_level = RiskLevel.parse(spec.risk_level)
        explicit_approval = spec.approval_required
        explicit_sandbox = spec.sandbox_required
        violations: list[str] = []

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
            violations.append(f"risk_level={risk_level.code}>=L3 (EXTERNAL_PUBLISH)")
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
            if requires_approval:
                violations.append(
                    f"connector={spec.connector!r}:{spec.action!r} not in external_write_allowlist"
                )
        else:
            requires_approval = risk_level > self.config.auto_approve_until
            reason = (
                "risk level is auto-approved"
                if not requires_approval
                else "risk level exceeds auto-approval threshold"
            )
            if requires_approval:
                violations.append(
                    f"risk_level={risk_level.code} > auto_approve_until={self.config.auto_approve_until.code}"
                )

        if risk_level >= RiskLevel.SANDBOX_EXECUTION:
            requires_sandbox = True

        # v0.18-A: dry-run operations bypass approval gate at the policy
        # layer (the handler still emits a no-write ProjectionDiff).  This
        # lets users freely preview side effects without burning a human
        # review slot.  We DON'T bypass sandbox — sandboxed code shouldn't
        # run even in preview.
        if spec.dry_run:
            requires_approval = False
            reason = (reason + " · dry_run skips approval gate") if reason else "dry_run"

        # Derive OPA-shape decision from the legacy booleans.
        if requires_sandbox:
            decision: PolicyDecisionKind = "require_sandbox"
        elif requires_approval:
            decision = "require_approval"
        else:
            decision = "allow"

        return PolicyDecision(
            decision=decision,
            reason=reason,
            violations=violations,
            budget_consumed={},
            trace_id=spec.trace_id,
            allowed=not requires_approval,
            requires_approval=requires_approval,
            requires_sandbox=requires_sandbox,
        )
