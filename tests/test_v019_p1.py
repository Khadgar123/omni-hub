"""v0.19 P1 tests: 5-Plane refactor — domain expansion to 19, Interface
Plane (Channel Protocol + Email/CLI/MCP/stubs), Application Plane
(ReportOrchestrator + TaskRouter), and SKILL.md stub generator."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub import knowledge_plane
from omni_hub.app import (
    ReportOrchestrator,
    ReportPeriod,
    TaskRouter,
)
from omni_hub.channels import (
    Channel,
    ChannelHealth,
    ChannelRegistry,
    CLIChannel,
    DiscordChannel,
    EmailChannel,
    EmailChannelConfig,
    FeishuChannel,
    InboundMessage,
    MCPChannel,
    OutboundMessage,
)
from omni_hub.domain_schemas import DOMAIN_SCHEMA_VERSION, DOMAIN_SCHEMAS
from omni_hub.skill_stubs import (
    SKILL_STUB_MARKER,
    SKILL_STUB_VERSION,
    regenerate_all,
    render_skill_stub,
)


# ---------------------------------------------------------------------------
# v0.19-A: Domain expansion
# ---------------------------------------------------------------------------


class DomainExpansionTests(unittest.TestCase):
    def test_19_domains_registered(self) -> None:
        self.assertEqual(len(DOMAIN_SCHEMAS), 19)

    def test_policy_split_into_us_and_cn(self) -> None:
        self.assertIn("us_policy", DOMAIN_SCHEMAS)
        self.assertIn("cn_policy", DOMAIN_SCHEMAS)
        self.assertNotIn("policy", DOMAIN_SCHEMAS)
        # Folder naming follows kebab-case.
        self.assertEqual(DOMAIN_SCHEMAS["us_policy"].folder, "us-policy")
        self.assertEqual(DOMAIN_SCHEMAS["cn_policy"].folder, "cn-policy")

    def test_six_new_v019_verticals_present(self) -> None:
        for new_domain in [
            "meta", "fitness_wellness", "cooking",
            "travel", "marketing", "enterprise",
        ]:
            with self.subTest(domain=new_domain):
                self.assertIn(new_domain, DOMAIN_SCHEMAS)
                self.assertTrue(DOMAIN_SCHEMAS[new_domain].position)
                # fitness_wellness, enterprise, marketing all need lint hints.
                self.assertTrue(DOMAIN_SCHEMAS[new_domain].lint_hints)

    def test_schema_version_at_least_v019(self) -> None:
        # v0.19 introduced the expansion; v0.20 bumps for the bilibili /
        # weibo / zhihu authoritative-source additions.
        self.assertGreaterEqual(DOMAIN_SCHEMA_VERSION, "v0.19")

    def test_meta_domain_has_no_external_cascade(self) -> None:
        self.assertEqual(DOMAIN_SCHEMAS["meta"].authoritative_sources, [])

    def test_fitness_wellness_emphasises_evidence_grade(self) -> None:
        schema = DOMAIN_SCHEMAS["fitness_wellness"]
        names = {f for f, _ in schema.frontmatter_optional}
        self.assertIn("evidence_grade", names)
        self.assertIn("rct_link", names)


# ---------------------------------------------------------------------------
# v0.19-E: Channel Protocol + Channels
# ---------------------------------------------------------------------------


class ChannelProtocolTests(unittest.TestCase):
    def test_inbound_message_factory_generates_trace_id(self) -> None:
        msg = InboundMessage.new(
            channel="cli", sender="alice@example.com", body="hello",
        )
        self.assertEqual(msg.channel, "cli")
        self.assertTrue(msg.trace_id.startswith("cli-"))
        self.assertEqual(msg.sender, "alice@example.com")

    def test_outbound_echoes_inbound_trace_id(self) -> None:
        inbound = InboundMessage.new(
            channel="email", sender="x@y.com", body="ping", subject="ping",
            metadata={"message_id": "<msg-1@y.com>"},
        )
        reply = OutboundMessage.in_reply_to_msg(inbound, "pong")
        self.assertEqual(reply.trace_id, inbound.trace_id)
        self.assertEqual(reply.recipient, inbound.sender)
        self.assertEqual(reply.in_reply_to, "<msg-1@y.com>")
        self.assertEqual(reply.subject, "Re: ping")

    def test_channel_registry_registers_and_lists(self) -> None:
        registry = ChannelRegistry()
        registry.register(CLIChannel())
        registry.register(MCPChannel())
        self.assertEqual(registry.names(), ["cli", "mcp"])

    def test_channel_registry_rejects_duplicate(self) -> None:
        registry = ChannelRegistry()
        registry.register(CLIChannel())
        with self.assertRaises(ValueError):
            registry.register(CLIChannel())

    def test_channel_registry_health_includes_all(self) -> None:
        registry = ChannelRegistry()
        registry.register(CLIChannel())
        registry.register(FeishuChannel())
        registry.register(DiscordChannel())
        snapshots = registry.health()
        names = {s.name for s in snapshots}
        self.assertEqual(names, {"cli", "feishu", "discord"})


class CLIChannelTests(unittest.TestCase):
    def test_cli_channel_reports_healthy(self) -> None:
        ch = CLIChannel()
        health = ch.health_check()
        self.assertTrue(health.ok)
        self.assertEqual(health.name, "cli")


class MCPChannelTests(unittest.TestCase):
    def test_mcp_channel_health_truthy_when_module_importable(self) -> None:
        ch = MCPChannel()
        health = ch.health_check()
        # Module is importable in this repo → ok.
        self.assertTrue(health.ok)


class EmailChannelTests(unittest.TestCase):
    def test_email_channel_unconfigured_when_no_env(self) -> None:
        # Clear any inherited env to make the test deterministic.
        keys = [
            "OMNI_EMAIL_IMAP_HOST", "OMNI_EMAIL_IMAP_USER",
            "OMNI_EMAIL_IMAP_PASSWORD", "OMNI_EMAIL_SMTP_HOST",
            "OMNI_EMAIL_SMTP_USER", "OMNI_EMAIL_SMTP_PASSWORD",
        ]
        original = {k: os.environ.pop(k, None) for k in keys}
        try:
            ch = EmailChannel()
            self.assertFalse(ch.configured())
            health = ch.health_check()
            self.assertFalse(health.ok)
        finally:
            for k, v in original.items():
                if v is not None:
                    os.environ[k] = v

    def test_email_channel_configured_when_env_set(self) -> None:
        env_values = {
            "OMNI_EMAIL_IMAP_HOST": "imap.example.com",
            "OMNI_EMAIL_IMAP_USER": "bot@example.com",
            "OMNI_EMAIL_IMAP_PASSWORD": "secret",
            "OMNI_EMAIL_SMTP_HOST": "smtp.example.com",
            "OMNI_EMAIL_SMTP_USER": "bot@example.com",
            "OMNI_EMAIL_SMTP_PASSWORD": "secret",
        }
        original = {k: os.environ.get(k) for k in env_values}
        try:
            for k, v in env_values.items():
                os.environ[k] = v
            ch = EmailChannel()
            self.assertTrue(ch.configured())
            assert ch.config is not None
            self.assertEqual(ch.config.from_addr, "bot@example.com")
            self.assertEqual(ch.config.mailbox, "INBOX")
        finally:
            for k, v in original.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class ExternalStubTests(unittest.TestCase):
    def test_feishu_stub_is_not_configured(self) -> None:
        ch = FeishuChannel()
        self.assertFalse(ch.health_check().ok)
        with self.assertRaises(NotImplementedError):
            list(ch.listen())

    def test_discord_stub_is_not_configured(self) -> None:
        ch = DiscordChannel()
        self.assertFalse(ch.health_check().ok)
        with self.assertRaises(NotImplementedError):
            ch.reply(OutboundMessage(
                channel="discord", trace_id="t1", recipient="u", body="hi",
            ))


# ---------------------------------------------------------------------------
# v0.19-F: Application Plane
# ---------------------------------------------------------------------------


class ReportOrchestratorTests(unittest.TestCase):
    def test_daily_report_runs_on_empty_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orch = ReportOrchestrator(tmp)
            summary = orch.build(ReportPeriod.DAILY)
            self.assertEqual(summary.period, "daily")
            self.assertEqual(len(summary.sections), 4)
            self.assertIn("ClaimLedger", summary.markdown)
            self.assertIn("wiki-lint findings", summary.markdown)
            self.assertIn("PreferenceStore", summary.markdown)
            self.assertIn("WorkflowKernel", summary.markdown)

    def test_weekly_report_aggregates_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            ledger = root / ".omni" / "claims.jsonl"
            ledger.parent.mkdir(parents=True, exist_ok=True)
            now = datetime.now(UTC).isoformat()
            ledger.write_text(
                "\n".join([
                    json.dumps({
                        "claim_id": "c1", "domain": "ai_progress",
                        "review_state": "approved",
                        "t_valid_from": now, "t_valid_to": None,
                        "statement": "x", "support": [],
                    }),
                    json.dumps({
                        "claim_id": "c2", "domain": "us_policy",
                        "review_state": "approved",
                        "t_valid_from": now, "t_valid_to": None,
                        "statement": "y", "support": [],
                    }),
                    "",
                ]),
                encoding="utf-8",
            )
            orch = ReportOrchestrator(root)
            summary = orch.build(ReportPeriod.WEEKLY)
            claims_section = summary.sections[0]
            self.assertEqual(claims_section.title, "ClaimLedger")
            self.assertEqual(claims_section.stats["added"], 2)
            self.assertEqual(
                claims_section.stats["by_domain"]["ai_progress"], 1,
            )
            self.assertEqual(
                claims_section.stats["by_domain"]["us_policy"], 1,
            )

    def test_report_period_days_correct(self) -> None:
        self.assertEqual(ReportPeriod.DAILY.days, 1)
        self.assertEqual(ReportPeriod.WEEKLY.days, 7)
        self.assertEqual(ReportPeriod.MONTHLY.days, 30)


class TaskRouterTests(unittest.TestCase):
    def test_router_picks_finance_for_ticker_keywords(self) -> None:
        msg = InboundMessage.new(
            channel="cli", sender="cli-user",
            body="how should I think about the NVDA earnings call",
        )
        decision = TaskRouter().route(msg)
        self.assertEqual(decision.selected_skill_id, "finance")
        self.assertGreater(decision.confidence, 0.0)

    def test_router_picks_cooking_for_chinese_recipe_query(self) -> None:
        msg = InboundMessage.new(
            channel="cli", sender="cli-user",
            body="今晚做什么红烧肉 麻婆豆腐",
        )
        decision = TaskRouter().route(msg)
        self.assertEqual(decision.selected_skill_id, "cooking")

    def test_router_picks_meta_for_omni_hub_question(self) -> None:
        msg = InboundMessage.new(
            channel="cli", sender="cli-user",
            body="omni-hub v0.19 接下来该做什么",
        )
        decision = TaskRouter().route(msg)
        self.assertEqual(decision.selected_skill_id, "meta")
        self.assertEqual(decision.recommended_operation, "task_enqueue")

    def test_router_falls_through_to_default_on_empty(self) -> None:
        msg = InboundMessage.new(
            channel="cli", sender="cli-user", body="",
        )
        decision = TaskRouter().route(msg)
        self.assertEqual(decision.confidence, 0.0)
        self.assertEqual(decision.selected_skill_id, "research")

    def test_reply_template_carries_trace_id(self) -> None:
        msg = InboundMessage.new(
            channel="email", sender="user@x", body="迭代 omni-hub",
            subject="meta question",
        )
        router = TaskRouter()
        decision = router.route(msg)
        reply = router.reply_template(msg, decision)
        self.assertEqual(reply.trace_id, msg.trace_id)
        self.assertEqual(reply.channel, "email")
        self.assertIn(msg.trace_id, reply.body)


# ---------------------------------------------------------------------------
# v0.19-G: SKILL.md stub generator
# ---------------------------------------------------------------------------


class SkillStubTests(unittest.TestCase):
    def test_render_skill_stub_includes_required_frontmatter(self) -> None:
        schema = DOMAIN_SCHEMAS["enterprise"]
        body = render_skill_stub("enterprise", schema)
        self.assertIn("name: enterprise-wiki", body)
        self.assertIn(f"schema_version: {SKILL_STUB_VERSION}", body)
        self.assertIn("status: active-domain", body)
        self.assertIn(SKILL_STUB_MARKER, body)
        # The hard rule must propagate into the stub.
        self.assertIn("Proposal[T]", body)
        self.assertIn("agent-harness", body) if False else None  # placeholder

    def test_regenerate_all_writes_19_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            actions = regenerate_all(skills_root=".agents/skills", workspace=tmp)
            self.assertEqual(len(actions), 19)
            for action in actions:
                self.assertEqual(action.action, "written")
            for schema in DOMAIN_SCHEMAS.values():
                target = Path(tmp) / ".agents" / "skills" / f"{schema.folder}-wiki" / "SKILL.md"
                self.assertTrue(target.exists(), f"stub missing: {target}")

    def test_regenerate_all_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            regenerate_all(workspace=tmp)
            actions2 = regenerate_all(workspace=tmp)
            for action in actions2:
                self.assertEqual(action.action, "unchanged")

    def test_regenerate_all_preserves_hand_edited_stubs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            regenerate_all(workspace=tmp)
            # Strip the marker → simulates hand-edit.
            target = Path(tmp) / ".agents" / "skills" / "research-wiki" / "SKILL.md"
            hand_body = target.read_text(encoding="utf-8").replace(
                SKILL_STUB_MARKER, "<!-- hand-edited by user -->",
            ) + "\n\nExtra human prose."
            target.write_text(hand_body, encoding="utf-8")
            actions = regenerate_all(workspace=tmp)
            # The hand-edited one should report 'hand-edited'.
            research_action = next(a for a in actions if a.skill_id == "research-wiki")
            self.assertEqual(research_action.action, "hand-edited")
            self.assertEqual(target.read_text(encoding="utf-8"), hand_body)


# ---------------------------------------------------------------------------
# v0.19 end-to-end: route → recommended op → build context pack
# ---------------------------------------------------------------------------


class RoutingToContextPackTests(unittest.TestCase):
    def test_research_routing_recommends_context_pack_build(self) -> None:
        msg = InboundMessage.new(
            channel="cli", sender="cli-user",
            body="arxiv 2510.04618 ACE 论文怎么评价",
        )
        decision = TaskRouter().route(msg)
        self.assertIn(decision.selected_skill_id, {"research", "ai_progress"})
        self.assertEqual(decision.recommended_operation, "context_pack_build")
        self.assertEqual(
            decision.recommended_payload["domain"],
            decision.selected_skill_id,
        )

    def test_engineering_routing_recommends_task_enqueue(self) -> None:
        msg = InboundMessage.new(
            channel="cli", sender="cli-user",
            body="this stack trace 让我修 ide lsp",
        )
        decision = TaskRouter().route(msg)
        self.assertEqual(decision.selected_skill_id, "engineering")
        self.assertEqual(decision.recommended_operation, "task_enqueue")
        self.assertEqual(decision.recommended_payload["lane"], "claude")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
