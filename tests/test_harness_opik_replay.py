from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.harness import opik_bridge, replay


class OpikJsonlBackendTests(unittest.TestCase):
    def test_log_and_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            backend = opik_bridge.LocalJsonlBackend(Path(tmp) / "trace.jsonl")
            backend.log("generation", {"text": "A"}, record_id="r1")
            backend.log("judge", {"winner": "r1"}, record_id="r1")
            backend.log("preference", {"decision": "accepted", "domain": "d"})
            entries = list(backend.read())
            self.assertEqual(len(entries), 3)
            self.assertEqual({e["kind"] for e in entries}, {"generation", "judge", "preference"})

    def test_filter_by_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            backend = opik_bridge.LocalJsonlBackend(Path(tmp) / "trace.jsonl")
            for k in ("generation", "judge", "preference", "preference"):
                backend.log(k, {"x": 1})
            prefs = list(backend.read(kind="preference"))
            self.assertEqual(len(prefs), 2)


class ReplayStatsTests(unittest.TestCase):
    def test_stats_aggregates_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Build a tiny trace file
            backend = opik_bridge.LocalJsonlBackend(Path(tmp) / "trace.jsonl")
            # 2 generations
            for i in range(2):
                backend.log("generation", {"text": f"x{i}"}, record_id=f"g{i}")
            # 3 preference (research: 2 accepted 1 rejected)
            for d in ("accepted", "accepted", "rejected"):
                backend.log("preference", {"domain": "research", "decision": d})
            # 2 compiles
            backend.log("compile", {"domain": "research", "to_version": "v1"})
            backend.log("compile", {"domain": "research", "to_version": "v2"})
            # 1 judge with winner deepseek-v4-pro
            backend.log("judge", {
                "winner_candidate_id": "cand-1",
                "record": {
                    "candidates": [
                        {"candidate_id": "cand-1", "model": "deepseek-v4-pro"},
                        {"candidate_id": "cand-2", "model": "claude-opus"},
                    ],
                },
            })

            # Force replay/stats to use the same path
            import os
            original_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                # Adjust opik_bridge default by monkeypatching path
                opik_bridge.LocalJsonlBackend.__init__.__defaults__ = (
                    Path(tmp) / "trace.jsonl",
                )
                result = replay.stats(prefer_backend="local").to_dict()
            finally:
                os.chdir(original_cwd)
                # Restore default
                opik_bridge.LocalJsonlBackend.__init__.__defaults__ = (
                    Path(".omni/traces/traces.jsonl"),
                )

            self.assertEqual(result["total_traces"], 8)
            self.assertEqual(result["by_kind"]["generation"], 2)
            self.assertEqual(result["by_kind"]["preference"], 3)
            self.assertEqual(result["by_kind"]["compile"], 2)
            self.assertEqual(result["by_kind"]["judge"], 1)
            self.assertEqual(
                result["preference_by_domain"]["research"],
                {"accepted": 2, "rejected": 1},
            )
            self.assertEqual(
                result["compiles_by_domain"]["research"], ["v1", "v2"],
            )
            self.assertEqual(
                result["judge_wins_by_model"]["deepseek-v4-pro"], 1,
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
