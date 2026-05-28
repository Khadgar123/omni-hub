"""Tests for progressive-disclosure context-pack tiers + bitemporal wiki-search filter."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub import knowledge_plane


def _seed_pages(root: Path) -> None:
    knowledge_plane.init_layout(root)
    # An open, current page.
    open_page = root / "vault/wiki/concepts/context-engineering.md"
    open_page.write_text(
        "---\n"
        "page_type: concept\n"
        "domain: research\n"
        "claim_ids: [\"c1\"]\n"
        "review_state: approved\n"
        "t_valid_from: 2026-04-01T00:00:00+00:00\n"
        "t_valid_to: null\n"
        "confidence: high\n"
        "---\n\n"
        "# Context Engineering\n\n"
        "Context engineering treats prompts and retrieval as a tactical wiki "
        "the agent edits between turns. Sources: Karpathy LLM Wiki gist.\n\n"
        "## Details\n\n"
        + "Body content paragraph. " * 60,
        encoding="utf-8",
    )
    # A superseded (closed) page that mentions same keyword.
    closed_page = root / "vault/wiki/concepts/old-context-pattern.md"
    closed_page.write_text(
        "---\n"
        "page_type: concept\n"
        "domain: research\n"
        "review_state: superseded\n"
        "t_valid_to: 2026-05-01T00:00:00+00:00\n"
        "superseded_by: vault/wiki/concepts/context-engineering.md\n"
        "---\n\n"
        "# Old context pattern\n\n"
        "Old narrative about context engineering before the wiki idea.\n",
        encoding="utf-8",
    )
    # A page closed by t_valid_to alone (no review_state change).
    past = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    expired_page = root / "vault/wiki/concepts/expired-context.md"
    expired_page.write_text(
        "---\n"
        "page_type: concept\n"
        "domain: research\n"
        "review_state: approved\n"
        f"t_valid_to: {past}\n"
        "---\n\n"
        "# Expired context fact\n\n"
        "Mentions context engineering once.\n",
        encoding="utf-8",
    )


class WikiSearchBitemporalFilterTests(unittest.TestCase):
    def test_default_skips_superseded_and_expired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_pages(root)
            results = knowledge_plane.search_wiki("context engineering", workspace=root)
            paths = [r.path for r in results]
            self.assertEqual(len(paths), 1)
            self.assertIn("context-engineering.md", paths[0])

    def test_include_closed_surfaces_everything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_pages(root)
            results = knowledge_plane.search_wiki(
                "context", workspace=root, include_closed=True,
            )
            self.assertEqual(len(results), 3)

    def test_results_carry_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_pages(root)
            results = knowledge_plane.search_wiki("context engineering", workspace=root)
            self.assertEqual(results[0].frontmatter.get("page_type"), "concept")
            self.assertEqual(results[0].frontmatter.get("review_state"), "approved")


class ContextPackTierTests(unittest.TestCase):
    def test_minimal_tier_clears_snippet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_pages(root)
            pack = knowledge_plane.build_context_pack(
                root, query="context engineering", domain="research", tier="minimal",
            )
            self.assertEqual(pack.tier, "minimal")
            self.assertEqual(pack.char_budget, 0)
            self.assertEqual(pack.wiki_results[0].snippet, "")
            self.assertEqual(pack.wiki_results[0].body_excerpt, "")
            # frontmatter still carried — the surface of "minimal" tier.
            self.assertIn("page_type", pack.wiki_results[0].frontmatter)

    def test_standard_tier_keeps_snippet_no_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_pages(root)
            pack = knowledge_plane.build_context_pack(
                root, query="context engineering", domain="research", tier="standard",
            )
            self.assertEqual(pack.tier, "standard")
            self.assertTrue(pack.wiki_results[0].snippet)
            self.assertEqual(pack.wiki_results[0].body_excerpt, "")

    def test_expanded_tier_loads_body_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_pages(root)
            pack = knowledge_plane.build_context_pack(
                root, query="context engineering", domain="research", tier="expanded",
            )
            self.assertEqual(pack.tier, "expanded")
            body = pack.wiki_results[0].body_excerpt
            self.assertTrue(body)
            # Frontmatter section should be stripped.
            self.assertFalse(body.startswith("---"))
            self.assertLessEqual(len(body), 8000)

    def test_invalid_tier_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_pages(root)
            with self.assertRaises(ValueError):
                knowledge_plane.build_context_pack(
                    root, query="x", domain="research", tier="ultra",
                )

    def test_total_chars_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_pages(root)
            pack_min = knowledge_plane.build_context_pack(
                root, query="context engineering", domain="research", tier="minimal",
            )
            pack_exp = knowledge_plane.build_context_pack(
                root, query="context engineering", domain="research", tier="expanded",
            )
            self.assertLessEqual(pack_min.total_chars, pack_exp.total_chars)

    def test_pack_persistence_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_pages(root)
            pack = knowledge_plane.build_context_pack(
                root, query="context engineering", domain="research",
                tier="expanded", persist=True,
            )
            saved = json.loads(Path(pack.path).read_text(encoding="utf-8"))
            self.assertEqual(saved["tier"], "expanded")
            self.assertGreater(saved["total_chars"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
