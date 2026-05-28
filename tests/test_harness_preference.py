from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.harness import dspy_compile
from omni_hub.harness.preference import PreferenceRecord, PreferenceStore


class PreferenceStoreTests(unittest.TestCase):
    def test_append_and_read_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PreferenceStore(Path(tmp) / "pref")
            store.append(PreferenceRecord(
                domain="research", candidate_text="A grounded claim [1].",
                decision="accepted", accepted_spans=["A grounded claim [1]."],
                reason="cites source",
            ))
            store.append(PreferenceRecord(
                domain="research", candidate_text="Obviously this is significant.",
                decision="rejected", rejected_spans=["Obviously this is significant."],
                reason="low-signal phrase",
            ))
            recs = list(store.read("research"))
            self.assertEqual(len(recs), 2)
            self.assertEqual(recs[0].decision, "accepted")
            self.assertEqual(recs[1].decision, "rejected")

    def test_stats_counts_each_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PreferenceStore(Path(tmp) / "pref")
            for _ in range(3):
                store.append(PreferenceRecord(domain="d", decision="accepted"))
            for _ in range(2):
                store.append(PreferenceRecord(domain="d", decision="rejected"))
            store.append(PreferenceRecord(domain="d", decision="edited"))
            stats = store.stats("d")
            self.assertEqual(stats["accepted"], 3)
            self.assertEqual(stats["rejected"], 2)
            self.assertEqual(stats["edited"], 1)
            self.assertEqual(stats["total"], 6)

    def test_export_filters_by_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PreferenceStore(Path(tmp) / "pref")
            store.append(PreferenceRecord(domain="d", decision="accepted", candidate_text="A"))
            store.append(PreferenceRecord(domain="d", decision="rejected", candidate_text="B"))
            store.append(PreferenceRecord(domain="d", decision="edited", candidate_text="C"))
            pos = store.export("d", include_decisions=("accepted", "edited"))
            neg = store.export("d", include_decisions=("rejected",))
            self.assertEqual({r.candidate_text for r in pos}, {"A", "C"})
            self.assertEqual({r.candidate_text for r in neg}, {"B"})

    def test_list_domains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PreferenceStore(Path(tmp) / "pref")
            store.append(PreferenceRecord(domain="research"))
            store.append(PreferenceRecord(domain="engineering"))
            self.assertEqual(
                set(store.list_domains()), {"research", "engineering"}
            )


class DspyCompileFallbackTests(unittest.TestCase):
    def test_manual_fallback_writes_system_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store = PreferenceStore(tmp_path / "pref")
            for i in range(4):
                store.append(PreferenceRecord(
                    domain="research", decision="accepted",
                    candidate_text=f"Grounded claim {i} [{i+1}].",
                    accepted_spans=[f"Grounded claim {i} [{i+1}]."],
                    reason="cites source",
                ))
            for i in range(2):
                store.append(PreferenceRecord(
                    domain="research", decision="rejected",
                    candidate_text=f"Obviously {i}", reason="low-signal",
                    rejected_spans=[f"Obviously {i}"],
                ))
            report = dspy_compile.compile(
                domain="research",
                from_version="v0",
                output_root=tmp_path / "prompts",
                preference_store=store,
                backend="manual",
            )
            self.assertEqual(report.backend, "manual-fewshot")
            self.assertEqual(report.from_version, "v0")
            self.assertEqual(report.to_version, "v1")
            self.assertEqual(report.positive_used, 4)
            self.assertEqual(report.negative_used, 2)
            prompt_path = Path(report.output_dir) / "system_prompt.md"
            self.assertTrue(prompt_path.exists())
            content = prompt_path.read_text(encoding="utf-8")
            self.assertIn("Positive exemplars (4)", content)
            self.assertIn("Negative exemplars", content)
            self.assertIn("Reviewer feedback", content)

    def test_dspy_backend_required_but_missing_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PreferenceStore(Path(tmp) / "pref")
            if dspy_compile._dspy_available():
                self.skipTest("dspy is installed; cannot exercise the missing-fork path")
            with self.assertRaises(RuntimeError):
                dspy_compile.compile(
                    domain="d", output_root=Path(tmp) / "prompts",
                    preference_store=store, backend="dspy",
                )

    def test_version_bumper(self) -> None:
        self.assertEqual(dspy_compile._bump_version("v0"), "v1")
        self.assertEqual(dspy_compile._bump_version("v12"), "v13")
        self.assertEqual(dspy_compile._bump_version("custom"), "custom-next")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
