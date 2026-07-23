"""v0.39 — E2E: forwarded paper → ingest → context-pack → task → report.

The 2026-05-28 review asked for:
    "跑一条真实 E2E：转发论文 → 入库 → 生成 context pack →
     产 PPT → 建复盘任务 → 周报汇总"

This test exercises the parts that run in main repo (stdlib only).
PPTX rendering depends on the agent-harness/integrations/pptx broker
(documented in v0.35) and is verified to return ``skipped=true``
gracefully when the broker is absent — that's the correct contract.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub import knowledge_plane
from omni_hub.app import TaskRouter
from omni_hub.builtins import build_default_registry
from omni_hub.channels.base import InboundMessage
from omni_hub.models import OperationSpec, RiskLevel
from omni_hub.proposals import APPROVED, ProposalStore
from omni_hub.runner import OperationRunner


class ForwardedPaperE2ETests(unittest.TestCase):
    def test_full_pipeline_runs_green(self) -> None:
        """One linear walk: classify forwarded URL → seed retrieval evidence
        → wiki-ingest → approve → apply → context-pack → task-add →
        pptx-build (broker-skip) → app-report-build."""

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            knowledge_plane.init_layout(workspace)
            registry = build_default_registry(workspace)
            runner = OperationRunner(registry)

            # ------------------------------------------------------
            # 1) Inbox: forwarded arxiv URL is classified as URL
            # ------------------------------------------------------
            forwarded = InboundMessage.new(
                channel="email", sender="me",
                body="check https://arxiv.org/abs/2510.04618 about ACE context engineering",
            )
            classify_result = runner.run(OperationSpec(
                name="inbox_classify", action="classify",
                payload={"body": forwarded.body, "subject": forwarded.subject,
                         "sender": forwarded.sender},
                risk_level=RiskLevel.READ_ONLY,
            )).to_dict()
            self.assertEqual(classify_result["status"], "succeeded")
            self.assertEqual(classify_result["output"]["category"], "url")

            # ------------------------------------------------------
            # 2) Seed a retrieval run + evidence (simulates the cascade
            #    output the inbox handler would have produced after
            #    capture_url + retrieve)
            # ------------------------------------------------------
            run_dir = workspace / ".omni" / "retrieval" / "r-e2e-1"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "run_manifest.json").write_text(json.dumps({
                "run_id": "r-e2e-1",
                "query": "ACE context engineering",
                "domain": "research",
                "created_at": datetime.now(UTC).isoformat(),
            }), encoding="utf-8")
            with (run_dir / "evidence.jsonl").open("w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "title": "ACE: Evolving Context as a Wiki",
                    "url": "https://arxiv.org/abs/2510.04618",
                    "snippet": "ACE evolves a persistent context document that the agent reads and edits across sessions.",
                    "canonical_id": "arxiv:2510.04618",
                    "source": "arxiv",
                    "score": 1.0,
                }) + "\n")

            # ------------------------------------------------------
            # 3) wiki-ingest → Proposal(wiki_update)
            # ------------------------------------------------------
            ingest = runner.run(OperationSpec(
                name="wiki_ingest", action="ingest",
                payload={"run_id": "r-e2e-1", "domain": "research",
                          "title": "", "max_records": 20},
                risk_level=RiskLevel.LOCAL_WRITE,
            ), approved=True).to_dict()
            self.assertEqual(ingest["status"], "succeeded")
            proposal_id = ingest["output"]["proposal_id"]
            self.assertTrue(proposal_id)

            # ------------------------------------------------------
            # 4) Approve + apply
            # ------------------------------------------------------
            ProposalStore(workspace).approve(proposal_id, reason="e2e auto-approve")
            apply_result = runner.run(OperationSpec(
                name="wiki_apply_proposal", action="apply",
                payload={"proposal": proposal_id},
                risk_level=RiskLevel.LOCAL_WRITE,
            ), approved=True).to_dict()
            self.assertEqual(apply_result["status"], "succeeded")

            # ------------------------------------------------------
            # 5) context-pack on the just-ingested topic
            # ------------------------------------------------------
            ctx = runner.run(OperationSpec(
                name="context_pack_build", action="build",
                payload={"query": "ACE context engineering",
                          "domain": "research",
                          "wiki_limit": 6, "research_limit": 6,
                          "persist": False, "tier": "standard",
                          "include_closed": False},
                risk_level=RiskLevel.READ_ONLY,
            )).to_dict()
            self.assertEqual(ctx["status"], "succeeded")

            # ------------------------------------------------------
            # 6) task-add: "复盘 ACE 论文" (review the paper)
            # ------------------------------------------------------
            task = runner.run(OperationSpec(
                name="task_add", action="add",
                payload={"user_id": "u_e2e", "title": "复盘 ACE 论文",
                          "category": "research", "priority": 2,
                          "estimated_minutes": 90,
                          "due_at": (datetime.now(UTC) + timedelta(days=2)).isoformat()},
                risk_level=RiskLevel.LOCAL_WRITE,
            ), approved=True).to_dict()
            self.assertEqual(task["status"], "succeeded")
            self.assertEqual(task["output"]["title"], "复盘 ACE 论文")

            # ------------------------------------------------------
            # 7) pptx-build: outline → broker (will skip without
            #    pptx-omni on PATH, that's the documented contract)
            # ------------------------------------------------------
            pptx_result = runner.run(OperationSpec(
                name="pptx_build", action="build",
                payload={
                    "outline": {
                        "title": "ACE — Context Evolution",
                        "slides": [
                            {"title": "Background",
                             "bullets": [{"text": "ACE evolves context"}]},
                            {"title": "Method",
                             "bullets": [{"text": "Wiki-style updates"}]},
                            {"title": "Implication",
                             "bullets": [{"text": "Persistent memory across sessions"}]},
                        ],
                    },
                    "output_path": "vault/decks/ace.pptx",
                },
                risk_level=RiskLevel.LOCAL_WRITE,
            ), approved=True).to_dict()
            self.assertEqual(pptx_result["status"], "succeeded")
            # Broker absent → returns skipped=true; with broker → would
            # contain `output_path`. Either is acceptable.
            self.assertIn(
                pptx_result["output"].get("skipped", False) or
                bool(pptx_result["output"].get("output_path", "")),
                {True},
            )

            # ------------------------------------------------------
            # 8) Cross-skill report rolls everything up
            # ------------------------------------------------------
            report = runner.run(OperationSpec(
                name="app_report_build", action="build",
                payload={"period": "weekly", "persist": False, "narrate": False},
                risk_level=RiskLevel.READ_ONLY,
            )).to_dict()
            self.assertEqual(report["status"], "succeeded")
            self.assertIn("ClaimLedger", report["output"]["markdown"])
            # The new claim should show up in by_domain.
            claims_stats = report["output"]["sections"][0]["stats"]
            self.assertGreaterEqual(claims_stats.get("added", 0), 1)

            # ------------------------------------------------------
            # 9) TaskRouter routes a follow-up review query to finance
            #    with schedule intent — same pattern as the review's
            #    failing case.
            # ------------------------------------------------------
            followup = InboundMessage.new(
                channel="cli", sender="me",
                body="明天上午提醒我复盘 BTC 和 NVDA 走势",
            )
            decision = TaskRouter().route(followup)
            self.assertEqual(decision.selected_skill_id, "finance")
            self.assertEqual(decision.primary_intent, "schedule")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
