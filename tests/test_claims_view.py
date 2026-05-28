"""Tests for claims-list / claims-show / claims-stats."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub import knowledge_plane


def _write_claim(root: Path, claim: dict) -> None:
    ledger = root / ".omni" / "claims.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(claim, ensure_ascii=False, sort_keys=True) + "\n")


def _seed_diverse_claims(root: Path) -> None:
    knowledge_plane.init_layout(root)
    _write_claim(root, {
        "claim_id": "c_open_research",
        "domain": "research", "review_state": "approved",
        "statement": "Open research claim.",
        "support": [], "against": [], "confidence": 0.8,
        "t_valid_from": "2026-04-01T00:00:00+00:00",
        "t_valid_to": None, "supersedes": [], "superseded_by": None,
    })
    _write_claim(root, {
        "claim_id": "c_open_finance",
        "domain": "finance", "review_state": "approved",
        "statement": "Open finance claim.",
        "support": [], "against": [], "confidence": 0.7,
        "t_valid_from": "2026-04-15T00:00:00+00:00",
        "t_valid_to": None, "supersedes": [], "superseded_by": None,
    })
    _write_claim(root, {
        "claim_id": "c_closed_old",
        "domain": "research", "review_state": "superseded",
        "statement": "Older closed claim.",
        "support": [], "against": [], "confidence": 0.75,
        "t_valid_from": "2026-01-01T00:00:00+00:00",
        "t_valid_to": "2026-05-01T00:00:00+00:00",
        "supersedes": [], "superseded_by": "c_replacement",
    })
    _write_claim(root, {
        "claim_id": "c_replacement",
        "domain": "research", "review_state": "approved",
        "statement": "Newer replacement claim.",
        "support": [], "against": [], "confidence": 0.85,
        "t_valid_from": "2026-05-01T00:00:00+00:00",
        "t_valid_to": None,
        "supersedes": ["c_closed_old"], "superseded_by": None,
    })
    _write_claim(root, {
        "claim_id": "c_rejected",
        "domain": "us_policy", "review_state": "rejected",
        "statement": "Rejected claim.",
        "support": [], "against": [], "confidence": 0.3,
        "t_valid_from": "2026-04-20T00:00:00+00:00",
        "t_valid_to": None, "supersedes": [], "superseded_by": None,
    })


class ListClaimsTests(unittest.TestCase):
    def test_default_filters_closed_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_diverse_claims(root)
            claims = knowledge_plane.list_claims(root)
            ids = {c["claim_id"] for c in claims}
            self.assertIn("c_open_research", ids)
            self.assertIn("c_open_finance", ids)
            self.assertIn("c_replacement", ids)
            self.assertNotIn("c_closed_old", ids)  # has t_valid_to set
            self.assertNotIn("c_rejected", ids)    # state=rejected

    def test_include_closed_surfaces_everything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_diverse_claims(root)
            claims = knowledge_plane.list_claims(root, include_closed=True)
            self.assertEqual(len(claims), 5)

    def test_filter_by_domain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_diverse_claims(root)
            claims = knowledge_plane.list_claims(root, domain="research")
            ids = {c["claim_id"] for c in claims}
            self.assertEqual(ids, {"c_open_research", "c_replacement"})

    def test_filter_by_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_diverse_claims(root)
            claims = knowledge_plane.list_claims(root, state="rejected", include_closed=True)
            self.assertEqual([c["claim_id"] for c in claims], ["c_rejected"])


class ShowClaimTests(unittest.TestCase):
    def test_show_walks_supersession_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_diverse_claims(root)
            result = knowledge_plane.show_claim(root, claim_id="c_replacement")
            self.assertEqual(result["claim"]["claim_id"], "c_replacement")
            chain_ids = [c["claim_id"] for c in result["supersedes_chain"]]
            self.assertIn("c_closed_old", chain_ids)
            self.assertEqual(result["superseded_chain"], [])

    def test_show_walks_forward_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_diverse_claims(root)
            result = knowledge_plane.show_claim(root, claim_id="c_closed_old")
            self.assertEqual(result["claim"]["claim_id"], "c_closed_old")
            forward_ids = [c["claim_id"] for c in result["superseded_chain"]]
            self.assertEqual(forward_ids, ["c_replacement"])

    def test_show_raises_on_unknown_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_diverse_claims(root)
            with self.assertRaises(KeyError):
                knowledge_plane.show_claim(root, claim_id="does_not_exist")


class ClaimsStatsTests(unittest.TestCase):
    def test_stats_aggregate_open_closed_and_buckets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_diverse_claims(root)
            stats = knowledge_plane.claims_stats(root)
            self.assertEqual(stats["total"], 5)
            self.assertEqual(stats["closed"], 2)  # c_closed_old + c_rejected
            self.assertEqual(stats["open"], 3)
            self.assertEqual(stats["by_state"]["approved"], 3)
            self.assertEqual(stats["by_state"]["rejected"], 1)
            self.assertEqual(stats["by_state"]["superseded"], 1)
            self.assertEqual(stats["by_domain"]["research"], 3)
            self.assertEqual(stats["by_domain"]["finance"], 1)
            self.assertEqual(stats["by_domain"]["us_policy"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
