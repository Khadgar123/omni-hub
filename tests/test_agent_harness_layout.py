from pathlib import Path
import json
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class AgentHarnessLayoutTests(unittest.TestCase):
    def test_manifest_matches_submodule_layout(self) -> None:
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (root / "agent-harness" / "manifest.json").read_text(encoding="utf-8")
        )
        gitmodules = (root / ".gitmodules").read_text(encoding="utf-8")

        expected = {
            "swe-agent": ("agent-harness/swe-agent", "main"),
            "promptfoo": ("agent-harness/promptfoo", "main"),
            "argilla": ("agent-harness/argilla", "develop"),
            "graphiti": ("agent-harness/graphiti", "main"),
        }
        forks = {item["id"]: item for item in manifest["forks"]}

        self.assertEqual(set(forks), set(expected))
        for fork_id, (path, branch) in expected.items():
            self.assertEqual(forks[fork_id]["path"], path)
            self.assertEqual(forks[fork_id]["branch"], branch)
            self.assertEqual(forks[fork_id]["decision"], "fork")
            self.assertIn(f"[submodule \"{path}\"]", gitmodules)
            self.assertIn(f"path = {path}", gitmodules)

        pending = {item["id"]: item for item in manifest.get("pending_forks", [])}
        # 2026 reassessment promoted these three. They live in pending_forks
        # until the user creates personal forks on GitHub.
        for pid in ("dspy", "openhands", "opik"):
            self.assertIn(pid, pending, f"{pid} missing from pending_forks")
            self.assertEqual(
                pending[pid]["decision"],
                "fork-pending-personal-clone",
                f"{pid} should be flagged as fork-pending-personal-clone",
            )
            self.assertTrue(
                pending[pid]["upstream"].startswith("https://"),
                f"{pid} upstream URL missing",
            )
            # pending forks must NOT yet be declared as submodules
            self.assertNotIn(
                f"[submodule \"{pending[pid]['path']}\"]",
                gitmodules,
                f"{pid} should NOT be in .gitmodules until user runs "
                f"add_pending_harness_forks.sh",
            )

        # opik used to be a candidate; after promotion the candidates list may
        # be empty or contain other entries, but opik specifically must NOT be
        # there anymore.
        candidates = {item["id"]: item for item in manifest.get("candidates", [])}
        self.assertNotIn("opik", candidates)

    def test_domain_profiles_cover_first_batch_domains(self) -> None:
        root = Path(__file__).resolve().parents[1]
        profiles = json.loads(
            (root / "agent-harness" / "domain-profiles.json").read_text(
                encoding="utf-8"
            )
        )

        expected_domains = {
            "engineering",
            "research",
            "photography",
            "fashion",
            "chat_relationships",
            "finance",
            "policy",
            "international_relations",
        }
        domains = profiles["domains"]

        self.assertEqual(set(domains), expected_domains)
        for profile in domains.values():
            self.assertTrue(profile["goal"])
            self.assertGreaterEqual(len(profile["required_context"]), 3)
            self.assertGreaterEqual(len(profile["proposal_rules"]), 3)
            self.assertGreaterEqual(len(profile["judge_dimensions"]), 3)


if __name__ == "__main__":
    unittest.main()
