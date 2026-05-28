"""Tests for the SKILL.md ↔ registry/skills.json reconciliation (P1-5)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.skill_sync import parse_frontmatter, sync_skills
from omni_hub.testing import cli_runner as _run_cli


SKILL_MD_FIXTURE = """---
name: example-skill
description: |
  Inspect example things.
  Trigger when user says "example".
license: MIT
omni_hub:
  kind: connector
  entrypoint: operation:example_op
  risk_level: L0
  tags:
    - example
    - docs
---

# Example body
"""

REGISTRY_FIXTURE = [
    {
        "skill_id": "memory-search",
        "name": "Memory Search",
        "kind": "memory",
        "description": "Search canonical local memory.",
        "version": "0.1.0",
        "status": "active",
        "entrypoint": "operation:search_memory",
        "risk_level": "L0",
        "required_permissions": [],
        "connectors": [],
        "tags": ["memory"],
        "inputs": {},
        "outputs": {},
        "source_path": "",
        "created_at": "2026-05-03T00:00:00+00:00",
        "updated_at": "2026-05-03T00:00:00+00:00",
    }
]


def _seed(workspace: Path) -> None:
    (workspace / ".agents" / "skills" / "example-skill").mkdir(parents=True)
    (workspace / ".agents" / "skills" / "example-skill" / "SKILL.md").write_text(
        SKILL_MD_FIXTURE, encoding="utf-8"
    )
    (workspace / "registry").mkdir(parents=True)
    (workspace / "registry" / "skills.json").write_text(
        json.dumps(REGISTRY_FIXTURE), encoding="utf-8"
    )


class FrontmatterParserTests(unittest.TestCase):
    def test_parses_top_level_strings_and_block_scalar(self) -> None:
        fm = parse_frontmatter(SKILL_MD_FIXTURE)
        self.assertEqual(fm["name"], "example-skill")
        self.assertIn("example", fm["description"])
        self.assertEqual(fm["license"], "MIT")

    def test_parses_nested_omni_hub_block(self) -> None:
        fm = parse_frontmatter(SKILL_MD_FIXTURE)
        omni = fm["omni_hub"]
        self.assertEqual(omni["kind"], "connector")
        self.assertEqual(omni["entrypoint"], "operation:example_op")
        self.assertEqual(omni["risk_level"], "L0")
        self.assertEqual(set(omni["tags"]), {"example", "docs"})

    def test_no_frontmatter_returns_empty_dict(self) -> None:
        self.assertEqual(parse_frontmatter("# Just markdown\n"), {})

    def test_unclosed_frontmatter_returns_empty_dict(self) -> None:
        self.assertEqual(parse_frontmatter("---\nname: x\n# missing close\n"), {})


class SyncSkillsTests(unittest.TestCase):
    def test_dry_run_reports_md_only_and_registry_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _seed(workspace)

            summary = sync_skills(workspace, apply=False)
            self.assertFalse(summary["applied"])
            self.assertIn("example-skill", summary["md_only"])
            self.assertIn("memory-search", summary["registry_only"])

    def test_apply_writes_union_with_md_taking_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _seed(workspace)

            summary = sync_skills(workspace, apply=True)
            self.assertTrue(summary["applied"])

            registry = json.loads(
                (workspace / "registry" / "skills.json").read_text(encoding="utf-8")
            )
            skill_ids = {entry["skill_id"] for entry in registry}
            self.assertEqual(skill_ids, {"example-skill", "memory-search"})

            example = next(e for e in registry if e["skill_id"] == "example-skill")
            self.assertEqual(example["kind"], "connector")
            self.assertEqual(example["entrypoint"], "operation:example_op")
            self.assertEqual(example["risk_level"], "L0")

    def test_drift_reported_when_md_and_registry_disagree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _seed(workspace)
            # Add a registry entry for example-skill that disagrees with the SKILL.md
            registry = json.loads(
                (workspace / "registry" / "skills.json").read_text(encoding="utf-8")
            )
            registry.append({
                **REGISTRY_FIXTURE[0],
                "skill_id": "example-skill",
                "name": "Different Name",
                "kind": "memory",
                "entrypoint": "operation:other_op",
                "risk_level": "L1",
            })
            (workspace / "registry" / "skills.json").write_text(
                json.dumps(registry), encoding="utf-8"
            )

            summary = sync_skills(workspace, apply=False)
            self.assertTrue(any(d["skill_id"] == "example-skill" for d in summary["drift"]))
            diff = next(d for d in summary["drift"] if d["skill_id"] == "example-skill")
            self.assertIn("kind", diff["diffs"])
            self.assertIn("entrypoint", diff["diffs"])


class SkillSyncCliTests(unittest.TestCase):
    def test_skill_sync_dry_run_via_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _seed(workspace)
            result = _run_cli(workspace, ["skill-sync"])
            self.assertEqual(result["status"], "succeeded")
            output = result["output"]
            self.assertFalse(output["applied"])
            self.assertIn("example-skill", output["md_only"])

    def test_skill_sync_apply_via_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _seed(workspace)
            result = _run_cli(workspace, ["skill-sync", "--apply"])
            self.assertEqual(result["status"], "succeeded")
            self.assertTrue(result["output"]["applied"])
            registry = json.loads(
                (workspace / "registry" / "skills.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(registry), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
