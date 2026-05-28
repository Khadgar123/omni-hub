"""v0.24-v0.29 P1 tests:
- v0.24 agent-harness/integrations/feishu + discord scaffolding (manifest)
- v0.26 ReportOrchestrator narrative mode (NarrativeRequest → TaskPacket)
- v0.28 CrossSkillTransfer scan + signals + findings
- v0.29 A/B test runner + store + win_rate aggregation
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.ab import ABTestRunner, ABTestStore, ABTestVerdict, Variant
from omni_hub.app import (
    NarrativeRequest,
    ReportOrchestrator,
    ReportPeriod,
)
from omni_hub.meta import CrossSkillFinding, CrossSkillTransfer, PatternSignal


# ---------------------------------------------------------------------------
# v0.24 — Feishu / Discord scaffolding
# ---------------------------------------------------------------------------


class IntegrationsManifestTests(unittest.TestCase):
    def test_manifest_registers_feishu_and_discord_pending(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "agent-harness" / "manifest.json").read_text(encoding="utf-8"),
        )
        pending_ids = {p["id"] for p in manifest.get("pending_forks", [])}
        self.assertIn("feishu-oapi", pending_ids)
        self.assertIn("discord-py", pending_ids)

    def test_integration_readmes_exist(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        for name in ("feishu", "discord"):
            readme = (
                repo_root / "agent-harness" / "integrations" / name / "README.md"
            )
            self.assertTrue(readme.exists(), f"{name} README missing")
            self.assertIn("Broker contract", readme.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# v0.26 — ReportOrchestrator narrative mode
# ---------------------------------------------------------------------------


class NarrativeRequestTests(unittest.TestCase):
    def test_to_packet_carries_period_and_context(self) -> None:
        req = NarrativeRequest(
            period="weekly",
            markdown_summary="# Report\n\nSome stats",
            target_audience="self",
            additional_notes="focus on AI claims",
        )
        packet = req.to_packet()
        self.assertEqual(packet["task_type"], "report_narrate")
        self.assertEqual(packet["domain_profile"], "meta")
        self.assertEqual(packet["context"]["period"], "weekly")
        self.assertIn("Some stats", packet["context"]["report_markdown"])

    def test_build_with_narrative_returns_summary_and_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orch = ReportOrchestrator(tmp)
            summary, narrative = orch.build_with_narrative(
                ReportPeriod.WEEKLY,
                target_audience="oncall",
                additional_notes="cite stats",
                trace_id="t-1",
            )
            self.assertEqual(summary.period, "weekly")
            self.assertEqual(narrative.period, "weekly")
            self.assertEqual(narrative.target_audience, "oncall")
            self.assertEqual(narrative.trace_id, "t-1")
            self.assertIn("omni-hub", narrative.markdown_summary)


# ---------------------------------------------------------------------------
# v0.28 — CrossSkillTransfer
# ---------------------------------------------------------------------------


def _seed_preference(
    root: Path,
    domain: str,
    accepted_spans_per_record: list[list[str]],
    rejected_spans_per_record: list[list[str]] | None = None,
) -> None:
    """Write a domain's preference jsonl manually for tests."""

    target = root / ".omni" / "preference"
    target.mkdir(parents=True, exist_ok=True)
    file = target / f"{domain}.jsonl"
    rejected_spans_per_record = rejected_spans_per_record or [
        [] for _ in accepted_spans_per_record
    ]
    with file.open("a", encoding="utf-8") as f:
        for accepted, rejected in zip(
            accepted_spans_per_record, rejected_spans_per_record, strict=False,
        ):
            f.write(json.dumps({
                "task_id": f"{domain}-x",
                "domain": domain,
                "prompt_version": "v0",
                "candidate_text": "irrelevant",
                "decision": "accepted",
                "accepted_spans": accepted,
                "rejected_spans": rejected,
                "reason": "test seed",
                "created_at": "2026-05-28T00:00:00+00:00",
            }) + "\n")


class CrossSkillTransferTests(unittest.TestCase):
    def test_pattern_signal_normalises_correctly(self) -> None:
        sig = PatternSignal(domain="d", token="t",
                            accepted_count=4, rejected_count=1)
        # (4-1) / (4+1) = 0.6
        self.assertAlmostEqual(sig.signal, 0.6, places=2)

    def test_scan_returns_per_domain_counters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_preference(root, "research", [["citation density"]])
            _seed_preference(root, "engineering", [["citation density"]])
            transfer = CrossSkillTransfer(root)
            scan = transfer.scan()
            self.assertIn("research", scan)
            self.assertIn("engineering", scan)
            self.assertEqual(
                scan["research"]["accepted"]["citation"], 1,
            )

    def test_find_transfers_detects_cross_skill_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Token "citation" is strong in 3 domains
            for domain in ("research", "engineering", "us_policy"):
                _seed_preference(root, domain, [
                    ["citation backed", "citation backed"],
                    ["citation backed"],
                ])
            # Token "citation" is absent in a 4th domain
            _seed_preference(root, "cooking", [["recipe technique"]])
            transfer = CrossSkillTransfer(
                root, min_strong_domains=3,
                signal_threshold=0.3, min_accepted_in_strong=1,
            )
            findings = transfer.find_transfers()
            tokens = {f.token for f in findings}
            self.assertIn("citation", tokens)
            cit = next(f for f in findings if f.token == "citation")
            self.assertGreaterEqual(len(cit.strong_domains), 3)
            self.assertIn("cooking", cit.weak_domains)
            self.assertIn("citation", cit.suggested_update)

    def test_find_transfers_respects_token_blocklist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # "the" is in the default blocklist
            for domain in ("research", "engineering", "us_policy"):
                _seed_preference(root, domain, [
                    ["the citation marker", "the citation marker"],
                ])
            _seed_preference(root, "cooking", [["recipe technique"]])
            transfer = CrossSkillTransfer(
                root, min_strong_domains=3,
                signal_threshold=0.3, min_accepted_in_strong=1,
            )
            tokens = {f.token for f in transfer.find_transfers()}
            self.assertNotIn("the", tokens)


# ---------------------------------------------------------------------------
# v0.29 — A/B test runner + store
# ---------------------------------------------------------------------------


class ABTestRunnerTests(unittest.TestCase):
    def test_heuristic_judge_runner_emits_verdict(self) -> None:
        runner = ABTestRunner(judge_name="heuristic")
        a = Variant(label="baseline", candidate="ACE evolves context [1].")
        b = Variant(
            label="improved",
            candidate=(
                "ACE evolves context [1].\n\nMem0 OS adds bitemporal "
                "validity [2].\n\n## References\n[1] arxiv:2510.04618\n"
                "[2] arxiv:2507.19457"
            ),
        )
        verdict = runner.run(
            run_id="ab_test1", domain="research", a=a, b=b, reference="",
        )
        self.assertIn(verdict.winner, {"a", "b", "tie"})
        # B has more citations + structure, should win.
        self.assertEqual(verdict.winner, "b")
        self.assertIn(verdict.confidence_label,
                      {"tie", "marginal", "moderate", "decisive"})

    def test_classify_thresholds(self) -> None:
        self.assertEqual(ABTestRunner._classify(0.005), ("tie", "tie"))
        self.assertEqual(ABTestRunner._classify(0.05), ("b", "marginal"))
        self.assertEqual(ABTestRunner._classify(-0.15), ("a", "moderate"))
        self.assertEqual(ABTestRunner._classify(0.30), ("b", "decisive"))


class ABTestStoreTests(unittest.TestCase):
    def _build_verdict(
        self, *, run_id: str, domain: str, winner: str, delta: float,
    ) -> ABTestVerdict:
        a = Variant(label="A", candidate="x")
        b = Variant(label="B", candidate="y")
        return ABTestVerdict(
            run_id=run_id, domain=domain, judge_name="heuristic",
            a=a, b=b,
            verdict_a={"composite": 0.5}, verdict_b={"composite": 0.5 + delta},
            winner=winner, delta=delta,
            confidence_label="moderate", rationale="test",
        )

    def test_record_and_get_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ABTestStore(tmp)
            v = self._build_verdict(run_id="r1", domain="research", winner="b", delta=0.1)
            store.record(v)
            fetched = store.get("r1")
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.winner, "b")
            self.assertAlmostEqual(fetched.delta, 0.1, places=3)

    def test_list_filters_by_domain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ABTestStore(tmp)
            store.record(self._build_verdict(run_id="r1", domain="research",
                                              winner="b", delta=0.1))
            store.record(self._build_verdict(run_id="r2", domain="finance",
                                              winner="a", delta=-0.2))
            research = store.list(domain="research", limit=10)
            self.assertEqual(len(research), 1)
            self.assertEqual(research[0]["domain"], "research")

    def test_win_rate_aggregates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ABTestStore(tmp)
            for i in range(3):
                store.record(self._build_verdict(
                    run_id=f"r{i}-a", domain="research", winner="a", delta=-0.2,
                ))
            for i in range(2):
                store.record(self._build_verdict(
                    run_id=f"r{i}-b", domain="research", winner="b", delta=0.2,
                ))
            store.record(self._build_verdict(
                run_id="r-tie", domain="research", winner="tie", delta=0.01,
            ))
            stats = store.win_rate(domain="research")
            self.assertEqual(stats["total"], 6)
            self.assertEqual(stats["tally"]["a"], 3)
            self.assertEqual(stats["tally"]["b"], 2)
            self.assertEqual(stats["tally"]["tie"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
