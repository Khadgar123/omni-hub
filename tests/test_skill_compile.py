"""Tests for v0.16-B preference → SKILL.md compile."""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.harness.dspy_compile import compile_skill_md
from omni_hub.harness.preference import PreferenceRecord, PreferenceStore


def _seed_preference(root: Path, *, domain: str = "research") -> PreferenceStore:
    store = PreferenceStore(root / ".omni" / "preference")
    store.append(PreferenceRecord(
        task_id="t1", domain=domain, prompt_version="v0",
        candidate_text="# Compiled wiki page about agentic context engineering.\n\n"
                       "The ACE paper proposes evolving context as a tactical wiki.",
        decision="accepted",
        accepted_spans=["agentic context engineering"],
        reason="good citation density",
        reviewer="local-user",
    ))
    store.append(PreferenceRecord(
        task_id="t2", domain=domain, prompt_version="v0",
        candidate_text="Hallucinated claim with no source citations.",
        decision="rejected",
        rejected_spans=["no source citations"],
        reason="missing citations",
        reviewer="local-user",
    ))
    return store


class SkillCompileFrontmatterTests(unittest.TestCase):
    def test_emits_anthropic_skill_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _seed_preference(root, domain="research")
            report = compile_skill_md(
                domain="research",
                output_root=root / ".agents" / "skills",
                preference_store=store,
            )
            self.assertEqual(report.skill_id, "research-wiki")
            target = Path(report.target_path)
            self.assertTrue(target.exists())
            body = target.read_text(encoding="utf-8")

            # Frontmatter contract: name + description both present.
            self.assertTrue(body.startswith("---\n"))
            header = body.split("\n---\n", 1)[0]
            self.assertIn("name: research-wiki", header)
            self.assertRegex(header, r"description: .+")
            # Description must be ≤ 1024 chars.
            desc_match = re.search(r"description: (.*)", header)
            self.assertIsNotNone(desc_match)
            self.assertLessEqual(len(desc_match.group(1)), 1024)

            # Body should mention positive + negative exemplar counts.
            self.assertIn("Positive exemplars (1)", body)
            self.assertIn("Anti-patterns", body)
            self.assertIn("Anti-patterns — do not imitate (1)", body)

    def test_skill_id_kebab_case_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _seed_preference(root)
            with self.assertRaises(ValueError):
                compile_skill_md(
                    domain="research",
                    skill_id="Research_Wiki",  # uppercase / underscore -> reject
                    output_root=root / ".agents" / "skills",
                    preference_store=store,
                )

    def test_description_override_truncates_at_1024(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _seed_preference(root)
            long_desc = "x" * 2000
            report = compile_skill_md(
                domain="research",
                description=long_desc,
                output_root=root / ".agents" / "skills",
                preference_store=store,
            )
            body = Path(report.target_path).read_text(encoding="utf-8")
            desc = re.search(r"description: (.*)", body).group(1)
            self.assertLessEqual(len(desc), 1024)
            self.assertTrue(desc.endswith("..."))

    def test_includes_authoritative_sources_from_domain_schema(self) -> None:
        """The compiled SKILL.md must list domain authoritative sources so
        Claude Code can cite without re-reading the global schema."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _seed_preference(root, domain="research")
            report = compile_skill_md(
                domain="research",
                output_root=root / ".agents" / "skills",
                preference_store=store,
            )
            body = Path(report.target_path).read_text(encoding="utf-8")
            # research authoritative_sources include openalex + semantic_scholar.
            self.assertIn("`openalex`", body)
            self.assertIn("`semantic_scholar`", body)
            self.assertIn("Stale-after-days: `730`", body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
