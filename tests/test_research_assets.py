from pathlib import Path
import json
from unittest import mock
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub import research_assets


class ResearchAssetsTests(unittest.TestCase):
    def test_status_and_search_across_research_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rf = root / "agent-harness" / "researchflow"
            pb = root / "agent-harness" / "paperbite"
            (rf / "obsidian-vault" / "index").mkdir(parents=True)
            (rf / "obsidian-vault" / "analysis" / "ICLR_2026").mkdir(parents=True)
            (pb / "index").mkdir(parents=True)
            (pb / "analysis" / "ICLR_2026").mkdir(parents=True)

            rf_note = (
                rf / "obsidian-vault" / "analysis" / "ICLR_2026" / "AdaReasoner.md"
            )
            rf_note.write_text("# AdaReasoner\n\nTool orchestration.", encoding="utf-8")
            pb_note = pb / "analysis" / "ICLR_2026" / "Agentic_Context.md"
            pb_note.write_text("# Agentic Context\n\nContext evolution.", encoding="utf-8")

            _write_jsonl(
                rf / "obsidian-vault" / "index" / "index.jsonl",
                [
                    {
                        "title": "AdaReasoner",
                        "analysis_path": (
                            "obsidian-vault/analysis/ICLR_2026/AdaReasoner.md"
                        ),
                        "methods": ["tool orchestration"],
                        "primary_logic": "adaptive tool use",
                    }
                ],
            )
            _write_jsonl(
                pb / "index" / "index.jsonl",
                [
                    {
                        "title": "Agentic Context Engineering",
                        "analysis_path": "analysis/ICLR_2026/Agentic_Context.md",
                        "methods": ["context engineering"],
                        "primary_logic": "self-improving contexts",
                    }
                ],
            )

            status = research_assets.status(root)
            self.assertEqual(status["total_index_records"], 2)
            self.assertEqual(status["total_analysis_notes"], 2)

            results = research_assets.search("context", workspace=root)
            self.assertEqual(results[0].source_id, "paperbite")
            self.assertEqual(results[0].title, "Agentic Context Engineering")

            read = research_assets.read_analysis(
                results[0].analysis_path,
                workspace=root,
                source_id=results[0].source_id,
            )
            self.assertIn("Context evolution", read["body"])

    def test_read_analysis_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agent-harness" / "paperbite" / "index").mkdir(parents=True)
            with self.assertRaises(PermissionError):
                research_assets.read_analysis(
                    "../../secret.md",
                    workspace=root,
                    source_id="paperbite",
                )

    def test_read_analysis_falls_back_to_git_for_sparse_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agent-harness" / "paperbite").mkdir(parents=True)
            completed = mock.Mock()
            completed.returncode = 0
            completed.stdout = "# Sparse note\n"
            completed.stderr = ""
            with mock.patch("omni_hub.research_assets.subprocess.run", return_value=completed):
                read = research_assets.read_analysis(
                    "analysis/ICLR_2026/Sparse.md",
                    workspace=root,
                    source_id="paperbite",
                )
            self.assertEqual(read["body"], "# Sparse note\n")

    def test_researchflow_skill_inventory_reads_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = (
                root
                / "agent-harness"
                / "researchflow"
                / ".claude"
                / "skills"
                / "research-workflow"
                / "SKILL.md"
            )
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\n"
                "name: research-workflow\n"
                "status: active-router\n"
                "mode: local-file-workflow\n"
                "description: >\n"
                "  Unified research pipeline router.\n"
                "---\n"
                "# Body\n",
                encoding="utf-8",
            )

            skills = research_assets.list_researchflow_skills(root)
            self.assertEqual(len(skills), 1)
            self.assertEqual(skills[0].name, "research-workflow")
            self.assertEqual(skills[0].status, "active-router")
            self.assertIn("Unified", skills[0].description)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

class ResearchFlowClaimsAdapterTests(unittest.TestCase):
    """WS3: main_analysis.json -> candidate claims (omni-hub schema)."""

    ANALYSIS = {
        "paper_metadata": {"title": "ACE", "venue": "ICLR", "year": 2026},
        "analysis_truth": {
            "core_insight": "Context can be evolved as a playbook rather than fine-tuned.",
            "decisive_evidence": [
                {"claim": "Grow-and-refine avoids context collapse.",
                 "anchor": "sec3.2", "confidence": 0.8},
            ],
        },
        "method": {
            "proposed_method_name": "ACE",
            "changed_slots": [
                {"slot_name": "context update", "baseline_value": "full rewrite",
                 "proposed_value": "delta merge", "evidence_anchor": "sec4",
                 "confidence": 0.7},
            ],
        },
        "experiments": {
            "main_results": [
                {"benchmark": "AppWorld", "metric": "success", "proposed": "59.4",
                 "baseline": "48.8", "delta": "+10.6", "anchor": "tab2",
                 "confidence": 0.75},
            ],
        },
    }

    def _convert(self):
        return research_assets.researchflow_analysis_to_claims(
            self.ANALYSIS, source_id="rf:ace",
            analysis_path="analysis/ICLR_2026/ACE.md",
        )

    def test_extracts_three_claim_families(self) -> None:
        kinds = {c["support"][0]["claim_kind"] for c in self._convert()}
        self.assertEqual(kinds, {"conclusion", "method", "result"})

    def test_claims_match_omni_schema(self) -> None:
        required = {"claim_id", "domain", "statement", "support", "confidence",
                    "review_state", "t_valid_from", "t_valid_to", "supersedes",
                    "superseded_by"}
        for c in self._convert():
            self.assertTrue(required <= set(c), f"missing keys in {set(c)}")
            self.assertEqual(c["review_state"], "proposed")
            self.assertEqual(c["domain"], "research")
            self.assertIn("anchor", c["support"][0])

    def test_deterministic_unique_ids(self) -> None:
        a = [c["claim_id"] for c in self._convert()]
        b = [c["claim_id"] for c in self._convert()]
        self.assertEqual(a, b)
        self.assertEqual(len(set(a)), len(a))

    def test_malformed_is_skipped_not_crashed(self) -> None:
        bad = {"analysis_truth": {"decisive_evidence": [None, {}, {"claim": ""}]},
               "method": "notadict", "experiments": {"main_results": ["x"]}}
        self.assertEqual(research_assets.researchflow_analysis_to_claims(bad), [])
        self.assertEqual(research_assets.researchflow_analysis_to_claims(None), [])


class ResearchFlowProposalRoundtripTests(unittest.TestCase):
    """WS3 end-to-end: RF analysis -> Proposal -> approve -> apply (WS1 projection)."""

    def test_analysis_to_proposal_to_projected_page(self) -> None:
        from omni_hub import knowledge_plane
        from omni_hub.proposals import ProposalStore

        analysis = ResearchFlowClaimsAdapterTests.ANALYSIS
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            (root / ".omni").mkdir(exist_ok=True)
            (root / ".omni" / "rf.json").write_text(
                json.dumps(analysis), encoding="utf-8")

            res = research_assets.propose_researchflow_analysis(
                root, analysis_json=".omni/rf.json")
            self.assertIsInstance(res["proposal_id"], str)
            self.assertEqual(res["claim_count"], 4)
            self.assertTrue(res["target_path"].startswith("vault/wiki/syntheses/"))

            ProposalStore(root).approve(res["proposal_id"], reason="test")
            applied = knowledge_plane.apply_wiki_proposal(root, res["proposal_id"])
            self.assertEqual(applied["claims_written"], 4)
            body = (root / applied["target_path"]).read_text(encoding="utf-8")
            self.assertIn("rendered_from: claims", body)   # WS1 projection
            self.assertIn("Grow-and-refine", body)          # conclusion claim
            self.assertIn("AppWorld", body)                 # result claim

    def test_empty_analysis_rejected(self) -> None:
        from omni_hub import knowledge_plane

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            (root / ".omni").mkdir(exist_ok=True)
            (root / ".omni" / "empty.json").write_text(
                json.dumps({"paper_metadata": {"title": "x"}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                research_assets.propose_researchflow_analysis(
                    root, analysis_json=".omni/empty.json")


if __name__ == "__main__":
    unittest.main()
