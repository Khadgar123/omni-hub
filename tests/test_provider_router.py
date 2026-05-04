from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.audit import AuditLogger
from omni_hub.builtins import build_default_registry
from omni_hub.models import OperationSpec, OperationStatus, RiskLevel
from omni_hub.provider_router import (
    HealthStatus,
    ModelSpec,
    ProviderAccount,
    ProviderHealth,
    ProviderRouterStore,
    ProjectModelOrder,
    ProjectRouteOverride,
    ProjectRouteProfile,
    RouteAbility,
    RouteRequest,
)
from omni_hub.runner import OperationRunner


class ProviderRouterTests(unittest.TestCase):
    def test_read_only_store_does_not_create_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ProviderRouterStore(tmpdir, create=False)

            self.assertEqual(store.route(RouteRequest()).to_dict()["selected"], None)
            self.assertEqual(
                store.stats(),
                {
                    "provider_accounts": 0,
                    "model_catalog": 0,
                    "route_abilities": 0,
                    "project_route_profiles": 0,
                    "project_model_orders": 0,
                    "project_route_overrides": 0,
                    "provider_health": 0,
                    "usage_request_logs": 0,
                },
            )
            self.assertFalse(
                (Path(tmpdir) / ".omni" / "provider-router.sqlite3").exists()
            )

    def test_rejects_raw_secret(self) -> None:
        with self.assertRaises(ValueError):
            ProviderAccount(
                account_id="openai-main",
                provider="openai",
                name="OpenAI Main",
                base_url="https://api.openai.com/v1",
                secret_ref="sk-test-raw-secret",
            )

    def test_accepts_local_secret_ref(self) -> None:
        account = ProviderAccount(
            account_id="openai-main",
            provider="openai",
            name="OpenAI Main",
            base_url="https://api.openai.com/v1",
            secret_ref="local:omni-hub/openai-main",
        )

        self.assertEqual(account.secret_ref, "local:omni-hub/openai-main")

    def test_routes_by_priority_capability_budget_and_health(self) -> None:
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
                    account_id="local-ollama",
                    provider="ollama",
                    name="Local Ollama",
                    base_url="http://127.0.0.1:11434/v1",
                )
            )
            store.upsert_model(
                ModelSpec(
                    model_id="gpt-5.4",
                    capabilities=["text", "tools", "vision"],
                    context_window=400000,
                    input_usd_per_million=2.0,
                    output_usd_per_million=10.0,
                    supports_batch=True,
                )
            )
            store.upsert_model(
                ModelSpec(
                    model_id="local-small",
                    capabilities=["text"],
                    context_window=32000,
                    input_usd_per_million=0.0,
                    output_usd_per_million=0.0,
                )
            )
            store.upsert_ability(
                RouteAbility(
                    account_id="openai-main",
                    model_id="gpt-5.4",
                    priority=20,
                    weight=1.0,
                )
            )
            store.upsert_ability(
                RouteAbility(
                    account_id="local-ollama",
                    model_id="local-small",
                    priority=50,
                    weight=1.0,
                )
            )
            store.set_health(
                ProviderHealth(
                    account_id="openai-main",
                    model_id="gpt-5.4",
                    status=HealthStatus.HEALTHY,
                    latency_ms=300,
                )
            )

            decision = store.route(
                RouteRequest(
                    capabilities=["vision", "tools"],
                    input_tokens=1000,
                    output_tokens=500,
                    max_cost_usd=0.01,
                    require_batch=True,
                )
            )

            self.assertIsNotNone(decision.selected)
            self.assertEqual(decision.selected.account.account_id, "openai-main")
            rejected_reasons = {item["reason"] for item in decision.rejected}
            self.assertIn("missing capabilities: tools, vision", rejected_reasons)

            too_expensive = store.route(
                RouteRequest(
                    capabilities=["vision"],
                    input_tokens=1000,
                    output_tokens=500,
                    max_cost_usd=0.0001,
                )
            )
            self.assertIsNone(too_expensive.selected)
            self.assertTrue(
                any(
                    "exceeds max_cost_usd" in item["reason"]
                    for item in too_expensive.rejected
                )
            )

    def test_health_down_removes_candidate(self) -> None:
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
            store.upsert_model(
                ModelSpec(model_id="gpt-5.4", capabilities=["text"])
            )
            store.upsert_ability(
                RouteAbility(account_id="openai-main", model_id="gpt-5.4")
            )
            store.set_health(
                ProviderHealth(
                    account_id="openai-main",
                    model_id="gpt-5.4",
                    status=HealthStatus.DOWN,
                    last_error="timeout",
                )
            )

            decision = store.route(RouteRequest(capabilities=["text"]))

            self.assertIsNone(decision.selected)
            self.assertEqual(decision.rejected[0]["reason"], "health is down")

    def test_project_route_override_changes_priority_only_for_project(self) -> None:
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
                )
            )
            store.upsert_project_override(
                ProjectRouteOverride(
                    project_id="writing",
                    account_id="anthropic-main",
                    model_id="claude-opus",
                    priority=50,
                    weight=2,
                )
            )

            default_decision = store.route(RouteRequest(capabilities=["text"]))
            project_decision = store.route(
                RouteRequest(project_id="writing", capabilities=["text"])
            )

            self.assertEqual(
                default_decision.selected.account.account_id,
                "openai-main",
            )
            self.assertEqual(
                project_decision.selected.account.account_id,
                "anthropic-main",
            )
            self.assertIn(
                "project_override=writing",
                project_decision.selected.reasons,
            )

    def test_project_profile_applies_defaults_and_budget_cap(self) -> None:
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
            store.upsert_model(
                ModelSpec(
                    model_id="gpt-5.4",
                    capabilities=["text", "tools"],
                    input_usd_per_million=2.0,
                    output_usd_per_million=10.0,
                )
            )
            store.upsert_ability(
                RouteAbility(account_id="openai-main", model_id="gpt-5.4")
            )
            store.upsert_project_profile(
                ProjectRouteProfile(
                    project_id="agent-dev",
                    default_capabilities=["tools"],
                    max_cost_usd=0.001,
                    preferred_providers=["openai"],
                )
            )

            decision = store.route(
                RouteRequest(
                    project_id="agent-dev",
                    capabilities=["text"],
                    input_tokens=1000,
                    output_tokens=500,
                )
            )

            self.assertIsNone(decision.selected)
            self.assertEqual(decision.request.capabilities, ["text", "tools"])
            self.assertEqual(decision.request.max_cost_usd, 0.001)
            self.assertTrue(
                any(
                    "exceeds max_cost_usd" in item["reason"]
                    for item in decision.rejected
                )
            )

    def test_project_model_orders_store_model_names_by_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ProviderRouterStore(tmpdir)

            order = store.upsert_project_model_order(
                ProjectModelOrder(
                    project_id="omni-hub",
                    slot="reasoning",
                    model_ids=["deepseek-chat", "gpt-5.5", "deepseek-chat"],
                )
            )

            self.assertEqual(order.model_ids, ["deepseek-chat", "gpt-5.5"])
            listed = store.list_project_model_orders(project_id="omni-hub")
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].slot, "reasoning")
            self.assertEqual(listed[0].model_ids, ["deepseek-chat", "gpt-5.5"])
            self.assertEqual(store.stats()["project_model_orders"], 1)

            store.delete_project_model_order("omni-hub", "reasoning")
            self.assertEqual(store.list_project_model_orders(project_id="omni-hub"), [])

    def test_operations_register_and_simulate_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = OperationRunner(
                build_default_registry(tmpdir),
                audit=AuditLogger(Path(tmpdir) / "audit.jsonl"),
            )
            for spec in [
                OperationSpec(
                    name="add_provider_account",
                    action="register_provider",
                    payload={
                        "account_id": "openai-main",
                        "provider": "openai",
                        "name": "OpenAI Main",
                        "base_url": "https://api.openai.com/v1",
                        "secret_ref": "env:OPENAI_API_KEY",
                    },
                    risk_level=RiskLevel.LOCAL_WRITE,
                ),
                OperationSpec(
                    name="add_model",
                    action="register_model",
                    payload={
                        "model_id": "gpt-5.4",
                        "capabilities": ["text", "tools"],
                        "input_usd_per_million": 2.0,
                        "output_usd_per_million": 10.0,
                    },
                    risk_level=RiskLevel.LOCAL_WRITE,
                ),
                OperationSpec(
                    name="set_route_ability",
                    action="set_route_ability",
                    payload={
                        "account_id": "openai-main",
                        "model_id": "gpt-5.4",
                        "priority": 10,
                    },
                    risk_level=RiskLevel.LOCAL_WRITE,
                ),
            ]:
                result = runner.run(spec)
                self.assertEqual(result.status, OperationStatus.SUCCEEDED)

            route_result = runner.run(
                OperationSpec(
                    name="route_simulate",
                    action="route_simulate",
                    payload={"capabilities": ["tools"], "limit": 3},
                    risk_level=RiskLevel.READ_ONLY,
                )
            )

            self.assertEqual(route_result.status, OperationStatus.SUCCEEDED)
            self.assertEqual(
                route_result.output["selected"]["account"]["account_id"],
                "openai-main",
            )


if __name__ == "__main__":
    unittest.main()
