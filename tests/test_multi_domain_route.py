"""v0.46 cross-domain routing — the 'plan' half of plan-and-execute for a
task that spans several knowledge domains (e.g. research + finance).

route_multi() is deterministic + LLM-free (reuses route()'s heuristic
scores); the execute+synthesize half stays gated, so this is unit-testable
without a model.
"""

import unittest

from omni_hub.app.task_router import MultiRoutingDecision, TaskRouter
from omni_hub.channels.base import InboundMessage


def _ib(body: str) -> InboundMessage:
    return InboundMessage.new(channel="cli", sender="u", body=body)


class MultiDomainRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.r = TaskRouter()

    def test_single_domain_is_not_multi(self) -> None:
        body = "how do I read a 10-k earnings report for this stock"
        d = self.r.route_multi(_ib(body))
        self.assertIsInstance(d, MultiRoutingDecision)
        self.assertFalse(d.is_multi_domain)
        self.assertEqual(len(d.domains), 1)
        self.assertEqual(d.primary_skill_id, self.r.route(_ib(body)).selected_skill_id)
        self.assertTrue(all(dr.recommended_operation for dr in d.domains))

    def test_two_domains_detected(self) -> None:
        # 4 finance tokens (10-k, earnings, stock, options) + 5 research tokens
        # (arxiv, paper, doi, citation, venue) → both well within min_ratio.
        body = (
            "compare the 10-k earnings stock options report with the "
            "arxiv paper doi citation venue"
        )
        d = self.r.route_multi(_ib(body))
        self.assertTrue(d.is_multi_domain)
        ids = {dr.skill_id for dr in d.domains}
        self.assertIn("finance", ids)
        self.assertIn("research", ids)
        # primary is first, ordered by score
        self.assertEqual(d.domains[0].skill_id, d.primary_skill_id)
        # confidences are non-increasing
        confs = [dr.confidence for dr in d.domains]
        self.assertEqual(confs, sorted(confs, reverse=True))

    def test_no_match_falls_back_to_single_default(self) -> None:
        d = self.r.route_multi(_ib("zzz qqq wibble"))
        self.assertFalse(d.is_multi_domain)
        self.assertEqual(len(d.domains), 1)
        self.assertEqual(d.primary_skill_id, self.r.default_skill_id)


class AppRouteMultiOpTests(unittest.TestCase):
    """The CLI op (app_route_multi) end-to-end through the builtin."""

    def test_op_returns_multi_domain_plan(self) -> None:
        import tempfile
        from pathlib import Path

        from omni_hub import builtins as ohb
        from omni_hub.models import OperationSpec, RiskLevel

        with tempfile.TemporaryDirectory() as d:
            op = ohb.make_app_route_multi(Path(d))
            spec = OperationSpec(
                name="app_route_multi", action="route",
                payload={
                    "query": "compare the 10-k earnings stock options report "
                             "with the arxiv paper doi citation venue",
                },
                risk_level=RiskLevel.READ_ONLY,
            )
            out = op(spec)
        self.assertTrue(out["decision"]["is_multi_domain"])
        ids = {dom["skill_id"] for dom in out["decision"]["domains"]}
        self.assertIn("finance", ids)
        self.assertIn("research", ids)


if __name__ == "__main__":
    unittest.main()
