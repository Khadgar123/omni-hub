from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.cli import main


class KnowledgePlaneCliTests(unittest.TestCase):
    def test_wiki_cli_round_trip_from_research_proposal_to_context_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_paperbite(root)
            original_cwd = os.getcwd()
            try:
                os.chdir(root)
                init_payload = _run_cli(["wiki-init"])
                self.assertEqual(init_payload["status"], "succeeded")

                propose_payload = _run_cli(
                    [
                        "wiki-propose-research",
                        "--source",
                        "paperbite",
                        "--path",
                        "analysis/ICLR_2026/Agentic_Context.md",
                    ]
                )
                proposal_id = propose_payload["output"]["proposal"]["proposal_id"]
                self.assertEqual(propose_payload["output"]["proposal"]["kind"], "wiki_update")

                approve_payload = _run_cli(
                    ["propose-approve", "--id", proposal_id, "--reason", "cli smoke"]
                )
                self.assertEqual(approve_payload["status"], "succeeded")

                apply_payload = _run_cli(["wiki-apply-proposal", "--proposal", proposal_id])
                self.assertEqual(apply_payload["status"], "succeeded")
                self.assertTrue((root / apply_payload["output"]["target_path"]).exists())

                pack_payload = _run_cli(
                    [
                        "context-pack-build",
                        "--query",
                        "context engineering",
                        "--domain",
                        "research",
                        "--persist",
                    ]
                )
                self.assertEqual(pack_payload["status"], "succeeded")
                self.assertGreaterEqual(len(pack_payload["output"]["wiki_results"]), 1)
                self.assertGreaterEqual(len(pack_payload["output"]["research_results"]), 1)
                self.assertTrue(Path(pack_payload["output"]["path"]).exists())
            finally:
                os.chdir(original_cwd)


def _run_cli(argv: list[str]) -> dict[str, object]:
    buffer = StringIO()
    with redirect_stdout(buffer):
        exit_code = main(argv)
    payload = json.loads(buffer.getvalue())
    if exit_code != 0:
        raise AssertionError(payload)
    return payload


def _seed_paperbite(root: Path) -> None:
    pb = root / "agent-harness" / "paperbite"
    note = pb / "analysis" / "ICLR_2026" / "Agentic_Context.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "# Agentic Context Engineering\n\n"
        "ACE evolves context as a persistent tactical wiki for agents.\n",
        encoding="utf-8",
    )
    index = pb / "index" / "index.jsonl"
    index.parent.mkdir(parents=True)
    index.write_text(
        json.dumps(
            {
                "title": "Agentic Context Engineering",
                "analysis_path": "analysis/ICLR_2026/Agentic_Context.md",
                "core_operator": "Context becomes a persistent tactical wiki.",
                "primary_logic": "Curators update context incrementally.",
                "methods": ["ACE", "context engineering"],
                "topics": ["agent memory"],
                "paper_link": "https://openreview.net/forum?id=eC4ygDs02R",
                "venue_year": "ICLR_2026",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
