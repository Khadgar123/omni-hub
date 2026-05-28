"""v0.37 P1 tests — fixes from the 2026-05-28 review."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.app import TaskRouter
from omni_hub.app.task_router import INTENT_WEIGHT, KEYWORD_WEIGHT
from omni_hub.channels.base import InboundMessage
from omni_hub.skill_stubs import regenerate_all, SKILL_STUB_MARKER, SKILL_STUB_VERSION
from omni_hub.skill_sync import sync_skills
from omni_hub.skills import SkillKind, SkillRegistry, SkillSpec


# ---------------------------------------------------------------------------
# P0 — skill three-truth-source merge
# ---------------------------------------------------------------------------


class SkillThreeTruthSourceMergeTests(unittest.TestCase):
    """The 2026-05-28 review's headline P0: 25 SKILL.md vs 5 in skill-list.

    After v0.37:
        * SkillKind.DOMAIN_WIKI exists (no entrypoint required)
        * Auto-generated stubs embed an ``omni_hub:`` metadata block
        * ``skill-sync --apply`` merges md → registry/skills.json
        * SkillRegistry.list() returns the 19 wiki skills + any existing
          registry rows
    """

    def test_domain_wiki_kind_exists(self) -> None:
        self.assertEqual(SkillKind.DOMAIN_WIKI.value, "domain_wiki")

    def test_stub_marker_bumped_to_v037(self) -> None:
        self.assertEqual(SKILL_STUB_VERSION, "v0.37")
        self.assertIn("v0.37", SKILL_STUB_MARKER)

    def test_regenerated_stubs_have_omni_hub_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            regenerate_all(workspace=tmp)
            sample = Path(tmp) / ".agents" / "skills" / "enterprise-wiki" / "SKILL.md"
            self.assertTrue(sample.exists())
            body = sample.read_text(encoding="utf-8")
            # The omni_hub: block must include the domain_wiki kind + an
            # entrypoint defaulting to context_pack_build.
            self.assertIn("omni_hub:", body)
            self.assertIn("kind: domain_wiki", body)
            self.assertIn("entrypoint: \"operation:context_pack_build\"", body)
            self.assertIn("domain: \"enterprise\"", body)

    def test_skill_sync_apply_promotes_md_to_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # 1) generate stubs
            regenerate_all(workspace=tmp)
            # 2) apply
            report = sync_skills(tmp, apply=True)
            self.assertTrue(report["applied"])
            # 3) registry/skills.json now contains the 19 wiki domain skills
            registry = SkillRegistry(tmp)
            skills = registry.list()
            domain_skills = [s for s in skills if s.kind is SkillKind.DOMAIN_WIKI]
            self.assertGreaterEqual(len(domain_skills), 19)
            # Each one must satisfy the new contract.
            for skill in domain_skills:
                with self.subTest(skill_id=skill.skill_id):
                    self.assertTrue(skill.skill_id.endswith("-wiki"))
                    self.assertEqual(skill.entrypoint, "operation:context_pack_build")
                    self.assertIn("wiki", skill.tags)
                    self.assertIn("domain", skill.tags)


# ---------------------------------------------------------------------------
# P1 — TaskRouter intent classification
# ---------------------------------------------------------------------------


class TaskRouterIntentTests(unittest.TestCase):
    """The 2026-05-28 review's headline P1: 'OpenAI 最新组织架构 值得加入'
    was routed to ``ai_progress`` instead of ``enterprise``.

    After v0.37: intent phrases get 3× weight; ``组织架构 / 值得加入`` win
    over the ai_progress entity keyword ``OpenAI``.
    """

    def test_intent_weight_higher_than_keyword(self) -> None:
        self.assertGreater(INTENT_WEIGHT, KEYWORD_WEIGHT)

    def test_openai_org_query_routes_to_enterprise(self) -> None:
        msg = InboundMessage.new(
            channel="cli", sender="me",
            body="分析 OpenAI 最新组织架构和产品变化,判断是否值得加入",
        )
        decision = TaskRouter().route(msg)
        self.assertEqual(decision.selected_skill_id, "enterprise")
        # Recommended op for engineering / meta / enterprise is task_enqueue
        # (see TaskRouter._recommend).
        self.assertEqual(decision.recommended_operation, "task_enqueue")

    def test_pure_ai_progress_query_still_routes_correctly(self) -> None:
        msg = InboundMessage.new(
            channel="cli", sender="me",
            body="Claude 4.7 有什么新特性 DSPy 3 怎么用",
        )
        decision = TaskRouter().route(msg)
        self.assertEqual(decision.selected_skill_id, "ai_progress")

    def test_due_diligence_intent_routes_to_enterprise(self) -> None:
        msg = InboundMessage.new(
            channel="cli", sender="me",
            body="due diligence on Anthropic — funding round + headcount",
        )
        decision = TaskRouter().route(msg)
        self.assertEqual(decision.selected_skill_id, "enterprise")


# ---------------------------------------------------------------------------
# P2 — SQLite connection lifecycle (smoke probes)
# ---------------------------------------------------------------------------


class ManagedConnectionTests(unittest.TestCase):
    """Verify the v0.37 ``_ManagedConnection`` actually closes on exit.

    Catches regressions where a future refactor drops the factory= argument.
    """

    def test_connect_sqlite_store_returns_managed_connection(self) -> None:
        from omni_hub._storage import _ManagedConnection, connect_sqlite_store
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "probe.sqlite3"
            with connect_sqlite_store(db) as conn:
                self.assertIsInstance(conn, _ManagedConnection)
                conn.execute("CREATE TABLE t (k TEXT)")
                conn.execute("INSERT INTO t VALUES ('x')")
            # After exit, conn should be closed: attempting another execute
            # raises ProgrammingError.
            import sqlite3
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
