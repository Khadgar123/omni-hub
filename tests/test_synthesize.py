"""Tests for the cascade synthesis layer (v0.45)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.retrieval.base import RetrievalRecord
from omni_hub.retrieval import synthesize as syn_mod
from omni_hub.retrieval.synthesize import synthesize_answer, SynthesisResult


def _recs(n: int = 3) -> list[RetrievalRecord]:
    return [
        RetrievalRecord(
            source=f"src{i}", title=f"Title {i}",
            snippet=f"Snippet body {i} about the topic.",
            url=f"https://example.com/{i}",
            canonical_id=f"c{i}",
        )
        for i in range(1, n + 1)
    ]


class SynthesizeFailSoftTests(unittest.TestCase):
    def test_no_records_returns_no_records_mode(self) -> None:
        r = synthesize_answer("q", [], domain="research")
        self.assertEqual(r.mode, "no-records")
        self.assertEqual(r.used_record_count, 0)

    def test_empty_query_returns_no_records(self) -> None:
        r = synthesize_answer("   ", _recs(), domain="research")
        self.assertEqual(r.mode, "no-records")

    def test_no_llm_channel_falls_back_to_concat(self) -> None:
        # Force both channels off: empty ccload + empty deepseek key.
        with patch.object(syn_mod, "_resolve_deepseek_key", return_value=""), \
             patch.object(syn_mod, "_ccload_base", return_value=""):
            r = synthesize_answer("what is X", _recs(3), domain="research",
                                  deepseek_api_key="", ccload_base="")
        self.assertEqual(r.mode, "fallback-concat")
        self.assertIn("un-synthesized", r.answer)
        self.assertEqual(r.used_record_count, 3)
        # citations always built regardless of channel
        self.assertEqual(len(r.citations), 3)

    def test_transport_exception_degrades_to_concat(self) -> None:
        # DeepSeek key present but transport raises → concat fallback.
        with patch.object(syn_mod, "_resolve_deepseek_key", return_value="sk-fake"), \
             patch.object(syn_mod, "_ccload_base", return_value=""), \
             patch.object(syn_mod, "_call_deepseek", side_effect=RuntimeError("boom")):
            r = synthesize_answer("what is X", _recs(2), domain="research")
        self.assertEqual(r.mode, "fallback-concat")
        self.assertEqual(r.used_record_count, 2)

    def test_max_records_caps_used(self) -> None:
        with patch.object(syn_mod, "_resolve_deepseek_key", return_value=""), \
             patch.object(syn_mod, "_ccload_base", return_value=""):
            r = synthesize_answer("q", _recs(10), domain="research",
                                  max_records=4, deepseek_api_key="", ccload_base="")
        self.assertEqual(r.used_record_count, 4)
        self.assertEqual(len(r.citations), 4)


class SynthesizeParseTests(unittest.TestCase):
    def test_cited_n_extracted_from_answer(self) -> None:
        fake_answer = "Claim one [1]. Claim two [3]. Out of range [99]."
        with patch.object(syn_mod, "_resolve_deepseek_key", return_value="sk-fake"), \
             patch.object(syn_mod, "_ccload_base", return_value=""), \
             patch.object(syn_mod, "_call_deepseek", return_value=fake_answer):
            r = synthesize_answer("q", _recs(5), domain="research")
        self.assertEqual(r.mode, "deepseek-direct")
        # [99] out of range (only 5 records) must be filtered
        self.assertEqual(r.cited_n, [1, 3])

    def test_ccload_preferred_over_deepseek(self) -> None:
        with patch.object(syn_mod, "_ccload_base", return_value="http://localhost:8080"), \
             patch.object(syn_mod, "_call_ccload", return_value="Answer via ccload [1]."):
            r = synthesize_answer("q", _recs(2), domain="research")
        self.assertEqual(r.mode, "ccload")
        self.assertIn("ccload", r.answer)

    def test_to_dict_roundtrip(self) -> None:
        with patch.object(syn_mod, "_resolve_deepseek_key", return_value=""), \
             patch.object(syn_mod, "_ccload_base", return_value=""):
            r = synthesize_answer("q", _recs(2), domain="research",
                                  deepseek_api_key="", ccload_base="")
        d = r.to_dict()
        self.assertIn("answer", d)
        self.assertIsInstance(d["citations"], list)
        self.assertEqual(d["citations"][0]["n"], 1)


if __name__ == "__main__":
    unittest.main()
