"""v0.46 provenance + measured source quality (the 'provenance' leg of the
refactor/provenance-trace-plan branch).

Covers:
  * source_tier() cost/access lookup
  * Cascade stamping served_via / source_tier / cascade_rank onto records
  * SourceQualityStore rolling outcomes + freshness-decayed quality_score
  * the load-bearing principle: a tier-2 *fallback* can out-SCORE a tier-0
    *primary* — priority (cascade order / tier) is decoupled from quality.
"""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from omni_hub.retrieval.base import RetrievalRecord
from omni_hub.retrieval.cascade import Cascade
from omni_hub.retrieval.source_policy import source_tier
from omni_hub.retrieval.source_quality import SourceQualityStore


class _Src:
    def __init__(self, name: str) -> None:
        self.name = name

    def retrieve(self, query, *, limit=5, domain=""):
        return [
            RetrievalRecord(
                source=self.name,
                title=f"{self.name} hit",
                url=f"https://{self.name}/x",
                snippet="a snippet sentence.",
                canonical_id=f"{self.name}:1",
            )
        ]


class SourceTierTests(unittest.TestCase):
    def test_tiers_match_policy(self) -> None:
        self.assertEqual(source_tier("openalex"), 0)        # academic, free
        self.assertEqual(source_tier("semantic_scholar"), 1)  # personal key
        self.assertEqual(source_tier("x_twitter"), 2)       # paid broker
        self.assertEqual(source_tier("totally_unknown"), 0)  # default free


class CascadeProvenanceTests(unittest.TestCase):
    def test_records_are_stamped(self) -> None:
        c = Cascade({"openalex": _Src("openalex"), "x_twitter": _Src("x_twitter")})
        res = c.retrieve("q", sources=["openalex", "x_twitter"], fusion="concat")
        by_src = {r.source: r for r in res.records}
        self.assertEqual(by_src["openalex"].metadata["served_via"], "live")
        self.assertEqual(by_src["openalex"].metadata["source_tier"], 0)
        self.assertEqual(by_src["openalex"].metadata["cascade_rank"], 0)
        self.assertEqual(by_src["x_twitter"].metadata["source_tier"], 2)
        self.assertEqual(by_src["x_twitter"].metadata["cascade_rank"], 1)


class SourceQualityStoreTests(unittest.TestCase):
    def test_record_and_score(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            store = SourceQualityStore(Path(d))
            stamp = "2026-05-29T00:00:00+00:00"
            now = datetime(2026, 5, 29, 0, 0, 0, tzinfo=UTC)
            # openalex tried 3x, succeeded 1x → low rate; x_twitter 3/3 → high.
            for ok_alex, ok_x in [(False, True), (False, True), (True, True)]:
                tried = ["openalex", "x_twitter"]
                succeeded = [s for s, ok in (("openalex", ok_alex), ("x_twitter", ok_x)) if ok]
                store.record_cascade(tried=tried, succeeded=succeeded, at=stamp)
            self.assertAlmostEqual(store.stat("openalex").success_rate(), 1 / 3)
            self.assertAlmostEqual(store.stat("x_twitter").success_rate(), 1.0)
            # Same freshness (same stamp) → the tier-2 fallback out-scores the
            # tier-0 primary purely on measured quality.
            score_x = store.quality_score("x_twitter", now=now)
            score_alex = store.quality_score("openalex", now=now)
            self.assertGreater(score_x, score_alex)
            self.assertGreater(source_tier("x_twitter"), source_tier("openalex"))  # priority says the opposite

    def test_freshness_decays(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            store = SourceQualityStore(Path(d))
            store.record_cascade(
                tried=["openalex"], succeeded=["openalex"],
                at="2026-01-01T00:00:00+00:00",
            )
            fresh = store.quality_score(
                "openalex", now=datetime(2026, 1, 1, tzinfo=UTC), half_life_days=30,
            )
            stale = store.quality_score(
                "openalex", now=datetime(2026, 3, 2, tzinfo=UTC), half_life_days=30,
            )
            self.assertAlmostEqual(fresh, 1.0, places=3)        # rate 1.0 × freshness 1.0
            self.assertAlmostEqual(stale, 0.25, places=2)       # ~2 half-lives → 0.25
            self.assertEqual(store.quality_score("never_seen"), 0.0)


if __name__ == "__main__":
    unittest.main()
