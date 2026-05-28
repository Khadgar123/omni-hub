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


if __name__ == "__main__":
    unittest.main()
