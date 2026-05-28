"""Tests for the Karpathy wiki-lint six rules + supersede + conflict-resolve."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub import knowledge_plane
from omni_hub.proposals import ProposalStore
from omni_hub.wiki_lint import (
    ALL_RULES,
    RULE_BROKEN_CROSS_REF,
    RULE_CONTRADICTION,
    RULE_DATA_GAP,
    RULE_MISSING_CONCEPT,
    RULE_ORPHAN_PAGE,
    RULE_STALE_FACT,
    lint_wiki,
)


def _seed_wiki(root: Path) -> None:
    knowledge_plane.init_layout(root)


def _write_page(root: Path, relative: str, frontmatter: dict, body: str = "") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in frontmatter.items():
        if v is None:
            lines.append(f"{k}: null")
        elif isinstance(v, list):
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    if body:
        lines.append(body)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_claim(root: Path, claim: dict) -> None:
    ledger = root / ".omni" / "claims.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(claim, ensure_ascii=False, sort_keys=True) + "\n")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class WikiLintRuleTests(unittest.TestCase):
    def test_contradiction_detects_divergent_stance_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_wiki(root)
            # Two approved claims sharing the same statement key with
            # disjoint support sets + confidence gap >= 0.2.
            _write_claim(root, {
                "claim_id": "c_a",
                "statement": "ACE evolves the wiki incrementally without overwriting.",
                "support": [{"source_id": "arxiv:2510.04618"}],
                "against": [],
                "confidence": 0.85,
                "review_state": "approved",
                "t_valid_from": _now_iso(),
                "t_valid_to": None,
            })
            _write_claim(root, {
                "claim_id": "c_b",
                "statement": "ACE evolves the wiki incrementally without overwriting.",
                "support": [{"source_id": "openalex:W4399"}],
                "against": [],
                "confidence": 0.45,
                "review_state": "approved",
                "t_valid_from": _now_iso(),
                "t_valid_to": None,
            })

            report = lint_wiki(root, rules=[RULE_CONTRADICTION])
            self.assertEqual(report.by_rule.get(RULE_CONTRADICTION, 0), 1)
            finding = report.findings[0]
            self.assertEqual(sorted(finding.affected_claim_ids), ["c_a", "c_b"])

    def test_contradiction_skips_supersed_chain(self) -> None:
        """Closed (t_valid_to set) claims must NOT participate in contradiction."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_wiki(root)
            _write_claim(root, {
                "claim_id": "c_old",
                "statement": "Same statement key for contradiction trigger.",
                "support": [{"source_id": "src_a"}],
                "against": [],
                "confidence": 0.85,
                "review_state": "approved",
                "t_valid_from": _now_iso(),
                "t_valid_to": _now_iso(),  # closed
            })
            _write_claim(root, {
                "claim_id": "c_new",
                "statement": "Same statement key for contradiction trigger.",
                "support": [{"source_id": "src_b"}],
                "against": [],
                "confidence": 0.50,
                "review_state": "approved",
                "t_valid_from": _now_iso(),
                "t_valid_to": None,
            })
            report = lint_wiki(root, rules=[RULE_CONTRADICTION])
            self.assertEqual(report.by_rule.get(RULE_CONTRADICTION, 0), 0)

    def test_stale_fact_flags_past_t_valid_to(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_wiki(root)
            past = (datetime.now(UTC) - timedelta(days=10)).isoformat()
            _write_page(root, "vault/wiki/concepts/old-fact.md", {
                "page_type": "concept",
                "domain": "engineering",
                "t_valid_from": past,
                "t_valid_to": past,
                "superseded_by": None,
                "confidence": "medium",
                "review_state": "approved",
            }, body="Body content.")

            report = lint_wiki(root, rules=[RULE_STALE_FACT])
            self.assertEqual(report.by_rule.get(RULE_STALE_FACT, 0), 1)

    def test_stale_fact_skips_when_superseded_by_is_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_wiki(root)
            past = (datetime.now(UTC) - timedelta(days=10)).isoformat()
            _write_page(root, "vault/wiki/concepts/closed-fact.md", {
                "page_type": "concept",
                "domain": "engineering",
                "t_valid_to": past,
                "superseded_by": "vault/wiki/concepts/new-fact.md",
            })
            report = lint_wiki(root, rules=[RULE_STALE_FACT])
            self.assertEqual(report.by_rule.get(RULE_STALE_FACT, 0), 0)

    def test_orphan_page_flags_pages_with_no_inbound_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_wiki(root)
            _write_page(root, "vault/wiki/concepts/alpha.md", {"page_type": "concept"}, body="Body.")
            _write_page(root, "vault/wiki/concepts/beta.md", {"page_type": "concept"},
                        body="See [[alpha]] for context.")
            # alpha has inbound from beta → not orphan.  beta has no inbound → orphan.
            report = lint_wiki(root, rules=[RULE_ORPHAN_PAGE])
            slugs = [Path(f.affected_paths[0]).stem for f in report.findings]
            self.assertIn("beta", slugs)
            self.assertNotIn("alpha", slugs)

    def test_missing_concept_flags_unmatched_wiki_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_wiki(root)
            _write_page(root, "vault/wiki/concepts/anchor.md", {"page_type": "concept"},
                        body="References [[nonexistent-thing]] in the body.")
            report = lint_wiki(root, rules=[RULE_MISSING_CONCEPT])
            self.assertEqual(report.by_rule.get(RULE_MISSING_CONCEPT, 0), 1)
            self.assertEqual(report.findings[0].detail["missing_slug"], "nonexistent-thing")

    def test_broken_cross_ref_flags_claim_id_not_in_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_wiki(root)
            _write_claim(root, {
                "claim_id": "real_claim",
                "statement": "Real claim statement.",
                "support": [], "against": [], "confidence": 0.7,
                "review_state": "approved",
                "t_valid_from": _now_iso(), "t_valid_to": None,
            })
            _write_page(root, "vault/wiki/concepts/with-bad-ref.md", {
                "page_type": "concept",
                "claim_ids": ["real_claim", "ghost_claim_id"],
            }, body="Body.")
            report = lint_wiki(root, rules=[RULE_BROKEN_CROSS_REF])
            self.assertEqual(report.by_rule.get(RULE_BROKEN_CROSS_REF, 0), 1)
            self.assertEqual(report.findings[0].detail["missing_claim_ids"], ["ghost_claim_id"])

    def test_data_gap_flags_old_low_confidence_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_wiki(root)
            page = _write_page(root, "vault/wiki/concepts/stale-low.md", {
                "page_type": "concept",
                "confidence": "low",
            }, body="Body.")
            # Backdate the mtime to > 30 days ago.
            old_ts = time.time() - 40 * 86400
            import os
            os.utime(page, (old_ts, old_ts))

            report = lint_wiki(root, rules=[RULE_DATA_GAP], stale_after_days=30)
            self.assertEqual(report.by_rule.get(RULE_DATA_GAP, 0), 1)

    def test_persist_proposals_writes_one_lint_finding_per(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_wiki(root)
            # Seed: one broken cross-ref + one orphan page.
            _write_page(root, "vault/wiki/concepts/with-bad-ref.md", {
                "page_type": "concept",
                "claim_ids": ["ghost"],
            }, body="Solo page.")
            report = lint_wiki(root, persist_proposals=True)
            self.assertGreaterEqual(report.total, 2)
            self.assertEqual(len(report.proposal_ids), report.total)
            stored = ProposalStore(root).list(kind="lint_finding")
            self.assertEqual(len(stored), report.total)

    def test_all_rules_runs_all_six(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_wiki(root)
            report = lint_wiki(root)  # default = all rules
            # No pages, no claims → zero findings but the by_rule dict
            # should still be safe.
            self.assertEqual(report.total, 0)
            self.assertEqual(set(ALL_RULES), {
                "contradiction", "stale_fact", "orphan_page",
                "missing_concept", "broken_cross_ref", "data_gap",
            })


class SupersedeTests(unittest.TestCase):
    def test_supersede_closes_old_window_and_links_new(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_wiki(root)
            _write_claim(root, {
                "claim_id": "c_old",
                "statement": "Old fact.",
                "support": [], "against": [], "confidence": 0.7,
                "review_state": "approved",
                "t_valid_from": _now_iso(), "t_valid_to": None,
            })
            _write_claim(root, {
                "claim_id": "c_new",
                "statement": "Replacement fact.",
                "support": [], "against": [], "confidence": 0.85,
                "review_state": "approved",
                "t_valid_from": _now_iso(), "t_valid_to": None,
                "supersedes": [],
            })

            result = knowledge_plane.supersede_claim(
                root, new_claim_id="c_new", old_claim_id="c_old",
                reason="upstream paper retracted",
            )
            self.assertEqual(result["new_claim_id"], "c_new")
            self.assertEqual(result["old_claim_id"], "c_old")

            ledger = (root / ".omni" / "claims.jsonl").read_text(encoding="utf-8").splitlines()
            claims = {json.loads(line)["claim_id"]: json.loads(line) for line in ledger if line.strip()}
            self.assertIsNotNone(claims["c_old"]["t_valid_to"])
            self.assertEqual(claims["c_old"]["superseded_by"], "c_new")
            self.assertEqual(claims["c_old"]["review_state"], "superseded")
            self.assertIn("c_old", claims["c_new"]["supersedes"])

            log = (root / "vault" / "wiki" / "log.md").read_text(encoding="utf-8")
            self.assertIn("] supersede |", log)

    def test_supersede_rejects_same_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_wiki(root)
            with self.assertRaises(ValueError):
                knowledge_plane.supersede_claim(root, new_claim_id="x", old_claim_id="x")


class ConflictResolveTests(unittest.TestCase):
    def _seed_pair_and_finding(self, root: Path) -> str:
        knowledge_plane.init_layout(root)
        _write_claim(root, {
            "claim_id": "c_a",
            "statement": "Pair statement key.",
            "support": [{"source_id": "src_a"}],
            "against": [], "confidence": 0.85,
            "review_state": "approved",
            "t_valid_from": "2026-04-01T00:00:00+00:00",
            "t_valid_to": None,
        })
        _write_claim(root, {
            "claim_id": "c_b",
            "statement": "Pair statement key.",
            "support": [{"source_id": "src_b"}],
            "against": [], "confidence": 0.50,
            "review_state": "approved",
            "t_valid_from": "2026-05-01T00:00:00+00:00",
            "t_valid_to": None,
            "supersedes": [],
        })
        report = lint_wiki(root, rules=[RULE_CONTRADICTION], persist_proposals=True)
        self.assertEqual(len(report.proposal_ids), 1)
        return report.proposal_ids[0]

    def test_supersede_decision_closes_older_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = self._seed_pair_and_finding(root)
            knowledge_plane.resolve_conflict(
                root, proposal_id=pid, decision="supersede",
                reason="newer paper is canonical",
            )
            # c_b has newer t_valid_from → it's the "new" claim.
            ledger = (root / ".omni" / "claims.jsonl").read_text(encoding="utf-8").splitlines()
            claims = {json.loads(line)["claim_id"]: json.loads(line) for line in ledger if line.strip()}
            self.assertEqual(claims["c_a"]["superseded_by"], "c_b")
            self.assertIn("c_a", claims["c_b"]["supersedes"])

            proposal = ProposalStore(root).load(pid)
            self.assertEqual(proposal.state, "approved")
            self.assertIn("supersede", proposal.reason)

    def test_keep_both_marks_review_state_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = self._seed_pair_and_finding(root)
            knowledge_plane.resolve_conflict(root, proposal_id=pid, decision="keep_both")
            ledger = (root / ".omni" / "claims.jsonl").read_text(encoding="utf-8").splitlines()
            for line in ledger:
                if not line.strip():
                    continue
                record = json.loads(line)
                self.assertEqual(record["review_state"], "conflict")

    def test_reject_old_marks_only_older_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = self._seed_pair_and_finding(root)
            knowledge_plane.resolve_conflict(root, proposal_id=pid, decision="reject_old")
            ledger = (root / ".omni" / "claims.jsonl").read_text(encoding="utf-8").splitlines()
            claims = {json.loads(line)["claim_id"]: json.loads(line) for line in ledger if line.strip()}
            self.assertEqual(claims["c_a"]["review_state"], "rejected")
            self.assertEqual(claims["c_b"]["review_state"], "approved")

    def test_resolve_rejects_non_contradiction_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            _write_page(root, "vault/wiki/concepts/orphan-x.md", {"page_type": "concept"}, body="x")
            report = lint_wiki(root, rules=[RULE_ORPHAN_PAGE], persist_proposals=True)
            pid = report.proposal_ids[0]
            with self.assertRaises(ValueError):
                knowledge_plane.resolve_conflict(root, proposal_id=pid, decision="supersede")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
