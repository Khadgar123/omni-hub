"""v0.38 P1 tests — Foundation / Functional / Domain skill taxonomy."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.skill_stubs import (
    FOUNDATION_SKILLS,
    FUNCTIONAL_SKILLS,
    regenerate_foundation,
    regenerate_functional,
    render_foundation_stub,
    render_functional_stub,
)
from omni_hub.skill_sync import sync_skills
from omni_hub.skills import SkillKind, SkillRegistry


class FoundationCatalogTests(unittest.TestCase):
    """The 2026-05-28 SOTA brief says 15-25 foundation skills is the sweet
    spot.  v0.38 ships 16; verify the catalog is non-empty and well-formed."""

    def test_foundation_catalog_count(self) -> None:
        self.assertGreaterEqual(len(FOUNDATION_SKILLS), 15)
        self.assertLessEqual(len(FOUNDATION_SKILLS), 25)

    def test_foundation_skills_have_unique_ids(self) -> None:
        ids = [s.skill_id for s in FOUNDATION_SKILLS]
        self.assertEqual(len(ids), len(set(ids)), "duplicate foundation skill_id")

    def test_foundation_buckets_are_balanced(self) -> None:
        buckets: dict[str, int] = {}
        for s in FOUNDATION_SKILLS:
            buckets[s.bucket] = buckets.get(s.bucket, 0) + 1
        # No single bucket dominates ( > 50% of total ).
        max_share = max(buckets.values()) / len(FOUNDATION_SKILLS)
        self.assertLess(max_share, 0.55, f"bucket imbalance: {buckets}")
        # Five SOTA-required buckets are present.
        for required in ("knowledge_access", "knowledge_update", "eval", "workflow"):
            self.assertIn(required, buckets, f"missing bucket: {required}")

    def test_render_foundation_stub_contains_layer_field(self) -> None:
        skill = FOUNDATION_SKILLS[0]
        body = render_foundation_stub(skill)
        self.assertIn("layer: foundation", body)
        self.assertIn(f"bucket: {skill.bucket}", body)
        self.assertIn(f"entrypoint: \"{skill.entrypoint}\"", body)


class FunctionalCatalogTests(unittest.TestCase):
    def test_functional_catalog_count(self) -> None:
        # SOTA brief: 8-15 functional orchestrators is the sweet spot.
        self.assertGreaterEqual(len(FUNCTIONAL_SKILLS), 8)
        self.assertLessEqual(len(FUNCTIONAL_SKILLS), 15)

    def test_functional_skills_have_unique_ids(self) -> None:
        ids = [s.skill_id for s in FUNCTIONAL_SKILLS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_render_functional_stub_lists_composed_foundations(self) -> None:
        # Pick a functional skill that composes at least one foundation.
        composing = next(s for s in FUNCTIONAL_SKILLS if s.composes)
        body = render_functional_stub(composing)
        self.assertIn("layer: functional", body)
        for fid in composing.composes:
            self.assertIn(fid, body)


class ThreeLayerGenerateAndSyncTests(unittest.TestCase):
    """End-to-end: generate all 3 layers, sync into registry/skills.json,
    verify SkillRegistry sees them with the right kind."""

    def test_full_three_layer_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # 1) materialise foundation + functional + domain stubs
            f = regenerate_foundation(workspace=tmp)
            self.assertGreaterEqual(len(f), len(FOUNDATION_SKILLS))
            for action in f:
                self.assertEqual(action.action, "written")
            fn = regenerate_functional(workspace=tmp)
            self.assertGreaterEqual(len(fn), len(FUNCTIONAL_SKILLS))
            for action in fn:
                self.assertEqual(action.action, "written")
            # 2) sync into registry
            report = sync_skills(tmp, apply=True)
            self.assertTrue(report["applied"])
            # 3) SkillRegistry sees them all
            registry = SkillRegistry(tmp)
            ids = {s.skill_id for s in registry.list()}
            for fs in FOUNDATION_SKILLS:
                self.assertIn(fs.skill_id, ids,
                              f"foundation skill {fs.skill_id} missing from registry")
            for fn_s in FUNCTIONAL_SKILLS:
                self.assertIn(fn_s.skill_id, ids,
                              f"functional skill {fn_s.skill_id} missing from registry")


class FunctionalBuiltinsTests(unittest.TestCase):
    """v0.38-B added 8 new builtins.  Verify each is registered + invocable
    with a stub payload."""

    def setUp(self) -> None:
        from omni_hub.builtins import build_default_registry
        from omni_hub.runner import OperationRunner

        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.runner = OperationRunner(build_default_registry(self.workspace))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, name: str, payload: dict, *, risk: str = "L0", approved: bool = False) -> dict:
        from omni_hub.models import OperationSpec, RiskLevel
        result = self.runner.run(
            OperationSpec(name=name, action="run", payload=payload,
                          risk_level=RiskLevel.parse(risk)),
            approved=approved,
        )
        return result.to_dict()

    def test_inbox_classify_registered(self) -> None:
        out = self._run("inbox_classify", {"body": "check https://arxiv.org/abs/x", "sender": "me"})
        self.assertEqual(out["status"], "succeeded")
        self.assertEqual(out["output"]["category"], "url")

    def test_project_plan_registered(self) -> None:
        out = self._run("project_plan",
                        {"user_id": "u_x", "title": "ship v0.40"},
                        risk="L1", approved=True)
        self.assertEqual(out["status"], "succeeded")
        self.assertEqual(out["output"]["title"], "ship v0.40")

    def test_pptx_build_returns_skipped_without_broker(self) -> None:
        # In test env, pptx-omni binary is not on PATH → returns skipped.
        out = self._run("pptx_build",
                        {"outline": {"title": "Demo", "slides": []}},
                        risk="L1", approved=True)
        self.assertEqual(out["status"], "succeeded")
        self.assertTrue(out["output"].get("skipped"))

    def test_calendar_add_registered(self) -> None:
        out = self._run("calendar_add", {
            "user_id": "u_x", "summary": "standup",
            "start": "2026-06-01T10:00:00+00:00",
            "end": "2026-06-01T11:00:00+00:00",
        }, risk="L1", approved=True)
        self.assertEqual(out["status"], "succeeded")
        self.assertEqual(out["output"]["summary"], "standup")

    def test_task_add_registered(self) -> None:
        out = self._run("task_add",
                        {"user_id": "u_x", "title": "watch ACE paper",
                         "category": "research"},
                        risk="L1", approved=True)
        self.assertEqual(out["status"], "succeeded")
        self.assertEqual(out["output"]["title"], "watch ACE paper")

    def test_schedule_plan_registered(self) -> None:
        # Empty workspace → schedule plan returns 0 tasks / 0 events.
        out = self._run("schedule_plan", {"user_id": "u_x", "days_ahead": 7})
        self.assertEqual(out["status"], "succeeded")
        self.assertEqual(out["output"]["task_count"], 0)

    def test_finance_screen_registered(self) -> None:
        # v0.43.4: finance_screen now actually hits EDGAR cascade,
        # so count >= 0 (network-dependent; test only that operation
        # registered and returned succeeded).
        out = self._run("finance_screen", {"tickers": ["NVDA"]})
        self.assertEqual(out["status"], "succeeded")
        self.assertIn("count", out["output"])
        self.assertGreaterEqual(out["output"]["count"], 0)

    def test_finance_screen_grounds_in_domain_context(self) -> None:
        # R3 knowledge->productivity edge: composes:[retrieve, context-pack]
        # now EXECUTES — the screen returns a domain knowledge pack, not just
        # signals.  build_context_pack reads local vault/wiki+claims only, so
        # this is deterministic and network-free.
        out = self._run("finance_screen", {"tickers": ["NVDA"], "domain": "finance"})
        self.assertEqual(out["status"], "succeeded")
        pack = out["output"].get("context_pack")
        self.assertIsInstance(pack, dict)
        self.assertIn("grounded", pack)

    def test_pptx_build_grounds_in_domain_context(self) -> None:
        # R3: composes:[context-pack] now EXECUTES even on the broker-skipped
        # path — the deck is grounded in domain knowledge before render.
        out = self._run("pptx_build",
                        {"outline": {"title": "ACE context engineering", "slides": []},
                         "domain": "research"},
                        risk="L1", approved=True)
        self.assertEqual(out["status"], "succeeded")
        pack = out["output"].get("context_pack")
        self.assertIsInstance(pack, dict)
        self.assertIn("grounded", pack)

    def test_app_route_task_grounds_knowledge_query(self) -> None:
        # R3: chat-route composes:[retrieve, context-pack] — a knowledge query
        # (recommended op = context_pack_build) now returns an EXECUTED
        # context_pack, not just a recommendation string.
        out = self._run("app_route_task", {"query": "diffusion models overview"})
        self.assertEqual(out["status"], "succeeded")
        self.assertEqual(
            out["output"]["decision"]["recommended_operation"],
            "context_pack_build",
        )
        pack = out["output"].get("context_pack")
        self.assertIsInstance(pack, dict)
        self.assertIn("grounded", pack)

    def test_app_route_task_ground_opt_out(self) -> None:
        # Opt-out: {"ground": False} returns routing only, no context_pack.
        out = self._run("app_route_task",
                        {"query": "diffusion models overview", "ground": False})
        self.assertEqual(out["status"], "succeeded")
        self.assertNotIn("context_pack", out["output"])

    def test_order_propose_lands_proposal(self) -> None:
        out = self._run("order_propose", {
            "user_id": "u_x", "instrument": "NVDA",
            "side": "buy", "qty": 10, "order_type": "limit", "limit_price": 195.0,
            "portfolio_value_usd": 50_000.0,
        }, risk="L1", approved=True)
        self.assertEqual(out["status"], "succeeded")
        self.assertTrue(out["output"]["proposal_id"])
        self.assertTrue(out["output"]["risk_passes"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
