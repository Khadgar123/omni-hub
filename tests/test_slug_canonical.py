"""Slug canonicalization invariants (v0.47 refactor, P0 step 2).

The knowledge base keeps a deliberate two-name split: the Python domain
*key*/`DomainSchema.slug` is underscore (``ai_progress``) while the
on-disk folder is hyphen (``ai-progress``), produced by
``knowledge_plane._slugify``.  The bug these tests guard against is a
producer writing the raw underscore key as a directory name, which forks
``vault/{raw,evidence}`` into ``ai_progress/`` vs ``ai-progress/`` twins
that ``wiki-ingest --domain`` cannot reconcile.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from omni_hub.domain_schemas import DOMAIN_SCHEMAS
from omni_hub.knowledge_plane import _slugify


def _load_seed_module():
    path = REPO_ROOT / "scripts" / "seed_wikipedia_minimal.py"
    spec = importlib.util.spec_from_file_location("seed_wikipedia_minimal", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SlugCanonicalTests(unittest.TestCase):
    def test_slugify_idempotent_and_underscore_to_hyphen(self) -> None:
        for raw in ("ai_progress", "us_policy", "international_relations", "research"):
            s = _slugify(raw)
            self.assertNotIn("_", s)
            self.assertEqual(s, raw.replace("_", "-"))
            self.assertEqual(_slugify(s), s, "slugify must be idempotent")

    def test_every_domain_schema_folder_is_canonical_slug(self) -> None:
        for key, schema in DOMAIN_SCHEMAS.items():
            self.assertEqual(schema.slug, key, f"{key}: dict key must equal schema.slug")
            self.assertEqual(
                _slugify(schema.slug),
                schema.folder,
                f"{key}: folder {schema.folder!r} must equal _slugify(slug)",
            )
            self.assertNotIn("_", schema.folder)

    def test_live_vault_dirs_have_no_slug_drift(self) -> None:
        for sub in ("raw", "evidence"):
            base = REPO_ROOT / "vault" / sub
            if not base.is_dir():
                continue
            for d in base.iterdir():
                if not d.is_dir():
                    continue
                self.assertEqual(
                    d.name,
                    _slugify(d.name),
                    f"vault/{sub}/{d.name} is not canonical (slug drift regressed)",
                )

    def test_seed_domain_writes_canonical_slug_dir(self) -> None:
        # _seed_domain must write the hyphen folder, never the underscore key.
        import omni_hub.retrieval.wikipedia as wp_mod
        from omni_hub.retrieval.base import RetrievalRecord

        class _FakeWP:
            name = "wikipedia"

            def retrieve(self, query, *, limit=1, domain=""):
                return [
                    RetrievalRecord(
                        source="wikipedia",
                        title=query,
                        url=f"https://en.wikipedia.org/wiki/{query}",
                        snippet="stub body",
                        canonical_id=f"wp:{query}",
                    )
                ]

        orig = wp_mod.WikipediaSource
        wp_mod.WikipediaSource = _FakeWP  # type: ignore[assignment]
        try:
            seed = _load_seed_module()
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                written = seed._seed_domain(root, "ai_progress", ["Transformer"], 1)
                self.assertEqual(written, 1)
                self.assertTrue(
                    (root / "vault" / "evidence" / "ai-progress").is_dir(),
                    "evidence must land under the hyphen slug",
                )
                self.assertFalse(
                    (root / "vault" / "evidence" / "ai_progress").exists(),
                    "underscore twin directory must never be created",
                )
                self.assertTrue((root / "vault" / "raw" / "ai-progress").is_dir())
        finally:
            wp_mod.WikipediaSource = orig  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
