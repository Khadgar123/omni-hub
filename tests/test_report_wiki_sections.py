"""Tests for v0.16-C: daily/weekly/monthly reports include wiki + lint sections."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub import knowledge_plane
from omni_hub.proposals import PENDING, Proposal, ProposalStore
from omni_hub.reports import build_daily, build_monthly, build_weekly


def _seed_wiki_with_claim(root: Path) -> None:
    knowledge_plane.init_layout(root)
    ledger = root / ".omni" / "claims.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({
        "claim_id": "c1", "domain": "research", "review_state": "approved",
        "statement": "x", "support": [], "against": [], "confidence": 0.8,
        "t_valid_from": "2026-05-28T00:00:00+00:00", "t_valid_to": None,
        "supersedes": [], "superseded_by": None,
    }) + "\n", encoding="utf-8")
    # Plus one closed claim to exercise open/closed split.
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "claim_id": "c2", "domain": "research", "review_state": "superseded",
            "statement": "y", "support": [], "against": [], "confidence": 0.7,
            "t_valid_from": "2026-04-01T00:00:00+00:00",
            "t_valid_to": "2026-05-01T00:00:00+00:00",
            "supersedes": [], "superseded_by": "c1",
        }) + "\n")


def _seed_pending_lint_findings(root: Path) -> None:
    store = ProposalStore(root)
    for rule, severity in [
        ("contradiction", "high"),
        ("orphan_page", "low"),
        ("orphan_page", "low"),
    ]:
        proposal = Proposal(
            kind="lint_finding", state=PENDING,
            title=f"[{rule}] sample",
            summary="seed for report test",
            payload={"rule": rule, "severity": severity,
                     "affected_paths": [], "affected_claim_ids": []},
        )
        store.store(proposal, write_card=False)


class ReportWikiSectionTests(unittest.TestCase):
    def test_daily_includes_wiki_health_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_wiki_with_claim(root)
            body, _ = build_daily(anchor=date(2026, 5, 28), workspace=root)
            self.assertIn("## Wiki health", body)
            self.assertIn("claims: total **2**", body)
            self.assertIn("open **1**", body)
            self.assertIn("closed **1**", body)

    def test_daily_lint_pipeline_section_aggregates_by_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_wiki_with_claim(root)
            _seed_pending_lint_findings(root)
            body, _ = build_daily(anchor=date(2026, 5, 28), workspace=root)
            self.assertIn("## Lint pipeline", body)
            self.assertIn("total pending: **3**", body)
            self.assertIn("`contradiction`=1", body)
            self.assertIn("`orphan_page`=2", body)

    def test_weekly_and_monthly_also_include_wiki_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_wiki_with_claim(root)
            for builder in (build_weekly, build_monthly):
                body, _ = builder(anchor=date(2026, 5, 28), workspace=root)
                self.assertIn("## Wiki health", body)
                self.assertIn("## Lint pipeline", body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
