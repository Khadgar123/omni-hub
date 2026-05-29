"""v0.20-v0.23 P1 tests:
- v0.20  Bilibili source + Zhihu/Weibo broker stubs
- v0.21  CN policy RSSHub connectors (gov_cn / stats_gov_cn / court_gov_cn / pbc_gov_cn)
- v0.22  Tushare + Crunchbase + LinkedIn broker stubs
- v0.23  Judge LLM framework (HeuristicJudge real, LLMJudge fallback)
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.judge import (
    DimensionScore,
    HeuristicJudge,
    JudgeRequest,
    JudgeVerdict,
    Judges,
    LLMJudge,
    composite_score,
)
from omni_hub.retrieval import DEFAULT_DOMAIN_CASCADES, builtin_sources
from omni_hub.retrieval.bilibili import BilibiliSource
from omni_hub.retrieval.business_intel import CrunchbaseSource, LinkedInBrokerSource
from omni_hub.retrieval.cn_finance import TushareSource
from omni_hub.retrieval.cn_policy import (
    CourtGovCnSource,
    GovCnSource,
    PBCGovCnSource,
    StatsGovCnSource,
)
from omni_hub.retrieval.zhihu_weibo import WeiboSource, ZhihuSource


# ---------------------------------------------------------------------------
# v0.20 — Bilibili / Zhihu / Weibo
# ---------------------------------------------------------------------------


class BilibiliSourceTests(unittest.TestCase):
    def test_name_and_tier(self) -> None:
        src = BilibiliSource()
        self.assertEqual(src.name, "bilibili")
        self.assertEqual(src.tier, 0)

    def test_empty_query_returns_empty_list(self) -> None:
        self.assertEqual(BilibiliSource().retrieve("", limit=5), [])

    def test_registered_in_builtin_sources_tier0(self) -> None:
        sources = builtin_sources()
        self.assertIn("bilibili", sources)
        self.assertEqual(sources["bilibili"].name, "bilibili")


class BrokerStubsTests(unittest.TestCase):
    def test_zhihu_off_when_binary_missing(self) -> None:
        src = ZhihuSource()
        status, msg = src.check()
        # `zhihu` CLI almost certainly is not on PATH in the test env.
        self.assertIn(status, {"off", "warn"})
        # Empty list when broker absent.
        self.assertEqual(src.retrieve("test", limit=5), [])

    def test_weibo_off_when_binary_missing(self) -> None:
        src = WeiboSource()
        status, _ = src.check()
        self.assertIn(status, {"off", "warn"})
        self.assertEqual(src.retrieve("test", limit=5), [])


class V020CascadeTests(unittest.TestCase):
    def test_fitness_wellness_cascade_includes_bilibili(self) -> None:
        self.assertIn("bilibili", DEFAULT_DOMAIN_CASCADES["fitness_wellness"])

    def test_cooking_cascade_includes_xhs_and_bilibili(self) -> None:
        casc = DEFAULT_DOMAIN_CASCADES["cooking"]
        self.assertIn("xiaohongshu", casc)
        self.assertIn("bilibili", casc)

    def test_travel_cascade_includes_xhs_and_bilibili(self) -> None:
        casc = DEFAULT_DOMAIN_CASCADES["travel"]
        self.assertIn("xiaohongshu", casc)
        self.assertIn("bilibili", casc)

    def test_marketing_includes_zhihu_and_weibo(self) -> None:
        casc = DEFAULT_DOMAIN_CASCADES["marketing"]
        self.assertIn("zhihu", casc)
        self.assertIn("weibo", casc)


# ---------------------------------------------------------------------------
# v0.21 — CN policy RSSHub connectors
# ---------------------------------------------------------------------------


class CNPolicySourceTests(unittest.TestCase):
    def test_four_sources_have_correct_names(self) -> None:
        self.assertEqual(GovCnSource().name, "gov_cn")
        self.assertEqual(StatsGovCnSource().name, "stats_gov_cn")
        self.assertEqual(CourtGovCnSource().name, "court_gov_cn")
        self.assertEqual(PBCGovCnSource().name, "pbc_gov_cn")

    def test_check_when_no_base_set(self) -> None:
        original = os.environ.pop("OMNI_RSSHUB_BASE", None)
        try:
            src = GovCnSource(base_url="")
            status, _ = src.check()
            self.assertEqual(status, "off")
        finally:
            if original is not None:
                os.environ["OMNI_RSSHUB_BASE"] = original

    def test_check_when_base_set(self) -> None:
        src = GovCnSource(base_url="http://localhost:1200")
        status, msg = src.check()
        self.assertEqual(status, "ok")
        self.assertIn("/gov/zhengce/zuixin", msg)

    def test_empty_query_returns_empty(self) -> None:
        src = GovCnSource(base_url="http://localhost:1200")
        self.assertEqual(src.retrieve("", limit=5), [])

    def test_cn_policy_cascade_includes_all_four_official_sources(self) -> None:
        casc = DEFAULT_DOMAIN_CASCADES["cn_policy"]
        for name in ("gov_cn", "stats_gov_cn", "court_gov_cn", "pbc_gov_cn"):
            self.assertIn(name, casc)

    def test_cn_policy_sources_registered_in_builtin(self) -> None:
        sources = builtin_sources()
        for name in ("gov_cn", "stats_gov_cn", "court_gov_cn", "pbc_gov_cn"):
            self.assertIn(name, sources)


# ---------------------------------------------------------------------------
# v0.22 — Tushare + Crunchbase + LinkedIn
# ---------------------------------------------------------------------------


class TushareSourceTests(unittest.TestCase):
    def test_off_when_no_token(self) -> None:
        original = os.environ.pop("TUSHARE_TOKEN", None)
        try:
            src = TushareSource()
            status, _ = src.check()
            self.assertEqual(status, "off")
            self.assertEqual(src.retrieve("600519.SH", limit=5), [])
        finally:
            if original is not None:
                os.environ["TUSHARE_TOKEN"] = original

    def test_ok_when_token_present(self) -> None:
        src = TushareSource(token="fake-token")
        status, msg = src.check()
        self.assertEqual(status, "ok")

    def test_skips_non_ticker_query(self) -> None:
        # The free-text "what's NVDA worth" — falls through to empty
        # when no real Tushare call is mocked.
        src = TushareSource(token="fake-token")
        with mock.patch.object(src, "_post", return_value={"data": {"items": [], "fields": []}}):
            records = src.retrieve("how is NVDA stock doing", limit=5)
        # Latin ticker NVDA matches the pattern, so it does call _post — but
        # with empty items we expect [].
        self.assertEqual(records, [])

    def test_returns_record_for_a_share_ticker(self) -> None:
        src = TushareSource(token="fake-token")
        fake_resp = {
            "data": {
                "fields": ["ts_code", "symbol", "name", "area", "industry", "market", "list_date"],
                "items": [["600519.SH", "600519", "贵州茅台", "贵州", "白酒", "主板", "20010827"]],
            }
        }
        with mock.patch.object(src, "_post", return_value=fake_resp):
            records = src.retrieve("600519.SH 怎么样", limit=5)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "600519.SH 贵州茅台")
        self.assertIn("tushare:ts_code:600519.SH", records[0].canonical_id)


class CrunchbaseSourceTests(unittest.TestCase):
    def test_off_when_no_key(self) -> None:
        original = os.environ.pop("CRUNCHBASE_API_KEY", None)
        try:
            src = CrunchbaseSource()
            status, _ = src.check()
            self.assertEqual(status, "off")
            self.assertEqual(src.retrieve("Anthropic", limit=5), [])
        finally:
            if original is not None:
                os.environ["CRUNCHBASE_API_KEY"] = original

    def test_ok_when_key_set(self) -> None:
        src = CrunchbaseSource(api_key="fake-key")
        status, _ = src.check()
        self.assertEqual(status, "ok")


class LinkedInBrokerTests(unittest.TestCase):
    def test_off_when_binary_missing(self) -> None:
        src = LinkedInBrokerSource()
        status, _ = src.check()
        self.assertEqual(status, "off")
        self.assertEqual(src.retrieve("OpenAI", limit=5), [])


class V022CascadeTests(unittest.TestCase):
    def test_finance_includes_tushare(self) -> None:
        self.assertIn("tushare", DEFAULT_DOMAIN_CASCADES["finance"])

    def test_enterprise_uses_free_company_sources(self) -> None:
        # v0.48: crunchbase (sales-gated), opencorporates (paid/£2250yr) and
        # linkedin (no public API) dropped — none obtainable for a single user.
        # Enterprise now leans on free substitutes.
        casc = DEFAULT_DOMAIN_CASCADES["enterprise"]
        self.assertIn("edgar", casc)
        self.assertIn("crossref", casc)
        self.assertIn("wikidata", casc)
        self.assertNotIn("crunchbase", casc)
        self.assertNotIn("linkedin", casc)
        self.assertNotIn("opencorporates", casc)


# ---------------------------------------------------------------------------
# v0.23 — Judge LLM framework
# ---------------------------------------------------------------------------


class JudgeBaseTests(unittest.TestCase):
    def test_dimension_score_to_dict(self) -> None:
        d = DimensionScore(dimension="x", score=0.5, weight=0.2, rationale="r")
        self.assertEqual(d.to_dict()["dimension"], "x")

    def test_composite_score_handles_zero_weight(self) -> None:
        dims = [
            DimensionScore("a", 0.8, weight=0.0),
            DimensionScore("b", 0.6, weight=1.0),
        ]
        self.assertEqual(composite_score(dims), 0.6)

    def test_judges_registry_get_raises_on_unknown(self) -> None:
        registry = Judges()
        registry.register(HeuristicJudge())
        with self.assertRaises(KeyError):
            registry.get("does-not-exist")
        self.assertEqual(registry.names(), ["heuristic"])


class HeuristicJudgeTests(unittest.TestCase):
    def test_high_quality_answer_scores_high_on_citation(self) -> None:
        judge = HeuristicJudge()
        request = JudgeRequest(
            domain="research",
            candidate=(
                "ACE evolves context across sessions [1]. Mem0 OS adds bitemporal "
                "validity [2]. GEPA optimises prompts reflectively [3].\n\n"
                "Letta's MemFS pivot makes memory git-backed [4].\n\n"
                "## References\n\n[1] arxiv:2510.04618\n[2] arxiv:2507.19457\n"
                "[3] arxiv:2510.04618\n[4] letta-blog-2026-03-16"
            ),
            reference="",
            rubric={
                "evidence_coverage": 0.30, "information_density": 0.20,
                "citation_support": 0.25, "style_fit": 0.10,
                "uncertainty_calibration": 0.15,
            },
        )
        verdict = judge.evaluate(request)
        self.assertEqual(verdict.judge_name, "heuristic")
        # 4 distinct citations → coverage 0.4
        coverage_dim = next(d for d in verdict.dimensions if d.dimension == "evidence_coverage")
        self.assertAlmostEqual(coverage_dim.score, 0.4, places=2)
        # composite should reflect mostly good (>= 0.4) but not 1.0
        self.assertGreater(verdict.composite, 0.3)
        self.assertLess(verdict.composite, 1.0)

    def test_empty_candidate_scores_zero_coverage(self) -> None:
        judge = HeuristicJudge()
        verdict = judge.evaluate(JudgeRequest(domain="research", candidate=""))
        coverage = next(d for d in verdict.dimensions if d.dimension == "evidence_coverage")
        self.assertEqual(coverage.score, 0.0)

    def test_hedged_candidate_gets_uncertainty_credit(self) -> None:
        judge = HeuristicJudge()
        verdict = judge.evaluate(JudgeRequest(
            domain="international_relations",
            candidate=(
                "The scenario may unfold across three possibly overlapping "
                "trajectories.  Approximately 40% likely to escalate."
            ),
        ))
        uncertainty = next(d for d in verdict.dimensions if d.dimension == "uncertainty_calibration")
        self.assertGreater(uncertainty.score, 0.0)

    def test_passes_extra_dimensions_through(self) -> None:
        judge = HeuristicJudge()
        verdict = judge.evaluate(JudgeRequest(
            domain="finance",
            candidate="dummy",
            rubric={"data_freshness": 0.30, "risk_disclosure": 0.30},
        ))
        names = {d.dimension for d in verdict.dimensions}
        self.assertIn("data_freshness", names)
        self.assertIn("risk_disclosure", names)


class LLMJudgeTests(unittest.TestCase):
    def test_fallback_when_no_ccload_and_no_sdk(self) -> None:
        # Clear env so neither path is available.  v0.42+ also includes
        # DeepSeek as a third LLM channel — pass empty deepseek_api_key
        # explicitly so the test still exercises the fallback path.
        keys = ("OMNI_CCLOAD_BASE", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY")
        original = {k: os.environ.pop(k, None) for k in keys}
        try:
            judge = LLMJudge(
                ccload_base="", anthropic_api_key="", deepseek_api_key="",
            )
            self.assertFalse(judge.available())
            verdict = judge.evaluate(JudgeRequest(
                domain="research", candidate="hello [1]",
            ))
            self.assertEqual(verdict.judge_name, "llm")
            self.assertEqual(verdict.metadata.get("mode"), "fallback-heuristic")
            # Composite should match heuristic given identical input.
            heur = HeuristicJudge().evaluate(JudgeRequest(
                domain="research", candidate="hello [1]",
            ))
            self.assertAlmostEqual(verdict.composite, heur.composite, places=4)
        finally:
            for k, v in original.items():
                if v is not None:
                    os.environ[k] = v

    def test_uses_ccload_when_base_present(self) -> None:
        judge = LLMJudge(ccload_base="http://localhost:8080", anthropic_api_key="")
        self.assertTrue(judge.available())

    def test_parses_ccload_response_into_verdict(self) -> None:
        judge = LLMJudge(ccload_base="http://localhost:8080", anthropic_api_key="")
        fake_raw = (
            '{"composite": 0.82, "dimensions": [\n'
            '{"dimension": "evidence_coverage", "score": 0.9, "weight": 0.3, "rationale": "5 refs"},\n'
            '{"dimension": "information_density", "score": 0.7, "weight": 0.2, "rationale": "ok"},\n'
            '{"dimension": "citation_support", "score": 0.9, "weight": 0.2, "rationale": "all cited"},\n'
            '{"dimension": "style_fit", "score": 0.6, "weight": 0.15, "rationale": "headings"},\n'
            '{"dimension": "uncertainty_calibration", "score": 0.8, "weight": 0.15, "rationale": "hedged"}\n'
            '], "rationale": "Strong evidence + citation hygiene."}'
        )
        with mock.patch.object(judge, "_call_ccload", return_value=fake_raw):
            verdict = judge.evaluate(JudgeRequest(
                domain="research", candidate="x", reference="y",
            ))
        self.assertEqual(verdict.judge_name, "llm")
        self.assertEqual(verdict.metadata.get("mode"), "ccload")
        self.assertAlmostEqual(verdict.composite, 0.82, places=2)
        self.assertEqual(len(verdict.dimensions), 5)

    def test_falls_back_when_ccload_returns_non_json(self) -> None:
        judge = LLMJudge(ccload_base="http://localhost:8080", anthropic_api_key="")
        with mock.patch.object(judge, "_call_ccload", return_value="not json at all"):
            verdict = judge.evaluate(JudgeRequest(
                domain="research", candidate="abc [1]",
            ))
        self.assertEqual(verdict.metadata.get("mode"), "fallback-heuristic")
        self.assertIn("non-JSON", verdict.metadata.get("reason", ""))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
