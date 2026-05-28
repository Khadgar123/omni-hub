from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.harness.argilla_bridge import (  # noqa: E402
    build_dataset_settings,
    proposal_to_record,
)
from omni_hub.harness.preference import PreferenceStore  # noqa: E402
from omni_hub.proposals import Proposal, ProposalStore  # noqa: E402
from omni_hub.testing import cli_runner as _run_cli  # noqa: E402


class ArgillaBridgeTests(unittest.TestCase):
    def test_proposal_to_record_preserves_review_contract(self) -> None:
        proposal = Proposal(
            proposal_id="p-1",
            kind="generation",
            title="QA candidate",
            summary="Short answer",
            source_task_id="task-1",
            source_paths=["vault/source.md"],
            suggested_action="review_generation",
            confidence=0.82,
            payload={
                "text": "The answer cites source [1].",
                "model": "deepseek-v4-pro",
                "tokens_total": 120,
                "cost_usd": 0.0012,
                "artifact_id": "artifact-1",
            },
        )

        record = proposal_to_record(
            proposal,
            dataset="omni_proposal_review_v1",
            domain="research",
            skill_id="qa",
            skill_version="v1",
        )

        self.assertEqual(record["external_id"], "p-1")
        self.assertEqual(record["fields"]["candidate_text"], "The answer cites source [1].")
        self.assertEqual(record["fields"]["summary"], "Short answer")
        self.assertEqual(record["metadata"]["proposal_id"], "p-1")
        self.assertEqual(record["metadata"]["domain"], "research")
        self.assertEqual(record["metadata"]["skill_id"], "qa")
        self.assertEqual(record["metadata"]["model"], "deepseek-v4-pro")
        self.assertEqual(record["metadata"]["artifact_id"], "artifact-1")
        self.assertEqual(
            record["suggestions"][0],
            {"question_name": "decision", "value": "approve"},
        )

    def test_dataset_settings_are_versioned_and_separate_from_records(self) -> None:
        settings = build_dataset_settings("omni_proposal_review_v1")

        self.assertEqual(settings["name"], "omni_proposal_review_v1")
        self.assertEqual(settings["schema_version"], 1)
        self.assertIn("candidate_text", {field["name"] for field in settings["fields"]})
        self.assertIn("decision", {question["name"] for question in settings["questions"]})
        self.assertIn(
            "proposal_id",
            {meta["name"] for meta in settings["metadata_properties"]},
        )


class ArgillaCliFlowTests(unittest.TestCase):
    def test_export_and_sync_feedback_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            proposal_store = ProposalStore(workspace)
            proposal = Proposal(
                proposal_id="proposal-1",
                kind="generation",
                title="Draft answer",
                summary="Needs review",
                source_task_id="task-42",
                payload={
                    "text": "Obviously this works.",
                    "model": "deepseek-v4-pro",
                },
            )
            proposal_store.store(proposal, write_card=False)

            exported = _run_cli(workspace, [
                "argilla-export-proposals",
                "--output", ".omni/argilla/proposals.jsonl",
                "--kind", "generation",
                "--domain", "research",
                "--skill-id", "qa",
                "--skill-version", "v1",
            ])

            self.assertEqual(exported["status"], "succeeded")
            self.assertEqual(exported["output"]["count"], 1)
            export_path = workspace / exported["output"]["file"]
            rows = [
                json.loads(line)
                for line in export_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(rows[0]["external_id"], "proposal-1")
            self.assertEqual(rows[0]["metadata"]["skill_version"], "v1")

            feedback_path = workspace / ".omni" / "argilla" / "feedback.jsonl"
            feedback_path.write_text(
                json.dumps(
                    {
                        "external_id": "proposal-1",
                        "metadata": {
                            "domain": "research",
                            "skill_id": "qa",
                            "skill_version": "v1",
                        },
                        "responses": [
                            {
                                "user_id": "reviewer-1",
                                "values": {
                                    "decision": {"value": "edit"},
                                    "review_reason": {"value": "remove low-signal wording"},
                                    "corrected_text": {"value": "This works because source [1] says so."},
                                    "faithfulness": {"value": 4},
                                    "citation_support": {"value": 5},
                                },
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            synced = _run_cli(workspace, [
                "argilla-sync-feedback",
                "--input", ".omni/argilla/feedback.jsonl",
                "--preference-root", ".omni/preference",
            ])

            self.assertEqual(synced["status"], "succeeded")
            self.assertEqual(synced["output"]["synced"], 1)
            decided = ProposalStore(workspace, create=False).load("proposal-1")
            self.assertEqual(decided.state, "approved")
            self.assertEqual(decided.reason, "remove low-signal wording")
            self.assertEqual(decided.decided_by, "reviewer-1")

            prefs = list(PreferenceStore(workspace / ".omni" / "preference").read("research"))
            self.assertEqual(len(prefs), 1)
            self.assertEqual(prefs[0].task_id, "task-42")
            self.assertEqual(prefs[0].decision, "edited")
            self.assertEqual(prefs[0].candidate_text, "Obviously this works.")
            self.assertEqual(prefs[0].edited_text, "This works because source [1] says so.")
            self.assertEqual(prefs[0].judge_summary["faithfulness"], 4.0)
            self.assertEqual(prefs[0].judge_summary["citation_support"], 5.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
