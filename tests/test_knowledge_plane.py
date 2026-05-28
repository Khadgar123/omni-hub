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

    def test_ingest_retrieval_evidence_produces_proposal_and_evidence_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            run_id = _seed_retrieval_run(root, domain="engineering")

            result = knowledge_plane.ingest_retrieval_evidence(
                root,
                run_id=run_id,
                domain="engineering",
            )

            self.assertEqual(result["domain"], "engineering")
            self.assertEqual(result["record_count"], 2)
            self.assertEqual(result["claim_count"], 2)
            self.assertEqual(len(result["evidence_files"]), 2)
            self.assertTrue(result["target_path"].startswith("vault/wiki/syntheses/"))

            proposal = result["proposal"]
            self.assertEqual(proposal.kind, "wiki_update")
            self.assertEqual(proposal.state, "pending")
            self.assertIn("ingest", proposal.payload)
            self.assertEqual(proposal.payload["ingest"]["run_id"], run_id)

            # vault/evidence is populated.
            for rel in result["evidence_files"]:
                self.assertTrue((root / rel).exists(), rel)

            # log.md got an ingest entry.
            log_body = (root / "vault" / "wiki" / "log.md").read_text(encoding="utf-8")
            self.assertIn("] ingest |", log_body)

            # Approve + apply lands wiki page + claims with bitemporal frontmatter.
            store = ProposalStore(root)
            store.approve(proposal.proposal_id, reason="cli smoke")
            applied = knowledge_plane.apply_wiki_proposal(root, proposal.proposal_id)

            page = (root / applied["target_path"]).read_text(encoding="utf-8")
            self.assertIn("page_type: synthesis", page)
            self.assertIn("t_valid_from:", page)
            self.assertIn("t_valid_to: null", page)
            self.assertIn("[R1]", page)

            claims = [
                json.loads(line)
                for line in (root / ".omni" / "claims.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            self.assertEqual(len(claims), 2)
            self.assertTrue(all("t_valid_from" in c for c in claims))
            self.assertTrue(all(c["t_valid_to"] is None for c in claims))

    def test_init_layout_overwrites_stale_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schema = root / "vault" / "wiki" / "AGENTS.md"
            schema.parent.mkdir(parents=True)
            schema.write_text("# Old stub\n", encoding="utf-8")

            knowledge_plane.init_layout(root)

            new_body = schema.read_text(encoding="utf-8")
            self.assertIn("schema_version: v0.19", new_body)
            self.assertIn("page_type: concept", new_body)
            self.assertIn("t_valid_from", new_body)

    def test_append_log_writes_karpathy_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)

            entry = knowledge_plane.append_log(
                root, op="manual", summary="check ingest pipeline", source="task-42",
            )
            self.assertEqual(entry["op"], "manual")
            log_body = (root / "vault" / "wiki" / "log.md").read_text(encoding="utf-8")
            self.assertIn("] manual | check ingest pipeline", log_body)
            self.assertIn("- source: task-42", log_body)

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


def _seed_retrieval_run(root: Path, *, domain: str = "engineering") -> str:
    """Mock a `.omni/retrieval/<run_id>/` directory matching EvidenceStore's layout."""
    run_id = "20260528T120000Z-abcdef01"
    run_dir = root / ".omni" / "retrieval" / run_id
    run_dir.mkdir(parents=True)
    records = [
        {
            "source": "openalex",
            "title": "Agentic Context Engineering: Evolving Contexts",
            "url": "https://openalex.org/W4399123456",
            "snippet": (
                "ACE evolves a persistent context document across episodes, "
                "treating it as a tactical wiki the agent reads and edits."
            ),
            "canonical_id": "doi:10.48550/arxiv.2510.04618",
            "cite_id": "R1",
            "score": 0.92,
        },
        {
            "source": "arxiv",
            "title": "GEPA: Reflective Prompt Evolution",
            "url": "https://arxiv.org/abs/2507.19457",
            "snippet": (
                "GEPA improves a prompt via reflective edits judged by an "
                "evaluator, achieving Pareto gains on coding benchmarks."
            ),
            "canonical_id": "arxiv:2507.19457",
            "cite_id": "R2",
            "score": 0.88,
        },
    ]
    (run_dir / "evidence.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    (run_dir / "sources.json").write_text(
        json.dumps({"count": 2, "urls": [r["url"] for r in records]}),
        encoding="utf-8",
    )
    (run_dir / "run_manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "query": "agentic context engineering",
                "domain": domain,
                "fusion": "rrf",
                "record_count": 2,
                "sources_tried": ["openalex", "arxiv"],
                "sources_succeeded": ["openalex", "arxiv"],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    return run_id


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
