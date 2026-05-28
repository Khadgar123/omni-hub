"""Optimizer layer tests for DSPy/GEPA-ready skill evolution contracts."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.optimizer import (
    DatasetSplit,
    EvalGate,
    OptimizationRun,
    OptimizerStore,
    SkillVersion,
)
from omni_hub.testing import cli_runner as _run_cli


class OptimizerModelTests(unittest.TestCase):
    def test_eval_gate_passes_only_with_enough_holdout_and_metrics(self) -> None:
        split = DatasetSplit(train_count=120, dev_count=40, holdout_count=30)
        gate = EvalGate(
            metric_thresholds={"faithfulness": 0.90, "citation_support": 0.85},
            min_holdout_count=20,
        )
        self.assertEqual(
            gate.decide(
                split=split,
                holdout_metrics={"faithfulness": 0.91, "citation_support": 0.86},
            ),
            "passed",
        )
        self.assertEqual(
            gate.decide(
                split=split,
                holdout_metrics={"faithfulness": 0.89, "citation_support": 0.86},
            ),
            "failed",
        )
        self.assertEqual(
            gate.decide(
                split=DatasetSplit(train_count=120, dev_count=40, holdout_count=5),
                holdout_metrics={"faithfulness": 0.99, "citation_support": 0.99},
            ),
            "needs_review",
        )

    def test_skill_version_validation_rejects_empty_identity(self) -> None:
        self.assertIn("skill_id", SkillVersion(skill_id="", version="v1").validate()[0])
        self.assertEqual(
            SkillVersion(
                skill_id="qa",
                version="v1",
                domain="research",
                prompt_path="prompts/qa/v1/system_prompt.md",
            ).validate(),
            [],
        )


class OptimizerStoreTests(unittest.TestCase):
    def test_store_roundtrips_skill_versions_and_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OptimizerStore(tmp)
            skill = SkillVersion(
                skill_id="qa",
                version="v1",
                domain="research",
                prompt_path="prompts/qa/v1/system_prompt.md",
                optimizer="manual",
            )
            stored = store.register_skill_version(skill)
            self.assertEqual(stored.skill_id, "qa")

            run = OptimizationRun(
                skill_id="qa",
                optimizer="gepa",
                from_version="v1",
                to_version="v2",
                dataset_split=DatasetSplit(
                    train_count=120, dev_count=40, holdout_count=30,
                ),
                eval_gate=EvalGate(
                    metric_thresholds={"faithfulness": 0.90},
                    min_holdout_count=20,
                ),
                holdout_metrics={"faithfulness": 0.93},
                pareto_candidates=6,
            )
            stored_run = store.record_run(run)
            self.assertEqual(stored_run.gate_decision, "passed")

            versions = store.list_skill_versions(skill_id="qa")
            runs = store.list_runs(skill_id="qa")
            self.assertEqual([(v.skill_id, v.version) for v in versions], [("qa", "v1")])
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].optimizer, "gepa")
            self.assertEqual(runs[0].dataset_split.holdout_count, 30)


class OptimizerCliTests(unittest.TestCase):
    def test_cli_registers_and_lists_skill_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            registered = _run_cli(workspace, [
                "optimizer-skill-register",
                "--skill-id", "qa",
                "--version", "v1",
                "--domain", "research",
                "--prompt-path", "prompts/qa/v1/system_prompt.md",
                "--optimizer", "manual",
            ])
            self.assertEqual(registered["status"], "succeeded")
            self.assertEqual(registered["output"]["skill_id"], "qa")

            listed = _run_cli(workspace, [
                "optimizer-skill-list", "--skill-id", "qa",
            ])
            self.assertEqual(listed["status"], "succeeded")
            self.assertEqual(listed["output"]["count"], 1)
            self.assertEqual(listed["output"]["versions"][0]["version"], "v1")

    def test_cli_records_optimization_run_with_gate_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            result = _run_cli(workspace, [
                "optimizer-run-record",
                "--skill-id", "qa",
                "--optimizer", "gepa",
                "--from-version", "v1",
                "--to-version", "v2",
                "--train-count", "120",
                "--dev-count", "40",
                "--holdout-count", "30",
                "--metric", "faithfulness=0.93",
                "--threshold", "faithfulness=0.90",
                "--min-holdout-count", "20",
                "--pareto-candidates", "6",
            ])
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["output"]["gate_decision"], "passed")

            runs = _run_cli(workspace, [
                "optimizer-run-list", "--skill-id", "qa",
            ])
            self.assertEqual(runs["output"]["count"], 1)
            self.assertEqual(runs["output"]["runs"][0]["optimizer"], "gepa")

    def test_harness_compile_registers_optimizer_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _run_cli(workspace, [
                "harness-preference-add",
                "--domain", "qa",
                "--decision", "accepted",
                "--text", "The answer cites source [1].",
                "--reason", "grounded answer",
            ])
            compiled = _run_cli(workspace, [
                "harness-compile",
                "--domain", "qa",
                "--from-version", "v0",
                "--output-root", str(workspace / "prompts"),
                "--store-root", str(workspace / ".omni" / "preference"),
                "--backend", "manual",
            ])
            self.assertEqual(compiled["status"], "succeeded")
            self.assertEqual(compiled["output"]["to_version"], "v1")

            store = OptimizerStore(workspace, create=False)
            versions = store.list_skill_versions(skill_id="qa")
            runs = store.list_runs(skill_id="qa")
            self.assertEqual([(v.skill_id, v.version) for v in versions], [("qa", "v1")])
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].optimizer, "manual-fewshot")
            self.assertEqual(runs[0].gate_decision, "needs_review")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
