"""Tests for the 12 per-domain wiki sub-schemas + materialise + per-domain
stale_after_days lookup."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub import knowledge_plane
from omni_hub.domain_schemas import (
    DOMAIN_SCHEMA_VERSION,
    DOMAIN_SCHEMAS,
    get_stale_after_days,
    materialise_all,
    render_domain_schema,
)


class DomainSchemaCoverageTests(unittest.TestCase):
    def test_all_twelve_domains_registered(self) -> None:
        expected = {
            "research", "engineering", "photography", "fashion",
            "chat_relationships", "finance", "policy",
            "international_relations", "ai_progress", "agent_systems",
            "social_en", "social_zh",
        }
        self.assertEqual(set(DOMAIN_SCHEMAS.keys()), expected)

    def test_each_schema_has_non_empty_position_and_lint_hints(self) -> None:
        for slug, schema in DOMAIN_SCHEMAS.items():
            with self.subTest(slug=slug):
                self.assertTrue(schema.position, f"{slug} missing position")
                # social_zh might have empty authoritative_sources fine,
                # but lint_hints must guide the user.
                if slug not in {"chat_relationships"}:
                    self.assertTrue(schema.lint_hints, f"{slug} missing lint_hints")

    def test_render_includes_schema_version_marker(self) -> None:
        body = render_domain_schema(DOMAIN_SCHEMAS["research"])
        self.assertIn(f"schema_version: {DOMAIN_SCHEMA_VERSION}", body)
        self.assertIn("ResearchFlow", body)
        self.assertIn("PaperBite", body)


class StaleAfterDaysLookupTests(unittest.TestCase):
    def test_finance_short_window(self) -> None:
        self.assertEqual(get_stale_after_days("finance"), 30)

    def test_research_long_window(self) -> None:
        self.assertEqual(get_stale_after_days("research"), 730)

    def test_international_relations_week_window(self) -> None:
        self.assertEqual(get_stale_after_days("international_relations"), 7)

    def test_unknown_domain_returns_default(self) -> None:
        self.assertEqual(get_stale_after_days("totally_made_up"), 30)
        self.assertEqual(get_stale_after_days("totally_made_up", default=99), 99)


class MaterialiseTests(unittest.TestCase):
    def test_materialise_creates_all_twelve_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actions = materialise_all(root)
            self.assertEqual(set(actions.values()) - {"written"}, set())
            for slug, schema in DOMAIN_SCHEMAS.items():
                target = root / schema.folder / "_schema.md"
                self.assertTrue(target.exists(), f"{slug}: {target}")

    def test_materialise_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            materialise_all(root)
            actions2 = materialise_all(root)
            self.assertTrue(all(a == "unchanged" for a in actions2.values()))

    def test_materialise_refreshes_when_marker_outdated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schema = DOMAIN_SCHEMAS["research"]
            target_dir = root / schema.folder
            target_dir.mkdir(parents=True)
            (target_dir / "_schema.md").write_text(
                "---\nschema_version: v0.01-old\n---\n# Old\n",
                encoding="utf-8",
            )
            actions = materialise_all(root)
            self.assertEqual(actions["research"], "refreshed")
            body = (target_dir / "_schema.md").read_text(encoding="utf-8")
            self.assertIn(f"schema_version: {DOMAIN_SCHEMA_VERSION}", body)

    def test_materialise_preserves_hand_edits(self) -> None:
        """If the marker matches but content differs, hand edits win."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schema = DOMAIN_SCHEMAS["research"]
            target_dir = root / schema.folder
            target_dir.mkdir(parents=True)
            hand_body = (
                f"---\nschema_version: {DOMAIN_SCHEMA_VERSION}\n---\n"
                "# Hand-edited research schema\n"
            )
            (target_dir / "_schema.md").write_text(hand_body, encoding="utf-8")
            actions = materialise_all(root)
            self.assertEqual(actions["research"], "hand-edited")
            self.assertEqual(
                (target_dir / "_schema.md").read_text(encoding="utf-8"),
                hand_body,
            )


class InitLayoutMaterialisesDomainSchemasTests(unittest.TestCase):
    def test_init_layout_writes_domain_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = knowledge_plane.init_layout(root)
            actions = status.get("domain_schemas") or {}
            self.assertEqual(len(actions), 12)
            for slug, schema in DOMAIN_SCHEMAS.items():
                target = root / "vault" / "wiki" / "domains" / schema.folder / "_schema.md"
                self.assertTrue(target.exists(), f"{slug} schema missing")
            # social_en / social_zh folder paths got added to WIKI_DIRS.
            self.assertTrue((root / "vault" / "wiki" / "domains" / "social-en").is_dir())
            self.assertTrue((root / "vault" / "wiki" / "domains" / "social-zh").is_dir())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
