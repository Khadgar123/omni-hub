"""Quant→ClaimLedger bridge (v0.49 quant integration).

The sanctioned path from a quant backtest finding to the parent ledger:
finding dict -> candidate claims (conclusion/backtest/risk) -> Proposal[T].
Raw OHLCV is never ingested.  Network-free.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.quant_assets import propose_quant_finding, quant_finding_to_claims

_FINDING = {
    "symbol": "BTCUSDT", "timeframe": "1d", "venue": "binance",
    "strategy": "EMA(20/50) trend-follow", "regime": "trend",
    "hypothesis": "EMA cross captures BTC weekly trend with positive expectancy",
    "conclusion": "2024-2026 backtest: edge concentrated in trending regimes, flat in chop",
    "backtest": {"sharpe": 1.4, "max_drawdown": 0.22, "win_rate": 0.48,
                 "period": "2024-01..2026-05", "trades": 130},
    "risk": "Edge collapses in chop; 22% drawdown; crypto 24/7 gap risk",
}


class FindingToClaimsTests(unittest.TestCase):
    def test_three_families(self) -> None:
        claims = quant_finding_to_claims(_FINDING, source_id="quant:btc", domain="finance")
        kinds = {c["support"][0]["claim_kind"] for c in claims}
        self.assertEqual(kinds, {"strategy_conclusion", "backtest_result", "risk_disclosure"})
        for c in claims:
            self.assertEqual(c["domain"], "finance")
            self.assertEqual(c["review_state"], "proposed")
            self.assertEqual(c["support"][0]["symbol"], "BTCUSDT")
        bt = next(c for c in claims if c["support"][0]["claim_kind"] == "backtest_result")
        self.assertIn("Sharpe 1.4", bt["statement"])
        self.assertIn("[BTCUSDT 1d]", bt["statement"])

    def test_deterministic_ids(self) -> None:
        a = quant_finding_to_claims(_FINDING, source_id="quant:btc")
        b = quant_finding_to_claims(_FINDING, source_id="quant:btc")
        self.assertEqual([c["claim_id"] for c in a], [c["claim_id"] for c in b])

    def test_empty_or_bad_finding(self) -> None:
        self.assertEqual(quant_finding_to_claims({}), [])
        self.assertEqual(quant_finding_to_claims("nope"), [])


class ProposeTests(unittest.TestCase):
    def test_emits_wiki_update_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            res = propose_quant_finding(tmp, finding=_FINDING)
            self.assertGreaterEqual(res["claim_count"], 3)
            self.assertTrue(res["proposal_id"])
            self.assertEqual(res["domain"], "finance")

    def test_builtin_via_runner(self) -> None:
        from omni_hub.builtins import build_default_registry
        from omni_hub.models import OperationSpec, RiskLevel
        from omni_hub.runner import OperationRunner
        with tempfile.TemporaryDirectory() as tmp:
            runner = OperationRunner(build_default_registry(Path(tmp)))
            out = runner.run(
                OperationSpec(
                    name="quant_finding_propose", action="propose",
                    payload={"finding": _FINDING},
                    risk_level=RiskLevel.parse("L1"),
                ),
                approved=True,
            ).to_dict()
            self.assertEqual(out["status"], "succeeded")
            self.assertTrue(out["output"]["proposal_id"])

    def test_no_claims_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                propose_quant_finding(tmp, finding={"symbol": "X"})  # no conclusion/bt/risk


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
