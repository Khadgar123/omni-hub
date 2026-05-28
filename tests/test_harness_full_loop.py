"""End-to-end harness flywheel: ensemble → judge → preference → compile → report.

The per-stage unit suites already cover internals; this test proves the
artifacts hand off cleanly between stages and that the OperationRunner audit
trail (added in P0-4) records every harness write.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.harness import dspy_compile, judge_ensemble
from omni_hub.harness.models import Candidate, GenerationRecord, JudgeRubric
from omni_hub.harness.preference import PreferenceStore
from omni_hub.reports import build_daily
from omni_hub.testing import cli_runner as _run_cli


GROUNDED_TEXT = (
    "The buffer cache hit rate rose to 92% [1].\n"
    "Mean query latency dropped from 38ms to 11ms [2].\n"
    "The shadow read path now serves 60% of warm requests [3]."
)
FLUFF_TEXT = (
    "In recent years, numerous studies have shown that performance plays "
    "an important role. Obviously this is significant."
)


def _build_record(*texts: str) -> GenerationRecord:
    return GenerationRecord(
        candidates=[Candidate(model=f"m{i}", text=t) for i, t in enumerate(texts)],
    )


class FullFlywheelTests(unittest.TestCase):
    def _run_flywheel(self, workspace: Path, domain: str) -> dict:
        # Stage 1 — ensemble outputs three candidates.  The ensemble unit tests
        # cover real HTTP fan-out; here we hand-build the record so the test
        # stays hermetic.
        record = _build_record(GROUNDED_TEXT, FLUFF_TEXT, GROUNDED_TEXT[:120])

        # Stage 2 — multi-judge scoring + bias audit; pick a winner.
        rubric = JudgeRubric()
        judges = [
            judge_ensemble.LocalHeuristicJudge(f"j{i}", f"heuristic-{i}")
            for i in range(3)
        ]
        outcome = judge_ensemble.run_judges(record, judges, rubric)
        self.assertIsNotNone(outcome.winner_candidate_id)
        winner = next(
            c for c in record.candidates
            if c.candidate_id == outcome.winner_candidate_id
        )
        self.assertNotEqual(winner.text, FLUFF_TEXT,
                            "heuristic must prefer the grounded candidate")

        # Stage 3 — persist accepted + rejected preferences through the CLI
        # so OperationRunner + AuditLogger fire (P0-4 gate).
        accepted = _run_cli(workspace, [
            "harness-preference-add",
            "--domain", domain,
            "--decision", "accepted",
            "--text", winner.text,
            "--reason", "well-grounded winner",
            "--task-id", f"flywheel-{domain}-pos",
        ])
        self.assertEqual(accepted["__exit"], 0)
        self.assertEqual(accepted["status"], "succeeded")
        self.assertTrue(accepted["audit_id"])

        rejected = _run_cli(workspace, [
            "harness-preference-add",
            "--domain", domain,
            "--decision", "rejected",
            "--text", FLUFF_TEXT,
            "--reason", "low signal phrases",
            "--task-id", f"flywheel-{domain}-neg",
        ])
        self.assertEqual(rejected["__exit"], 0)
        self.assertTrue(rejected["audit_id"])

        # The on-disk preference store should now reflect both decisions.
        store = PreferenceStore(workspace / ".omni" / "preference")
        stats = store.stats(domain)
        self.assertEqual(stats["accepted"], 1)
        self.assertEqual(stats["rejected"], 1)

        # Stage 4 — compile a new prompt version using the manual backend so
        # the test does not require the (optional) DSPy fork to be installed.
        output_root = workspace / "prompts"
        report = dspy_compile.compile(
            domain=domain,
            from_version="v0",
            output_root=output_root,
            preference_store=store,
            backend="manual",
        )
        self.assertEqual(report.to_version, "v1")
        self.assertEqual(report.backend, "manual-fewshot")
        self.assertGreaterEqual(report.positive_used, 1)
        self.assertGreaterEqual(report.negative_used, 1)
        prompt_dir = output_root / domain / "v1"
        self.assertTrue((prompt_dir / "compile_report.json").exists())

        # Stage 5 — the daily report should surface preference activity from
        # the store we just populated.
        body, _ctx = build_daily(workspace=workspace)
        self.assertIn("Preference flywheel", body)
        self.assertIn(domain, body)

        return {"stats": stats, "report_body": body, "compile": report.to_dict()}

    def test_flywheel_runs_clean_for_engineering_domain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_flywheel(Path(tmp), "engineering")
            self.assertEqual(result["stats"]["total"], 2)
            self.assertIn("engineering", result["report_body"])

    def test_flywheel_runs_clean_for_research_domain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_flywheel(Path(tmp), "research")
            self.assertEqual(result["stats"]["total"], 2)
            self.assertIn("research", result["report_body"])

    def test_harness_writes_are_audited(self) -> None:
        """Every harness write CLI must leave events in .omni/audit/events.jsonl."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _run_cli(workspace, [
                "harness-preference-add",
                "--domain", "engineering",
                "--decision", "accepted",
                "--text", "auditable claim with citation [1]",
                "--reason", "audit smoke",
            ])
            _run_cli(workspace, [
                "harness-redundancy-scan",
                "--db-path", str(workspace / "missing.sqlite3"),
            ])

            audit_path = workspace / ".omni" / "audit" / "events.jsonl"
            self.assertTrue(audit_path.exists())
            events = [
                json.loads(line)
                for line in audit_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            event_types = [e["event_type"] for e in events]
            self.assertIn("policy_evaluated", event_types)
            self.assertIn("operation_started", event_types)
            self.assertIn("operation_succeeded", event_types)
            op_names = {
                e["data"]["operation"]["name"]
                for e in events
                if e["event_type"] == "operation_started"
            }
            self.assertIn("harness_preference_add", op_names)
            self.assertIn("harness_redundancy_scan", op_names)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
