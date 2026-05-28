from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.harness.models import (
    Candidate,
    GenerationRecord,
    JudgeRubric,
    JudgeScore,
    TaskPacket,
)


class TaskPacketTests(unittest.TestCase):
    def test_default_packet_has_required_fields(self) -> None:
        packet = TaskPacket(goal="write research paragraph", domain_profile="research")
        errors = packet.validate()
        self.assertEqual(errors, [])
        self.assertTrue(packet.task_id)
        self.assertEqual(packet.schema_version, 1)

    def test_validate_rejects_empty_goal(self) -> None:
        packet = TaskPacket(goal="   ", domain_profile="research")
        errors = packet.validate()
        self.assertIn("goal must be non-empty", errors)

    def test_validate_rejects_unbalanced_rubric(self) -> None:
        packet = TaskPacket(
            goal="x",
            domain_profile="research",
            judge_rubric=JudgeRubric(
                evidence_coverage=0.5,
                information_density=0.5,
                citation_support=0.5,
                style_fit=0.5,
                uncertainty_calibration=0.5,
            ),
        )
        errors = packet.validate()
        self.assertTrue(any("judge_rubric weights" in e for e in errors))

    def test_roundtrip_through_dict(self) -> None:
        packet = TaskPacket(
            goal="x",
            domain_profile="engineering",
            sources_required=["a.md"],
            claims_to_cover=["c1", "c2"],
        )
        data = packet.to_dict()
        roundtrip = TaskPacket.from_dict(data)
        self.assertEqual(roundtrip.goal, packet.goal)
        self.assertEqual(roundtrip.sources_required, packet.sources_required)
        self.assertEqual(roundtrip.claims_to_cover, packet.claims_to_cover)


class GenerationRecordTests(unittest.TestCase):
    def test_best_candidate_uses_weighted_median(self) -> None:
        rubric = JudgeRubric(
            evidence_coverage=0.4,
            information_density=0.3,
            citation_support=0.2,
            style_fit=0.05,
            uncertainty_calibration=0.05,
        )
        c_high = Candidate(
            model="m-high",
            text="grounded",
            judge_scores=[
                JudgeScore(
                    judge_id="j1",
                    model="claude",
                    dimensions={
                        "evidence_coverage": 0.9,
                        "information_density": 0.8,
                        "citation_support": 0.9,
                        "style_fit": 0.7,
                        "uncertainty_calibration": 0.7,
                    },
                ),
                JudgeScore(
                    judge_id="j2",
                    model="deepseek",
                    dimensions={
                        "evidence_coverage": 0.85,
                        "information_density": 0.75,
                        "citation_support": 0.85,
                        "style_fit": 0.7,
                        "uncertainty_calibration": 0.7,
                    },
                ),
                JudgeScore(
                    judge_id="j3",
                    model="codex",
                    dimensions={  # noisy outlier — median should resist
                        "evidence_coverage": 0.1,
                        "information_density": 0.1,
                        "citation_support": 0.1,
                        "style_fit": 0.1,
                        "uncertainty_calibration": 0.1,
                    },
                ),
            ],
        )
        c_low = Candidate(
            model="m-low",
            text="generic",
            judge_scores=[
                JudgeScore(
                    judge_id="j1",
                    model="claude",
                    dimensions={k: 0.4 for k in [
                        "evidence_coverage",
                        "information_density",
                        "citation_support",
                        "style_fit",
                        "uncertainty_calibration",
                    ]},
                ),
                JudgeScore(
                    judge_id="j2",
                    model="deepseek",
                    dimensions={k: 0.45 for k in [
                        "evidence_coverage",
                        "information_density",
                        "citation_support",
                        "style_fit",
                        "uncertainty_calibration",
                    ]},
                ),
                JudgeScore(
                    judge_id="j3",
                    model="codex",
                    dimensions={k: 0.5 for k in [
                        "evidence_coverage",
                        "information_density",
                        "citation_support",
                        "style_fit",
                        "uncertainty_calibration",
                    ]},
                ),
            ],
        )

        record = GenerationRecord(candidates=[c_low, c_high])
        winner = record.best_candidate_by_judge(rubric)
        assert winner is not None
        self.assertEqual(winner.model, "m-high")

    def test_best_candidate_ignores_errored_or_unjudged(self) -> None:
        record = GenerationRecord(
            candidates=[
                Candidate(model="errored", error="boom"),
                Candidate(model="unjudged", text="..."),
            ]
        )
        self.assertIsNone(record.best_candidate_by_judge(JudgeRubric()))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
