"""Regression: the WS1 projection-integrity check is wired into wiki-doctor.

wiki_projection.doctor_projection() detects claims<->synthesis-page drift
(orphan pages with no backing claim; claim-referenced targets with no page).
This pins that run_doctor surfaces it and that it fires in both directions.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub import knowledge_plane, wiki_doctor


def _proj(report) -> "wiki_doctor.DoctorCheck":
    for c in report.checks:
        if c.name == "projection_integrity":
            return c
    raise AssertionError("projection_integrity check missing from run_doctor")


class WikiDoctorProjectionTests(unittest.TestCase):
    def test_clean_workspace_is_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            check = _proj(wiki_doctor.run_doctor(root))
            self.assertTrue(check.ok, check.detail)
            self.assertEqual(check.severity, "info")

    def test_orphan_synthesis_page_is_flagged_as_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            syn = root / "vault" / "wiki" / "syntheses"
            syn.mkdir(parents=True, exist_ok=True)
            (syn / "orphan.md").write_text(
                "---\npage_type: synthesis\n---\n# Orphan\n", encoding="utf-8"
            )
            report = wiki_doctor.run_doctor(root)
            check = _proj(report)
            self.assertFalse(check.ok)
            self.assertEqual(check.severity, "error")
            self.assertIn("orphan.md", str(check.detail.get("orphan_pages")))
            # an error-severity check must drag the overall verdict down
            self.assertFalse(report.ok)


if __name__ == "__main__":
    unittest.main()
