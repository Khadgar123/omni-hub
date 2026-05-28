from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.harness import domain_profiles


REPO = Path(__file__).resolve().parents[1]
PROFILES_PATH = REPO / "agent-harness" / "domain-profiles.json"


class DomainProfileLoaderTests(unittest.TestCase):
    def test_all_v019_domains_load(self) -> None:
        ids = domain_profiles.list_ids(PROFILES_PATH)
        self.assertEqual(set(ids), {
            # v0.13 (policy renamed to us_policy in v0.19)
            "engineering", "research", "photography", "fashion",
            "chat_relationships", "finance",
            "us_policy", "cn_policy",                # split in v0.19
            "international_relations",
            # v0.19 new verticals
            "meta", "fitness_wellness", "cooking", "travel",
            "marketing", "enterprise",
        })

    def test_each_profile_has_required_fields(self) -> None:
        for prof in domain_profiles.load_all(PROFILES_PATH).values():
            self.assertTrue(prof.goal)
            self.assertGreaterEqual(len(prof.required_context), 3)
            self.assertGreaterEqual(len(prof.proposal_rules), 3)
            self.assertGreaterEqual(len(prof.judge_dimensions), 3)

    def test_get_raises_for_unknown_domain(self) -> None:
        with self.assertRaises(KeyError):
            domain_profiles.get("astrology", path=PROFILES_PATH)


class TaskPacketTemplateTests(unittest.TestCase):
    def test_template_validates_for_each_domain(self) -> None:
        for domain_id in domain_profiles.list_ids(PROFILES_PATH):
            packet = domain_profiles.build_task_packet_template(
                domain_id, path=PROFILES_PATH,
                goal=f"test goal for {domain_id}",
            )
            errors = packet.validate()
            self.assertEqual(
                errors, [],
                f"{domain_id} template should validate, got errors: {errors}",
            )

    def test_finance_template_uses_finance_overrides(self) -> None:
        packet = domain_profiles.build_task_packet_template(
            "finance", path=PROFILES_PATH, goal="evaluate AAPL Q1 risk",
        )
        # finance must require fresh sources
        self.assertTrue(packet.retrieval_policy.freshness_required)
        self.assertGreaterEqual(packet.retrieval_policy.min_sources, 4)
        # uncertainty_calibration weight should be at least 0.15 after extras dilution
        weight = packet.judge_rubric.uncertainty_calibration
        self.assertGreater(weight, 0.10)
        # extras carry the domain-specific dimensions
        self.assertIn("data_freshness", packet.judge_rubric.extras)
        self.assertIn("risk_disclosure", packet.judge_rubric.extras)

    def test_us_policy_template_includes_jurisdiction_extras(self) -> None:
        packet = domain_profiles.build_task_packet_template(
            "us_policy", path=PROFILES_PATH, goal="explain the EU AI Act fit",
        )
        self.assertIn("jurisdiction_fit", packet.judge_rubric.extras)
        self.assertIn("source_precision", packet.judge_rubric.extras)

    def test_cn_policy_template_includes_jurisdiction_extras(self) -> None:
        packet = domain_profiles.build_task_packet_template(
            "cn_policy", path=PROFILES_PATH, goal="解读最新央行规定",
        )
        self.assertIn("jurisdiction_fit", packet.judge_rubric.extras)
        self.assertIn("source_precision", packet.judge_rubric.extras)

    def test_international_relations_template_weighs_uncertainty_high(self) -> None:
        packet = domain_profiles.build_task_packet_template(
            "international_relations", path=PROFILES_PATH,
            goal="analyse a hypothetical sanctions escalation",
        )
        rubric = packet.judge_rubric
        self.assertGreater(rubric.uncertainty_calibration, rubric.style_fit)
        self.assertIn("scenario_reasoning", rubric.extras)

    def test_all_templates_serialise_to_json(self) -> None:
        for packet in domain_profiles.all_templates(PROFILES_PATH):
            # round-trip through JSON
            text = json.dumps(packet.to_dict(), ensure_ascii=False)
            self.assertIn(packet.domain_profile, text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
