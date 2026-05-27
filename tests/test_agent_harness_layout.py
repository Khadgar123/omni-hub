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

        candidates = {item["id"]: item for item in manifest["candidates"]}
        self.assertEqual(candidates["opik"]["decision"], "evaluate-before-fork")


if __name__ == "__main__":
    unittest.main()
