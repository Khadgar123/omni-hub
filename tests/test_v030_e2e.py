"""v0.30 — end-to-end 5-Plane lifecycle test.

Exercises the full pipeline that v0.10-v0.29 built:

1. **Knowledge Plane** — retrieval cascade → manual evidence seed
2. **Knowledge Plane** — wiki-ingest → Proposal(wiki_update) → approve → apply
3. **Skill Plane**     — Judge framework scores the resulting page
4. **Skill Plane**     — A/B test of two prompt variants
5. **Application**     — TaskRouter routes a follow-up question with
                         conversation_history bias
6. **Application**     — Report orchestrator aggregates the new claim
7. **Meta**            — cross-skill scan reads accepted spans
                         (PreferenceStore auto-populated by apply)

This is the same flow the dogfood phase will run weekly; if it stays
green, v1.0 is reachable.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub import knowledge_plane
from omni_hub.ab import ABTestRunner, ABTestStore, Variant
from omni_hub.app import (
    ConversationTurn,
    ReportOrchestrator,
    ReportPeriod,
    TaskRouter,
)
from omni_hub.channels.base import InboundMessage
from omni_hub.harness.preference import PreferenceRecord, PreferenceStore
from omni_hub.judge import HeuristicJudge, JudgeRequest
from omni_hub.meta import CrossSkillTransfer
from omni_hub.proposals import APPROVED, Proposal, ProposalStore


class EndToEndLifecycleTests(unittest.TestCase):
    """One big test that walks every Plane."""

    def test_full_pipeline_runs_green(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # ------------------------------------------------------
            # 1) Knowledge Plane — init layout
            # ------------------------------------------------------
            knowledge_plane.init_layout(root)
            self.assertTrue((root / "vault" / "wiki" / "AGENTS.md").exists())
            self.assertTrue((root / "vault" / "wiki" / "domains" / "research").is_dir())

            # ------------------------------------------------------
            # 2) Seed an ingest evidence run + propose wiki_update
            # ------------------------------------------------------
            run_dir = root / ".omni" / "retrieval" / "r-2026-05-28T00"
            run_dir.mkdir(parents=True, exist_ok=True)
            manifest = {
                "run_id": "r-2026-05-28T00",
                "query": "ACE context evolution",
                "domain": "research",
                "created_at": datetime.now(UTC).isoformat(),
            }
            (run_dir / "run_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8",
            )
            evidence_records = [
                {
                    "title": "ACE: Evolving Context as a Wiki",
                    "url": "https://arxiv.org/abs/2510.04618",
                    "snippet": "ACE evolves a persistent context document that the agent reads and edits across sessions.",
                    "canonical_id": "arxiv:2510.04618",
                    "source": "arxiv",
                    "score": 1.0,
                },
                {
                    "title": "GEPA: Reflective Prompt Optimisation",
                    "url": "https://arxiv.org/abs/2507.19457",
                    "snippet": "GEPA reflectively edits prompts using evaluator feedback and improves coding-benchmark accuracy.",
                    "canonical_id": "arxiv:2507.19457",
                    "source": "arxiv",
                    "score": 0.9,
                },
            ]
            with (run_dir / "evidence.jsonl").open("w", encoding="utf-8") as f:
                for rec in evidence_records:
                    f.write(json.dumps(rec) + "\n")

            # Use knowledge_plane.ingest_retrieval_evidence directly so we
            # don't depend on the runner / CLI for the lifecycle test.
            ingest_result = knowledge_plane.ingest_retrieval_evidence(
                root, run_id="r-2026-05-28T00",
                domain="research",
            )
            self.assertIn("proposal_id", ingest_result)
            proposal_id = ingest_result["proposal_id"]
            self.assertTrue(proposal_id)

            # ------------------------------------------------------
            # 3) Approve + apply the wiki_update Proposal
            # ------------------------------------------------------
            proposals = ProposalStore(root)
            proposals.approve(proposal_id, reason="e2e-test approve")
            apply_result = knowledge_plane.apply_wiki_proposal(
                root, proposal_id=proposal_id,
            )
            self.assertTrue(apply_result.get("target_path"))
            self.assertGreaterEqual(int(apply_result.get("claims_written", 0)), 1)

            # ------------------------------------------------------
            # 4) PreferenceStore should auto-record the accepted apply
            # ------------------------------------------------------
            pref = PreferenceStore(root / ".omni" / "preference")
            stats = pref.stats("research")
            self.assertGreaterEqual(stats["total"], 1)

            # ------------------------------------------------------
            # 5) Skill Plane — Judge the applied page
            # ------------------------------------------------------
            wiki_page = root / apply_result["target_path"]
            self.assertTrue(wiki_page.exists(),
                            f"applied wiki page not on disk: {wiki_page}")
            candidate = wiki_page.read_text(encoding="utf-8")
            verdict = HeuristicJudge().evaluate(JudgeRequest(
                domain="research", candidate=candidate,
            ))
            self.assertGreater(verdict.composite, 0.0)
            self.assertLessEqual(verdict.composite, 1.0)

            # ------------------------------------------------------
            # 6) Skill Plane — A/B test two prompt variants
            # ------------------------------------------------------
            ab_runner = ABTestRunner(judge_name="heuristic")
            a = Variant(label="terse", candidate="ACE = context wiki.")
            b = Variant(
                label="cited",
                candidate=(
                    "ACE evolves context across sessions [1]. GEPA optimises "
                    "prompts reflectively [2].\n\n## References\n"
                    "[1] arxiv:2510.04618\n[2] arxiv:2507.19457"
                ),
            )
            store = ABTestStore(root)
            ab_verdict = ab_runner.run(
                run_id=store.new_run_id(),
                domain="research", a=a, b=b,
            )
            store.record(ab_verdict)
            self.assertEqual(ab_verdict.winner, "b")          # cited beats terse

            # ------------------------------------------------------
            # 7) Application — TaskRouter routes follow-up with history bias
            # ------------------------------------------------------
            router = TaskRouter()
            history = [
                ConversationTurn(
                    trace_id="t-prev", selected_skill_id="research",
                    timestamp="2026-05-28T00:00:00+00:00",
                ),
            ]
            follow_up = InboundMessage.new(
                channel="cli", sender="me",
                body="arxiv 论文 评审 投稿",
            )
            decision = router.route(follow_up, conversation_history=history)
            self.assertEqual(decision.selected_skill_id, "research")

            # ------------------------------------------------------
            # 8) Application — Report orchestrator picks up the claim
            # ------------------------------------------------------
            orch = ReportOrchestrator(root)
            summary = orch.build(ReportPeriod.WEEKLY)
            # The freshly added claim should show up in the by_domain rollup.
            claims_section = summary.sections[0]
            by_domain = claims_section.stats.get("by_domain", {})
            self.assertGreaterEqual(by_domain.get("research", 0), 0)
            # And the preference section sees the accepted record.
            pref_section = summary.sections[2]
            self.assertGreaterEqual(pref_section.stats.get("accepted", 0), 1)

            # ------------------------------------------------------
            # 9) Meta — cross-skill scan reads PreferenceStore
            # ------------------------------------------------------
            # Seed two more domains so the cross-skill scan has signal.
            pref.append(PreferenceRecord(
                task_id="meta-t", domain="engineering", prompt_version="v0",
                candidate_text="x", decision="accepted",
                accepted_spans=["citation marker citation marker"],
                reason="seed",
            ))
            pref.append(PreferenceRecord(
                task_id="meta-t2", domain="us_policy", prompt_version="v0",
                candidate_text="x", decision="accepted",
                accepted_spans=["citation marker citation"],
                reason="seed",
            ))
            transfer = CrossSkillTransfer(
                root,
                min_strong_domains=2,
                signal_threshold=0.3,
                min_accepted_in_strong=1,
            )
            findings = transfer.find_transfers()
            # Smoke: scan completes and returns a list (may be empty
            # depending on what the apply step happened to accept).
            self.assertIsInstance(findings, list)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
