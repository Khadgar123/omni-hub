"""Cascade measured-quality rerank — the v0.47 "降级不一定差" switch.

Default (quality_weight=0.0) keeps fetch/RRF order untouched.  With a
positive weight + a quality_fn, a measured-good source is promoted above a
priority-first-but-low-quality one, which is the whole point: priority
(cascade order / tier) is decoupled from realized quality.
"""

from __future__ import annotations

import unittest

from omni_hub.retrieval.base import RetrievalRecord
from omni_hub.retrieval.cascade import Cascade


class _Src:
    def __init__(self, name: str) -> None:
        self.name = name

    def retrieve(self, query, *, limit=5, domain=""):
        return [
            RetrievalRecord(
                source=self.name,
                title=f"{self.name} hit",
                url=f"https://{self.name}/x",
                snippet="a snippet.",
                canonical_id=f"{self.name}:1",
            )
        ]

    # cascade calls .name attr; provided above


class CascadeQualityRerankTests(unittest.TestCase):
    def _cascade(self) -> Cascade:
        return Cascade({"primary": _Src("primary"), "fallback": _Src("fallback")})

    def test_default_off_preserves_order(self) -> None:
        res = self._cascade().retrieve("q", sources=["primary", "fallback"], fusion="concat")
        self.assertEqual([r.source for r in res.records], ["primary", "fallback"])

    def test_weight_zero_with_fn_is_identity(self) -> None:
        # fn present but weight 0 -> rerank skipped entirely
        res = self._cascade().retrieve(
            "q",
            sources=["primary", "fallback"],
            fusion="concat",
            quality_fn=lambda s: 1.0 if s == "fallback" else 0.0,
            quality_weight=0.0,
        )
        self.assertEqual([r.source for r in res.records], ["primary", "fallback"])

    def test_measured_good_fallback_outranks_priority_primary(self) -> None:
        quality = {"primary": 0.1, "fallback": 0.9}  # measured: fallback is better
        res = self._cascade().retrieve(
            "q",
            sources=["primary", "fallback"],  # priority says primary first
            fusion="concat",
            quality_fn=lambda s: quality.get(s, 0.0),
            quality_weight=0.9,
        )
        self.assertEqual(
            res.records[0].source,
            "fallback",
            "a measured-good fallback must outrank the priority-first primary",
        )
        # cite ids reflect the reranked order
        self.assertEqual(res.records[0].cite_id, "R1")
        self.assertEqual(res.records[1].cite_id, "R2")


if __name__ == "__main__":
    unittest.main()
