"""Tests for v0.17-A wiki-dream (local Anthropic Dreaming parity)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub import knowledge_plane
from omni_hub.proposals import ProposalStore
from omni_hub.wiki_dream import (
    ALL_RULES,
    RULE_CLUSTER_CANONICAL,
    RULE_RAW_ORPHAN,
    RULE_STALE_ACTIVE,
    RULE_STATEMENT_CLUSTER,
    run_dream,
)


def _seed_retrieval(root: Path, run_id: str, *, records: list[dict], domain: str = "research", now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    run_dir = root / ".omni" / "retrieval" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "evidence.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8",
    )
    (run_dir / "run_manifest.json").write_text(
        json.dumps({
            "run_id": run_id, "query": "x", "domain": domain,
            "written_at": now.isoformat(),
        }),
        encoding="utf-8",
    )


def _write_claim(root: Path, claim: dict) -> None:
    ledger = root / ".omni" / "claims.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(claim, ensure_ascii=False, sort_keys=True) + "\n")


class ClusterCanonicalTests(unittest.TestCase):
    def test_pairs_with_no_wiki_page_emit_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            _seed_retrieval(root, "run1", records=[
                {"source": "openalex", "title": "ACE", "canonical_id": "doi:10.x/ace", "snippet": "..."},
            ])
            _seed_retrieval(root, "run2", records=[
                {"source": "arxiv", "title": "ACE again", "canonical_id": "doi:10.x/ace", "snippet": "..."},
            ])
            report = run_dream(root, since_days=0, rules=[RULE_CLUSTER_CANONICAL])
            self.assertEqual(report.by_rule.get(RULE_CLUSTER_CANONICAL, 0), 1)
            self.assertEqual(
                report.findings[0].detail["canonical_id"],
                "doi:10.x/ace",
            )

    def test_skipped_when_existing_page_references_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            # Write a wiki page that already cites the canonical_id.
            page = root / "vault/wiki/concepts/ace.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text(
                "---\npage_type: concept\nsource_ids: [\"doi:10.x/ace\"]\n---\n# ACE\n",
                encoding="utf-8",
            )
            _seed_retrieval(root, "run1", records=[
                {"source": "openalex", "canonical_id": "doi:10.x/ace", "title": "x"},
                {"source": "arxiv", "canonical_id": "doi:10.x/ace", "title": "y"},
            ])
            report = run_dream(root, since_days=0, rules=[RULE_CLUSTER_CANONICAL])
            self.assertEqual(report.by_rule.get(RULE_CLUSTER_CANONICAL, 0), 0)


class StatementClusterTests(unittest.TestCase):
    def test_three_claims_across_pages_emit_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            for i in range(3):
                _write_claim(root, {
                    "claim_id": f"c{i}",
                    "domain": "research",
                    "statement": "Models converge via context evolution patterns.",
                    "target_path": f"vault/wiki/concepts/page-{i}.md",
                    "t_valid_to": None,
                    "review_state": "approved",
                })
            report = run_dream(root, since_days=0, rules=[RULE_STATEMENT_CLUSTER])
            self.assertEqual(report.by_rule.get(RULE_STATEMENT_CLUSTER, 0), 1)

    def test_three_claims_same_page_no_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            for i in range(3):
                _write_claim(root, {
                    "claim_id": f"c{i}",
                    "domain": "research",
                    "statement": "Same statement key here for trigger.",
                    "target_path": "vault/wiki/concepts/single.md",
                    "t_valid_to": None,
                    "review_state": "approved",
                })
            report = run_dream(root, since_days=0, rules=[RULE_STATEMENT_CLUSTER])
            self.assertEqual(report.by_rule.get(RULE_STATEMENT_CLUSTER, 0), 0)


class RawOrphanTests(unittest.TestCase):
    def test_raw_without_evidence_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            raw = root / "vault/raw/research/abc.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text("# orphan", encoding="utf-8")
            report = run_dream(root, since_days=0, rules=[RULE_RAW_ORPHAN])
            self.assertEqual(report.by_rule.get(RULE_RAW_ORPHAN, 0), 1)


class StaleActiveTests(unittest.TestCase):
    def test_old_page_with_fresh_hits_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            old_date = (datetime.now(UTC) - timedelta(days=120)).isoformat()
            page = root / "vault/wiki/concepts/stale-active.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text(
                f"---\npage_type: concept\nsource_ids: [\"doi:10.x/hot\"]\n"
                f"t_valid_from: {old_date}\nt_valid_to: null\n---\n# stale\n",
                encoding="utf-8",
            )
            for i in range(3):
                _seed_retrieval(root, f"hot{i}", records=[
                    {"source": "openalex", "canonical_id": "doi:10.x/hot", "title": "t"},
                ])
            report = run_dream(root, since_days=0, rules=[RULE_STALE_ACTIVE])
            self.assertEqual(report.by_rule.get(RULE_STALE_ACTIVE, 0), 1)


class PersistProposalsTests(unittest.TestCase):
    def test_persist_creates_one_proposal_per_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            _seed_retrieval(root, "r1", records=[
                {"source": "a", "canonical_id": "doi:10.x/p", "title": "x"},
                {"source": "b", "canonical_id": "doi:10.x/p", "title": "y"},
            ])
            report = run_dream(root, since_days=0, persist_proposals=True)
            self.assertGreaterEqual(report.total, 1)
            self.assertEqual(len(report.proposal_ids), report.total)
            stored = ProposalStore(root).list(kind="wiki_dream")
            self.assertEqual(len(stored), report.total)


class StateAdvanceTests(unittest.TestCase):
    def test_state_file_records_last_dream_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            run_dream(root, since_days=0)
            state_path = root / ".omni" / "wiki_dream_state.json"
            self.assertTrue(state_path.exists())
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("last_dream_at", state)


class AllRulesContractTests(unittest.TestCase):
    def test_all_rules_runs_all_four(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            report = run_dream(root, since_days=0)  # default = all rules
            self.assertEqual(set(ALL_RULES), {
                "cluster_canonical", "statement_cluster",
                "raw_orphan", "stale_active",
            })
            self.assertEqual(report.total, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
