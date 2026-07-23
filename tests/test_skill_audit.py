"""Skill-taxonomy audit tests (HR #8/#9/#10) — synthetic fixtures so the
checks are deterministic and independent of the live (evolving) stubs."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omni_hub.skill_audit import audit_skills


def _write(root: Path, skill_id: str, frontmatter: str, body: str = "answer only.") -> None:
    d = root / skill_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")


class SkillAuditTests(unittest.TestCase):
    def test_findings_for_each_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # clean domain skill — no finding
            _write(root, "alpha-wiki",
                   'name: alpha-wiki\nstatus: active-domain\ndescription: |\n'
                   '  Triggers:\n  - "alpha one"\n  - "alpha two"')
            # *-wiki but does NOT declare the layer -> layer_missing
            _write(root, "beta-wiki",
                   'name: beta-wiki\ndescription: |\n  Triggers:\n  - "beta thing"')
            # two domain skills with identical triggers -> trigger_overlap
            _write(root, "gamma-wiki",
                   'name: gamma-wiki\nstatus: active-domain\ndescription: |\n'
                   '  - "shared one"\n  - "shared two"')
            _write(root, "delta-wiki",
                   'name: delta-wiki\nstatus: active-domain\ndescription: |\n'
                   '  - "shared one"\n  - "shared two"')
            # domain skill running a write verb in a fenced block -> answer_only_leak
            _write(root, "epsilon-wiki",
                   'name: epsilon-wiki\nstatus: active-domain\ndescription: |\n  - "eps q"',
                   body="Do it:\n\n```bash\nomni-hub wiki-ingest --run-id x --domain research\n```\n")

            findings = audit_skills(root)
            by_rule: dict[str, list[str]] = {}
            for f in findings:
                by_rule.setdefault(f.rule, []).append(f.skill_id)

            self.assertIn("beta-wiki", by_rule.get("layer_missing", []))
            self.assertTrue(
                any("gamma-wiki" in s and "delta-wiki" in s for s in by_rule.get("trigger_overlap", [])),
                f"expected gamma~delta overlap, got {by_rule.get('trigger_overlap')}",
            )
            self.assertIn("epsilon-wiki", by_rule.get("answer_only_leak", []))
            # the clean skill must not appear anywhere
            self.assertNotIn("alpha-wiki", [f.skill_id for f in findings])

    def test_clean_set_has_no_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "one-wiki",
                   'name: one-wiki\nstatus: active-domain\ndescription: |\n  - "uno"\n  - "ichi"')
            _write(root, "two-wiki",
                   'name: two-wiki\nstatus: active-domain\ndescription: |\n  - "dos"\n  - "ni"')
            self.assertEqual(audit_skills(root), [])

    def test_runs_against_live_skills_dir(self) -> None:
        # smoke: the real tree must at least parse without raising
        live = Path(__file__).resolve().parents[1] / ".agents" / "skills"
        if live.is_dir():
            findings = audit_skills(live)
            self.assertIsInstance(findings, list)


if __name__ == "__main__":
    unittest.main()
