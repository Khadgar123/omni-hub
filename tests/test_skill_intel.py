from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.audit import AuditLogger
from omni_hub.builtins import build_default_registry
from omni_hub.models import OperationSpec, OperationStatus, RiskLevel
from omni_hub.runner import OperationRunner
from omni_hub.skill_intel import (
    analyze_skill_set,
    evaluate_skill_quality,
    recommend_skills,
)
from omni_hub.skills import SkillKind, SkillSpec, SkillStatus


def sample_skills() -> list[SkillSpec]:
    return [
        SkillSpec(
            skill_id="url-capture",
            name="URL Capture",
            kind=SkillKind.CONNECTOR,
            description="Capture web pages and YouTube URLs into inbox cards.",
            status=SkillStatus.ACTIVE,
            entrypoint="operation:capture_url",
            risk_level=RiskLevel.LOCAL_WRITE,
            connectors=["web"],
            tags=["capture", "web", "youtube"],
        ),
        SkillSpec(
            skill_id="x-publish",
            name="X Publish",
            kind=SkillKind.CONNECTOR,
            description="Publish drafts to X.",
            status=SkillStatus.ACTIVE,
            entrypoint="operation:publish_x_post",
            risk_level=RiskLevel.EXTERNAL_PUBLISH,
            required_permissions=["x.write"],
            connectors=["x"],
            tags=["publish", "social"],
        ),
        SkillSpec(
            skill_id="web-capture-alt",
            name="Alternative Web Capture",
            kind=SkillKind.CONNECTOR,
            description="Another web capture implementation.",
            status=SkillStatus.DRAFT,
            entrypoint="operation:capture_url",
            risk_level=RiskLevel.LOCAL_WRITE,
            connectors=["web"],
            tags=["capture"],
        ),
    ]


class SkillIntelTests(unittest.TestCase):
    def test_recommend_skills_matches_query_and_penalizes_risk(self) -> None:
        recommendations = recommend_skills(sample_skills(), "youtube capture", limit=2)

        self.assertEqual(recommendations[0].skill_id, "url-capture")
        self.assertGreater(recommendations[0].score, recommendations[1].score)
        self.assertIn(recommendations[0].quality.grade, {"B", "A"})

    def test_recommend_skills_honors_max_risk(self) -> None:
        recommendations = recommend_skills(
            sample_skills(),
            "publish",
            max_risk=RiskLevel.LOCAL_WRITE,
        )

        self.assertEqual(recommendations, [])

    def test_evaluate_skill_quality_reports_metadata_gaps(self) -> None:
        quality = evaluate_skill_quality(sample_skills()[2])

        self.assertLess(quality.score, 0.7)
        self.assertIn("draft status", quality.issues)
        self.assertIn("missing input/output contract", quality.issues)

    def test_analyze_skill_set_detects_conflicts(self) -> None:
        analysis = analyze_skill_set(sample_skills())

        reasons = [conflict.reason for conflict in analysis.conflicts]
        self.assertIn("multiple skills share entrypoint operation:capture_url", reasons)
        self.assertIn("url-capture", analysis.skill_quality)
        self.assertTrue(
            any("publish-capable skills" in reason for reason in reasons)
        )

    def test_operations_recommend_and_analyze_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = OperationRunner(
                build_default_registry(tmpdir),
                audit=AuditLogger(Path(tmpdir) / "audit.jsonl"),
            )
            for skill in sample_skills():
                runner.run(
                    OperationSpec(
                        name="register_skill",
                        action="register",
                        payload=skill.to_dict(),
                        risk_level=RiskLevel.LOCAL_WRITE,
                    )
                )

            recommend_result = runner.run(
                OperationSpec(
                    name="recommend_skills",
                    action="recommend",
                    payload={"query": "capture youtube", "limit": 3},
                    risk_level=RiskLevel.READ_ONLY,
                )
            )
            self.assertEqual(recommend_result.status, OperationStatus.SUCCEEDED)
            self.assertEqual(
                recommend_result.output["recommendations"][0]["skill_id"],
                "url-capture",
            )

            analyze_result = runner.run(
                OperationSpec(
                    name="analyze_skills",
                    action="analyze",
                    payload={"skill_ids": ["url-capture", "web-capture-alt"]},
                    risk_level=RiskLevel.READ_ONLY,
                )
            )
            self.assertEqual(analyze_result.status, OperationStatus.SUCCEEDED)
            self.assertEqual(len(analyze_result.output["conflicts"]), 1)


if __name__ == "__main__":
    unittest.main()
