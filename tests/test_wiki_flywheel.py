"""Tests for v0.15 wiki / Argilla / domain-lint integration."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub import knowledge_plane
from omni_hub.domain_schemas import get_rule_override
from omni_hub.harness.argilla_bridge import _candidate_text, proposal_to_record
from omni_hub.harness.preference import PreferenceStore
from omni_hub.proposals import APPROVED, PENDING, Proposal, ProposalStore
from omni_hub.wiki_lint import (
    RULE_BROKEN_CROSS_REF,
    RULE_CONTRADICTION,
    RULE_DATA_GAP,
    lint_wiki,
)


# ---------------------------------------------------------------------------
# Argilla bridge: wiki_update / lint_finding candidate-text
# ---------------------------------------------------------------------------


class ArgillaCandidateTextTests(unittest.TestCase):
    def test_wiki_update_uses_body(self) -> None:
        proposal = Proposal(
            kind="wiki_update",
            state=PENDING,
            title="Synthesis: ACE",
            summary="Two claims compiled.",
            payload={
                "target_path": "vault/wiki/syntheses/ace.md",
                "domain": "ai_progress",
                "body": "---\npage_type: synthesis\n---\n\n# ACE body text.\n",
                "claims": [],
            },
        )
        text = _candidate_text(proposal)
        self.assertIn("ACE body text", text)

    def test_wiki_update_falls_back_to_claim_list(self) -> None:
        proposal = Proposal(
            kind="wiki_update",
            state=PENDING,
            title="Synthesis: ACE",
            summary="Two claims compiled.",
            payload={
                "target_path": "vault/wiki/syntheses/ace.md",
                "domain": "ai_progress",
                "claims": [
                    {"claim_id": "c1", "confidence": 0.5, "statement": "Claim A"},
                    {"claim_id": "c2", "confidence": 0.6, "statement": "Claim B"},
                ],
            },
        )
        text = _candidate_text(proposal)
        self.assertIn("`c1`", text)
        self.assertIn("Claim B", text)

    def test_lint_finding_renders_structured_text(self) -> None:
        proposal = Proposal(
            kind="lint_finding",
            state=PENDING,
            title="[contradiction] claims share key",
            summary="claims A ↔ B with divergent stance",
            payload={
                "rule": "contradiction",
                "severity": "high",
                "affected_paths": ["vault/wiki/concepts/x.md"],
                "affected_claim_ids": ["c_alpha", "c_beta"],
                "detail": {"statement_key": "key..."},
            },
        )
        text = _candidate_text(proposal)
        self.assertIn("Lint finding (contradiction", text)
        self.assertIn("severity=high", text)
        self.assertIn("c_alpha", text)
        self.assertIn("vault/wiki/concepts/x.md", text)

    def test_proposal_to_record_carries_wiki_body(self) -> None:
        proposal = Proposal(
            kind="wiki_update",
            state=PENDING,
            title="x", summary="y",
            payload={"body": "BODY-MARKER", "domain": "research"},
        )
        record = proposal_to_record(proposal, domain="research")
        self.assertIn("BODY-MARKER", record["fields"]["candidate_text"])
        self.assertEqual(record["metadata"]["kind"], "wiki_update")


# ---------------------------------------------------------------------------
# wiki-apply auto-feeds PreferenceStore (flywheel closure)
# ---------------------------------------------------------------------------


class WikiApplyFeedsPreferenceTests(unittest.TestCase):
    def test_apply_writes_accepted_preference_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)

            proposal = Proposal(
                kind="wiki_update",
                state=PENDING,
                title="Test synthesis",
                summary="A synthesis for flywheel test.",
                source_path=".omni/retrieval/r-flywheel",
                payload={
                    "target_path": "vault/wiki/syntheses/test-synthesis.md",
                    "domain": "engineering",
                    "body": "# Test synthesis\n\nFull body text used as accepted span.\n",
                    "claims": [
                        {
                            "claim_id": "c_flywheel",
                            "domain": "engineering",
                            "statement": "Flywheel closes the loop.",
                            "support": [],
                            "against": [],
                            "confidence": 0.7,
                            "review_state": "proposed",
                            "t_valid_from": datetime.now(UTC).isoformat(),
                            "t_valid_to": None,
                            "supersedes": [],
                            "superseded_by": None,
                        },
                    ],
                },
            )
            store = ProposalStore(root)
            store.store(proposal, write_card=False)
            store.approve(proposal.proposal_id, reason="flywheel test")

            applied = knowledge_plane.apply_wiki_proposal(root, proposal.proposal_id)
            self.assertTrue(applied["preference_path"].startswith(".omni/preference"))

            pref_store = PreferenceStore(root / ".omni" / "preference")
            records = list(pref_store.read("engineering"))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].decision, "accepted")
            self.assertIn("Full body text", records[0].accepted_spans[0])
            self.assertEqual(records[0].judge_summary["claims_written"], 1.0)


# ---------------------------------------------------------------------------
# Domain rule overrides
# ---------------------------------------------------------------------------


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


class DomainRuleOverrideTests(unittest.TestCase):
    def test_lookup_helper(self) -> None:
        self.assertEqual(get_rule_override("research", "broken_cross_ref"), "high")
        self.assertEqual(get_rule_override("fashion", "data_gap"), "skip")
        self.assertEqual(get_rule_override("research", "data_gap"), None)
        self.assertEqual(get_rule_override("unknown-domain", "broken_cross_ref"), None)

    def test_data_gap_skipped_in_fashion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)

            # Two pages, both confidence=low and old — research keeps, fashion skips.
            import os, time
            old_ts = time.time() - 800 * 86400  # well past everyone's threshold

            page_research = _write_page(root, "vault/wiki/domains/research/old.md", {
                "page_type": "domain_page",
                "domain": "research",
                "confidence": "low",
            }, body="Body.")
            page_fashion = _write_page(root, "vault/wiki/domains/fashion/old.md", {
                "page_type": "domain_page",
                "domain": "fashion",
                "confidence": "low",
            }, body="Body.")
            os.utime(page_research, (old_ts, old_ts))
            os.utime(page_fashion, (old_ts, old_ts))

            report = lint_wiki(root, rules=[RULE_DATA_GAP], stale_after_days=30)
            affected = {f.affected_paths[0] for f in report.findings if f.affected_paths}
            self.assertTrue(any("research/old.md" in p for p in affected),
                            f"research data_gap should fire; got {affected}")
            self.assertFalse(any("fashion/old.md" in p for p in affected),
                             f"fashion data_gap should be skipped; got {affected}")

    def test_broken_cross_ref_severity_elevated_in_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            _write_page(root, "vault/wiki/domains/research/bad-ref.md", {
                "page_type": "domain_page",
                "domain": "research",
                "claim_ids": ["nonexistent_claim"],
            }, body="Body.")
            report = lint_wiki(root, rules=[RULE_BROKEN_CROSS_REF])
            findings = [f for f in report.findings if "research/bad-ref" in f.affected_paths[0]]
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].severity, "high")
            self.assertEqual(findings[0].detail.get("domain_override"), "research")

    def test_contradiction_severity_demoted_in_international_relations(self) -> None:
        """contradiction default severity = high; international_relations
        overrides to low (multi-narrative is normal)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            now_iso = datetime.now(UTC).isoformat()
            _write_claim(root, {
                "claim_id": "ir_a",
                "domain": "international_relations",
                "statement": "Same statement key for IR contradiction.",
                "support": [{"source_id": "acled:1"}],
                "against": [], "confidence": 0.85,
                "review_state": "approved",
                "t_valid_from": now_iso, "t_valid_to": None,
            })
            _write_claim(root, {
                "claim_id": "ir_b",
                "domain": "international_relations",
                "statement": "Same statement key for IR contradiction.",
                "support": [{"source_id": "gdelt:2"}],
                "against": [], "confidence": 0.55,
                "review_state": "approved",
                "t_valid_from": now_iso, "t_valid_to": None,
            })
            report = lint_wiki(root, rules=[RULE_CONTRADICTION])
            self.assertEqual(len(report.findings), 1)
            self.assertEqual(report.findings[0].severity, "low")
            self.assertEqual(
                report.findings[0].detail.get("domain_override"),
                "international_relations",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
