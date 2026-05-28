"""v0.39 P1 tests — review-driven CLI exposure + AppIntent layer.

The 2026-05-28 (round 3) review found:
  P0 — 6 new modules (users/scheduling/inbox/projects/pptx/finance_ops) were
       NOT exposed via CLI; ``omni-hub --help`` missed every user-*, cal-*,
       personal-task-*, inbox-*, project-*, pptx-*, finance-* subcommand.
  P0 — TaskRouter routed "提醒我复盘 BTC 和 NVDA 走势并安排日程" to default
       research instead of finance + schedule intent.
  P2 — skill-sync reported 52 risk_level drift entries (MD "L0" vs reg 0).

This test file verifies all three are closed.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.app import AppIntent, TaskRouter
from omni_hub.app.task_router import (
    _APP_INTENT_PATTERNS,
    _INTENT_OPERATION,
    INTENT_WEIGHT,
    KEYWORD_WEIGHT,
)
from omni_hub.channels.base import InboundMessage
from omni_hub.cli import build_parser
from omni_hub.skill_stubs import regenerate_foundation, regenerate_functional, regenerate_all
from omni_hub.skill_sync import sync_skills


# ---------------------------------------------------------------------------
# P0 — CLI exposure
# ---------------------------------------------------------------------------


class CLIExposureTests(unittest.TestCase):
    """The 22 new CLI subcommands from cli/users.py / scheduling.py / inbox.py /
    projects.py / pptx.py / finance.py must all be discoverable."""

    EXPECTED = {
        # users (6)
        "user-list", "user-enroll", "user-approve",
        "user-set-persona", "user-memory-recall", "user-memory-archival",
        # scheduling (6)
        "cal-add", "cal-list", "personal-task-add",
        "personal-task-list", "personal-task-done", "schedule-plan",
        # inbox (1)
        "inbox-classify",
        # projects (3)
        "project-create", "project-list", "project-show",
        # pptx (1)
        "pptx-build",
        # finance (5)
        "finance-screen", "finance-watch-create", "finance-watch-list",
        "finance-portfolio-stats", "order-propose",
    }

    def test_all_v039_subcommands_registered(self) -> None:
        parser = build_parser()
        # argparse stores subparsers in the 'command' subparsers action.
        sub = next(
            a for a in parser._actions
            if hasattr(a, "choices") and a.choices and "wiki-init" in a.choices
        )
        registered = set(sub.choices.keys())
        missing = self.EXPECTED - registered
        self.assertEqual(missing, set(),
                          f"v0.39 review's P0 CLI gap: missing {sorted(missing)}")


# ---------------------------------------------------------------------------
# P0 — AppIntent layer (orthogonal to domain)
# ---------------------------------------------------------------------------


class AppIntentTests(unittest.TestCase):
    def test_intent_phrases_cover_8_intents(self) -> None:
        self.assertEqual(
            set(_APP_INTENT_PATTERNS),
            {"schedule", "task", "report", "pptx",
             "project", "inbox", "finance_op", "chat"},
        )

    def test_each_intent_maps_to_an_operation(self) -> None:
        for intent in _APP_INTENT_PATTERNS:
            self.assertIn(intent, _INTENT_OPERATION,
                          f"intent {intent} has no operation mapping")
            op, label = _INTENT_OPERATION[intent]
            self.assertTrue(op, f"intent {intent} missing operation name")

    def test_review_failing_query_now_routes_to_finance_schedule(self) -> None:
        """The exact query from the 2026-05-28 review."""
        msg = InboundMessage.new(
            channel="cli", sender="reviewer",
            body="明天上午提醒我复盘BTC和NVDA走势并安排日程",
        )
        decision = TaskRouter().route(msg)
        # Domain axis: BTC / NVDA / 走势 / 复盘 → finance.
        self.assertEqual(decision.selected_skill_id, "finance")
        # App-intent axis: 提醒/安排日程 → schedule (primary).
        self.assertEqual(decision.primary_intent, "schedule")
        # Recommended op is the schedule intent's canonical op.
        self.assertEqual(decision.recommended_operation, "calendar_add")
        # Multi-intent detection: 复盘 also fires the report intent.
        intent_names = {a.intent for a in decision.app_intents}
        self.assertIn("report", intent_names)

    def test_pure_chat_query_no_intent_falls_back_to_domain_recommendation(self) -> None:
        msg = InboundMessage.new(
            channel="cli", sender="me",
            body="Claude 4.7 有什么新特性",
        )
        decision = TaskRouter().route(msg)
        self.assertEqual(decision.selected_skill_id, "ai_progress")
        # No app intent in this query — falls back to domain-only recommendation.
        self.assertEqual(decision.primary_intent, "")
        self.assertEqual(decision.recommended_operation, "context_pack_build")

    def test_pptx_intent_recommends_pptx_build(self) -> None:
        msg = InboundMessage.new(
            channel="cli", sender="me",
            body="把这周的论文做一份 PPT 给同事看",
        )
        decision = TaskRouter().route(msg)
        # Primary intent could be pptx OR report depending on hit counts;
        # both are valid.  Verify pptx is at least detected.
        intents = {a.intent for a in decision.app_intents}
        self.assertIn("pptx", intents)


# ---------------------------------------------------------------------------
# P2 — risk_level diff normalisation
# ---------------------------------------------------------------------------


class RiskLevelDiffNormalisationTests(unittest.TestCase):
    """v0.37 review's leftover P2: MD "L0" vs registry 0 drift."""

    def test_sync_skills_reports_zero_drift_after_regenerate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            regenerate_foundation(workspace=tmp)
            regenerate_functional(workspace=tmp)
            regenerate_all(workspace=tmp)
            report = sync_skills(tmp, apply=True)
            self.assertEqual(report["drift"], [],
                              "risk_level (L0 vs 0) drift should be gone")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
