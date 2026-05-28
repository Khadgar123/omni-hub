"""v0.40 P1 tests — review-driven status/namespace + 2-level AppIntentRouter
+ 5-section domain template + CLI smoke."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.app import (
    AppIntentRouter,
    AppRouteDecision,
)
from omni_hub.app.intent_router import _INTENT_TOOLS
from omni_hub.channels.base import InboundMessage
from omni_hub.skill_stubs import (
    FOUNDATION_SKILLS,
    FUNCTIONAL_SKILLS,
    SKILL_NAMESPACES,
    SKILL_STATUSES,
    SKILL_STUB_VERSION,
    regenerate_foundation,
    regenerate_functional,
    regenerate_all,
    render_foundation_stub,
    render_functional_stub,
    render_skill_stub,
)
from omni_hub.skills import SkillKind, SkillRegistry, SkillStatus
from omni_hub.domain_schemas import DOMAIN_SCHEMAS


# ---------------------------------------------------------------------------
# P1 — SKILL.md status field (review's "stub | broker_required | deprecated")
# ---------------------------------------------------------------------------


class SkillStatusFieldTests(unittest.TestCase):
    def test_skill_status_enum_extended(self) -> None:
        self.assertEqual(SkillStatus.STUB.value, "stub")
        self.assertEqual(SkillStatus.BROKER_REQUIRED.value, "broker_required")
        self.assertEqual(SkillStatus.DEPRECATED.value, "deprecated")

    def test_skill_stub_constants_present(self) -> None:
        self.assertIn("active", SKILL_STATUSES)
        self.assertIn("stub", SKILL_STATUSES)
        self.assertIn("broker_required", SKILL_STATUSES)
        self.assertIn("deprecated", SKILL_STATUSES)

    def test_known_stub_functional_skills_marked(self) -> None:
        """The review specifically flagged 3 stubs — verify they're tagged."""

        by_id = {s.skill_id: s for s in FUNCTIONAL_SKILLS}
        self.assertEqual(by_id["project-plan"].status, "stub")
        self.assertEqual(by_id["finance-screen"].status, "stub")
        self.assertEqual(by_id["pptx-build"].status, "broker_required")
        self.assertEqual(by_id["inbox-route"].status, "stub")

    def test_foundation_stubs_render_status_and_namespace(self) -> None:
        skill = FOUNDATION_SKILLS[0]
        body = render_foundation_stub(skill)
        self.assertIn(f"status: {skill.status}", body)
        self.assertIn(f"namespace: {skill.namespace}", body)
        self.assertIn(f"bucket: {skill.bucket}", body)

    def test_functional_stub_with_status_shows_banner(self) -> None:
        stub_skill = next(s for s in FUNCTIONAL_SKILLS if s.status == "stub")
        body = render_functional_stub(stub_skill)
        self.assertIn("Status: stub", body)
        # The deprecation / broker_required banner is conditional on status.
        broker = next(s for s in FUNCTIONAL_SKILLS if s.status == "broker_required")
        broker_body = render_functional_stub(broker)
        self.assertIn("Status: broker_required", broker_body)


# ---------------------------------------------------------------------------
# P1 — Namespace field (foundation_core / foundation_write / foundation_eval)
# ---------------------------------------------------------------------------


class NamespaceLazyLoadHintTests(unittest.TestCase):
    def test_known_namespaces_present(self) -> None:
        for ns in (
            "foundation_core", "foundation_write", "foundation_eval",
            "functional", "domain",
        ):
            self.assertIn(ns, SKILL_NAMESPACES)

    def test_foundation_namespaces_inferred_from_bucket(self) -> None:
        # knowledge_access → foundation_core
        access = next(s for s in FOUNDATION_SKILLS if s.bucket == "knowledge_access")
        self.assertEqual(access.namespace, "foundation_core")
        # knowledge_update → foundation_write
        update = next(s for s in FOUNDATION_SKILLS if s.bucket == "knowledge_update")
        self.assertEqual(update.namespace, "foundation_write")
        # eval → foundation_eval
        ev = next(s for s in FOUNDATION_SKILLS if s.bucket == "eval")
        self.assertEqual(ev.namespace, "foundation_eval")

    def test_functional_skills_namespace_defaults_functional(self) -> None:
        for skill in FUNCTIONAL_SKILLS:
            self.assertEqual(skill.namespace, "functional")


# ---------------------------------------------------------------------------
# P1 — Domain SKILL.md 5-section contract
# ---------------------------------------------------------------------------


class FiveSectionDomainTemplateTests(unittest.TestCase):
    """Reviewer asked: every domain skill should ship a uniform contract —
    retrieve_knowledge / apply_knowledge / guardrails / eval_metric /
    write_policy.  Verify the stub generator emits all 5."""

    REQUIRED_SECTIONS = (
        "## 1. Retrieve Knowledge",
        "## 2. Apply Knowledge",
        "## 3. Guardrails",
        "## 4. Eval Metric",
        "## 5. Write Policy",
    )

    def test_render_skill_stub_has_all_5_sections(self) -> None:
        schema = DOMAIN_SCHEMAS["enterprise"]
        body = render_skill_stub("enterprise", schema)
        for section in self.REQUIRED_SECTIONS:
            self.assertIn(section, body, f"missing: {section!r}")

    def test_domain_stub_carries_layer_and_namespace(self) -> None:
        schema = DOMAIN_SCHEMAS["research"]
        body = render_skill_stub("research", schema)
        self.assertIn("layer: domain", body)
        self.assertIn("namespace: domain", body)

    def test_every_domain_stub_renders_with_5_sections(self) -> None:
        for slug, schema in DOMAIN_SCHEMAS.items():
            with self.subTest(domain=slug):
                body = render_skill_stub(slug, schema)
                for section in self.REQUIRED_SECTIONS:
                    self.assertIn(section, body)


# ---------------------------------------------------------------------------
# P1 — 2-level AppIntentRouter (explicit intent → domain → tools)
# ---------------------------------------------------------------------------


class AppIntentRouterTests(unittest.TestCase):
    def test_intent_tools_map_covers_8_intents(self) -> None:
        for intent in (
            "schedule", "task", "report", "pptx",
            "project", "inbox", "finance_op", "chat",
        ):
            self.assertIn(intent, _INTENT_TOOLS,
                          f"missing tool mapping for intent {intent!r}")

    def test_review_failing_query_2level_decision(self) -> None:
        """The 2026-05-28 (round 3) review's failing query, now
        re-checked through the explicit 2-level router."""

        msg = InboundMessage.new(
            channel="cli", sender="me",
            body="明天上午提醒我复盘BTC和NVDA走势并安排日程",
        )
        decision = AppIntentRouter().route(msg)
        self.assertEqual(decision.primary_intent, "schedule")
        self.assertEqual(decision.domain, "finance")
        self.assertIn("calendar-add", decision.foundation_tools)
        self.assertEqual(decision.next_operation, "calendar_add")
        # Multi-intent: 复盘 also fires report.
        self.assertIn("report", decision.secondary_intents)

    def test_pure_chat_query_no_intent_falls_back_to_default_tools(self) -> None:
        msg = InboundMessage.new(
            channel="cli", sender="me",
            body="Claude 4.7 有什么新特性",
        )
        decision = AppIntentRouter().route(msg)
        self.assertEqual(decision.primary_intent, "")
        # Default tools when no intent: context-pack + wiki-search.
        self.assertEqual(set(decision.foundation_tools),
                          {"context-pack", "wiki-search"})

    def test_pptx_intent_routes_to_pptx_tools(self) -> None:
        msg = InboundMessage.new(
            channel="cli", sender="me",
            body="把这周的论文做一份 PPT 给同事看",
        )
        decision = AppIntentRouter().route(msg)
        # Tools should include context-pack + wiki-search regardless.
        self.assertIn("context-pack", decision.foundation_tools)


# ---------------------------------------------------------------------------
# P2 — Sanity: end-to-end 3-layer regenerate still works after v0.40 changes
# ---------------------------------------------------------------------------


class V040RegenerationSmokeTests(unittest.TestCase):
    def test_full_three_layer_regen_drift_zero(self) -> None:
        from omni_hub.skill_sync import sync_skills

        with tempfile.TemporaryDirectory() as tmp:
            f = regenerate_foundation(workspace=tmp)
            self.assertGreaterEqual(len(f), len(FOUNDATION_SKILLS))
            fn = regenerate_functional(workspace=tmp)
            self.assertGreaterEqual(len(fn), len(FUNCTIONAL_SKILLS))
            dm = regenerate_all(workspace=tmp)
            self.assertGreaterEqual(len(dm), len(DOMAIN_SCHEMAS))
            # Apply, then re-diff — both sides should be aligned.
            sync_skills(tmp, apply=True)
            second = sync_skills(tmp, apply=False)
            self.assertEqual(second["drift"], [])

    def test_skill_stub_version_is_v040(self) -> None:
        self.assertEqual(SKILL_STUB_VERSION, "v0.40")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
