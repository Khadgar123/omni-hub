from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub import knowledge_plane
from omni_hub.proposals import ProposalStore


class KnowledgePlaneTests(unittest.TestCase):
    def test_init_layout_creates_karpathy_wiki_dirs_and_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            status = knowledge_plane.init_layout(root)

            self.assertTrue((root / "vault" / "raw").is_dir())
            self.assertTrue((root / "vault" / "evidence").is_dir())
            self.assertTrue((root / "vault" / "wiki" / "domains" / "research").is_dir())
            self.assertTrue((root / "vault" / "wiki" / "claims").is_dir())
            self.assertTrue((root / "vault" / "wiki" / "index.md").exists())
            self.assertTrue((root / "vault" / "wiki" / "log.md").exists())
            self.assertTrue((root / "vault" / "wiki" / "AGENTS.md").exists())
            self.assertEqual(status["wiki"]["ready"], True)

    def test_research_wiki_update_is_proposed_then_applied_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_paperbite(root)

            proposed = knowledge_plane.propose_research_wiki_update(
                root,
                source_id="paperbite",
                analysis_path="analysis/ICLR_2026/Agentic_Context.md",
            )

            proposal = proposed["proposal"]
            self.assertEqual(proposal.kind, "wiki_update")
            self.assertEqual(proposal.state, "pending")
            self.assertEqual(
                proposal.payload["target_path"],
                "vault/wiki/domains/research/agentic-context-engineering.md",
            )
            self.assertFalse((root / proposal.payload["target_path"]).exists())

            store = ProposalStore(root)
            store.approve(proposal.proposal_id, reason="reviewed")
            applied = knowledge_plane.apply_wiki_proposal(root, proposal.proposal_id)

            target = root / "vault/wiki/domains/research/agentic-context-engineering.md"
            self.assertTrue(target.exists())
            body = target.read_text(encoding="utf-8")
            self.assertIn("source_id: paperbite", body)
            self.assertIn("Agentic Context Engineering", body)
            self.assertEqual(applied["target_path"], str(target.relative_to(root)))

            claims_path = root / ".omni" / "claims.jsonl"
            claims = [
                json.loads(line)
                for line in claims_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(claims[0]["domain"], "research")
            self.assertIn("context", claims[0]["statement"].lower())

    def test_context_pack_combines_compiled_wiki_and_research_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            wiki_page = root / "vault/wiki/domains/research/context-engineering.md"
            wiki_page.write_text(
                "# Context Engineering\n\n"
                "Context engineering keeps persistent tactical knowledge in wiki pages.\n",
                encoding="utf-8",
            )
            _seed_paperbite(root)

            pack = knowledge_plane.build_context_pack(
                root,
                query="context engineering",
                domain="research",
                wiki_limit=3,
                research_limit=3,
                persist=True,
            )

            self.assertEqual(pack.query, "context engineering")
            self.assertEqual(pack.domain, "research")
            self.assertEqual(len(pack.wiki_results), 1)
            self.assertEqual(len(pack.research_results), 1)
            self.assertTrue(Path(pack.path).exists())
            saved = json.loads(Path(pack.path).read_text(encoding="utf-8"))
            self.assertEqual(saved["domain"], "research")
            self.assertEqual(saved["research_results"][0]["source_id"], "paperbite")


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
                "core_operator": (
                    "Context is maintained as a persistent tactical wiki for "
                    "self-improving agents."
                ),
                "primary_logic": (
                    "Generator, reflector, and curator roles update context "
                    "incrementally without overwriting useful evidence."
                ),
                "methods": ["ACE", "context engineering"],
                "topics": ["agent memory"],
                "paper_link": "https://openreview.net/forum?id=eC4ygDs02R",
                "venue_year": "ICLR_2026",
                "year": 2026,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
