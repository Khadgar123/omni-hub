from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.models import OperationSpec, RiskLevel
from omni_hub.policy import PolicyConfig, PolicyEngine


class PolicyEngineTests(unittest.TestCase):
    def test_local_write_is_auto_approved(self) -> None:
        decision = PolicyEngine().evaluate(
            OperationSpec(
                name="write_markdown",
                action="write",
                risk_level=RiskLevel.LOCAL_WRITE,
            )
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.requires_approval)

    def test_external_publish_requires_approval(self) -> None:
        decision = PolicyEngine().evaluate(
            OperationSpec(
                name="publish_post",
                connector="x",
                action="publish",
                risk_level=RiskLevel.EXTERNAL_PUBLISH,
            )
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.requires_approval)

    def test_allowlisted_external_send_is_allowed(self) -> None:
        policy = PolicyEngine(
            PolicyConfig(external_write_allowlist={"feishu:send_message"})
        )

        decision = policy.evaluate(
            OperationSpec(
                name="send_message",
                connector="feishu",
                action="send_message",
                risk_level=RiskLevel.EXTERNAL_SEND,
            )
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.requires_approval)

    def test_sandbox_execution_requires_sandbox_and_approval(self) -> None:
        decision = PolicyEngine().evaluate(
            OperationSpec(
                name="run_shell",
                action="execute",
                risk_level=RiskLevel.SANDBOX_EXECUTION,
            )
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.requires_approval)
        self.assertTrue(decision.requires_sandbox)


if __name__ == "__main__":
    unittest.main()
