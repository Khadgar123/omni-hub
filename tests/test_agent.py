from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.agent import AgentPlanner, AgentTaskRequest, estimate_input_tokens
from omni_hub.provider_router import (
    ModelSpec,
    ProviderAccount,
    ProviderRouterStore,
    ProjectRouteOverride,
    RouteAbility,
)


class AgentPlannerTests(unittest.TestCase):
    def test_agent_plan_uses_project_route_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ProviderRouterStore(tmpdir)
            store.upsert_account(
                ProviderAccount(
                    account_id="openai-main",
                    provider="openai",
                    name="OpenAI Main",
                    base_url="https://api.openai.com/v1",
                    secret_ref="env:OPENAI_API_KEY",
                )
            )
            store.upsert_account(
                ProviderAccount(
                    account_id="anthropic-main",
                    provider="anthropic",
                    name="Anthropic Main",
                    base_url="https://api.anthropic.com/v1",
                    secret_ref="env:ANTHROPIC_API_KEY",
                )
            )
            store.upsert_model(ModelSpec(model_id="gpt-5.4", capabilities=["text"]))
            store.upsert_model(ModelSpec(model_id="claude-opus", capabilities=["text"]))
            store.upsert_ability(
                RouteAbility(
                    account_id="openai-main",
                    model_id="gpt-5.4",
                    priority=20,
                )
            )
            store.upsert_ability(
                RouteAbility(
                    account_id="anthropic-main",
                    model_id="claude-opus",
                    priority=10,
                    model_mapping="claude-opus-4.1",
                )
            )
            store.upsert_project_override(
                ProjectRouteOverride(
                    project_id="writing",
                    account_id="anthropic-main",
                    model_id="claude-opus",
                    priority=50,
                )
            )

            plan = AgentPlanner(tmpdir).plan(
                AgentTaskRequest(
                    project_id="writing",
                    task_preview="draft a post",
                    capabilities=["text"],
                    input_tokens=estimate_input_tokens("draft a post"),
                )
            )

            self.assertEqual(plan.status, "planned")
            self.assertEqual(plan.invocation["account_id"], "anthropic-main")
            self.assertEqual(plan.invocation["provider_model_id"], "claude-opus-4.1")
            self.assertIn("project_override=writing", plan.invocation["reasons"])

    def test_agent_plan_blocks_when_no_route_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = AgentPlanner(tmpdir).plan(
                AgentTaskRequest(
                    project_id="missing",
                    task_preview="analyze image",
                    capabilities=["vision"],
                )
            )

            self.assertEqual(plan.status, "blocked")
            self.assertIsNone(plan.invocation)
            self.assertIn("no route candidate", plan.error)


if __name__ == "__main__":
    unittest.main()
