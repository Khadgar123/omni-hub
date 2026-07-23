"""P0.2 — applied pages are approved; proposed pages are closed-by-default.

apply_wiki_proposal rewrites the synthesis frontmatter to approved; the
default search / context-pack gate (_is_closed_page) skips proposed so an
un-applied draft never leaks downstream.
"""

import unittest
from datetime import UTC, datetime

from omni_hub.knowledge_plane import _is_closed_page, _set_frontmatter_review_state


class FrontmatterRewriteTests(unittest.TestCase):
    def test_rewrites_proposed_to_approved(self) -> None:
        body = (
            "---\npage_type: synthesis\nreview_state: proposed\n"
            "confidence: medium\n---\n\n# Title\n\nbody\n"
        )
        out = _set_frontmatter_review_state(body, "approved")
        self.assertIn("review_state: approved", out)
        self.assertNotIn("review_state: proposed", out)
        # only the frontmatter line changes; the rest is untouched
        self.assertIn("# Title", out)
        self.assertIn("confidence: medium", out)

    def test_only_first_match_touched(self) -> None:
        body = "review_state: proposed\nx: review_state: proposed (in text)\n"
        out = _set_frontmatter_review_state(body, "approved")
        self.assertEqual(out.count("review_state: approved"), 1)


class ClosedPageGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 5, 29, tzinfo=UTC)

    def test_proposed_is_closed(self) -> None:
        self.assertTrue(_is_closed_page({"review_state": "proposed"}, now=self.now))

    def test_rejected_superseded_closed(self) -> None:
        self.assertTrue(_is_closed_page({"review_state": "rejected"}, now=self.now))
        self.assertTrue(_is_closed_page({"review_state": "superseded"}, now=self.now))

    def test_approved_is_open(self) -> None:
        self.assertFalse(_is_closed_page({"review_state": "approved"}, now=self.now))

    def test_approved_but_expired_is_closed(self) -> None:
        self.assertTrue(_is_closed_page(
            {"review_state": "approved", "t_valid_to": "2020-01-01T00:00:00+00:00"},
            now=self.now,
        ))


if __name__ == "__main__":
    unittest.main()
