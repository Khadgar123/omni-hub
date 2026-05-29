"""P0.3 — claim quality gate.

The naive 'first sentence of a snippet -> claim' extraction was writing bare
titles / journal names / company names into the approved ledger.  The gate
keeps those out (they remain as evidence); real predications pass.
"""

import unittest

from omni_hub.knowledge_plane import _looks_like_claim


class ClaimQualityGateTests(unittest.TestCase):
    def test_rejects_titles_venues_entities(self) -> None:
        # the exact noise found in the polluted ledger
        for noise in (
            "Constellations",
            "Science China Information Sciences",
            "Journal of Infection",
            "Ageing Research Reviews",
            "Annals of Oncology",
            "Computers in Human Behavior",
            "LyondellBasell Industries N.V.",
        ):
            self.assertFalse(_looks_like_claim(noise), f"should reject: {noise!r}")

    def test_accepts_real_claims(self) -> None:
        for good in (
            "A primary bottleneck in contact-rich manipulation is the "
            "difficulty of collecting real-world data.",
            "On-policy self-distillation improves LLM reasoning by turning "
            "sparse verifier outcomes into dense token-level supervision.",
            "该方法通过自适应工具编排显著提升了视觉推理的准确率。",  # dense CJK claim
        ):
            self.assertTrue(_looks_like_claim(good), f"should accept: {good!r}")

    def test_short_with_verb_still_rejected(self) -> None:
        # has a verb but too short to be a substantive claim
        self.assertFalse(_looks_like_claim("It is good."))

    def test_long_without_verb_rejected(self) -> None:
        # long enough but no predicate — a comma-joined title list
        self.assertFalse(
            _looks_like_claim("Deep Learning, Reinforcement Learning, and Optimization")
        )


if __name__ == "__main__":
    unittest.main()
