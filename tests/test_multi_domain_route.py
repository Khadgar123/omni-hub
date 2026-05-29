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


class MultiDomainOrchestratorTests(unittest.TestCase):
    """WS2: orchestrate() fans out ONE shared cascade.retrieve per routed domain."""

    # both finance + research tokens -> a 2-domain plan (mirrors the route test)
    MULTI_QUERY = (
        "compare the 10-k earnings stock options report with the "
        "arxiv paper doi citation venue"
    )

    class _FakeResult:
        def __init__(self, domain, succeeded):
            self.sources_tried = ["a", "b", "c", "d"]
            self.sources_succeeded = succeeded
            self.records = [{"title": f"{domain} hit", "cite_id": "R1"}]

    class _FakeCascade:
        def __init__(self):
            self.calls = []

        def retrieve(self, query, *, domain, per_source_limit, total_limit, fusion):
            self.calls.append(domain)
            # finance well-covered; everything else under-sourced (1/4)
            succ = ["a", "b", "c"] if domain == "finance" else ["a"]
            return MultiDomainOrchestratorTests._FakeResult(domain, succ)

    def _run(self, query):
        from omni_hub.app.multi_domain import orchestrate
        fc = self._FakeCascade()
        bundle = orchestrate(".", query, cascade=fc)
        return bundle, fc

    def test_one_retrieval_per_domain_no_overlap(self) -> None:
        bundle, fc = self._run(self.MULTI_QUERY)
        self.assertGreaterEqual(len(fc.calls), 2)            # multi-domain
        self.assertEqual(len(fc.calls), len(set(fc.calls)))  # no dup calls
        self.assertEqual(len(bundle.domains), len(fc.calls))
        self.assertTrue(all(d.objective for d in bundle.domains))  # delegation contracts

    def test_coverage_warning_on_under_sourced_domain(self) -> None:
        bundle, _ = self._run(self.MULTI_QUERY)
        non_finance = [d for d in bundle.domains if d.domain != "finance"]
        self.assertTrue(non_finance)
        self.assertTrue(any(not d.coverage_ok for d in non_finance))
        self.assertTrue(bundle.coverage_warnings)

    def test_empty_query_rejected(self) -> None:
        from omni_hub.app.multi_domain import orchestrate
        with self.assertRaises(ValueError):
            orchestrate(".", "   ")

    def test_retrieval_failure_isolated(self) -> None:
        from omni_hub.app.multi_domain import orchestrate

        class Boom:
            def retrieve(self, *a, **k):
                raise RuntimeError("network down")

        bundle = orchestrate(".", self.MULTI_QUERY, cascade=Boom())
        self.assertTrue(all(d.error for d in bundle.domains))
        self.assertTrue(bundle.coverage_warnings)
        # a crashing worker still yields a structured (errored) row, no exception
        self.assertGreaterEqual(len(bundle.domains), 1)

    def test_app_orchestrate_op_end_to_end(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from omni_hub import builtins as ohb
        from omni_hub.models import OperationSpec, RiskLevel

        with tempfile.TemporaryDirectory() as d:
            op = ohb.make_app_orchestrate(Path(d))
            spec = OperationSpec(
                name="app_orchestrate", action="orchestrate",
                payload={"query": self.MULTI_QUERY},
                risk_level=RiskLevel.READ_ONLY,
            )
            with patch(
                "omni_hub.retrieval.Cascade",
                return_value=self._FakeCascade(),
            ):
                out = op(spec)
        self.assertIn("domains", out)
        self.assertGreaterEqual(out["domain_count"], 2)
        self.assertTrue(all(dd["objective"] for dd in out["domains"]))


if __name__ == "__main__":
    unittest.main()
