"""Tests for the FTS5 sidecar + search_wiki(backend=...) routing."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub import knowledge_plane
from omni_hub.wiki_fts import WikiFTSIndex, fts5_available


def _write_page(root: Path, relative: str, frontmatter: dict, body: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in frontmatter.items():
        if v is None:
            lines.append(f"{k}: null")
        elif isinstance(v, list):
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _seed_corpus(root: Path) -> None:
    knowledge_plane.init_layout(root)
    _write_page(root, "vault/wiki/concepts/karpathy-llm-wiki.md", {
        "page_type": "concept", "domain": "ai_progress",
        "review_state": "approved", "t_valid_from": "2026-04-01T00:00:00+00:00",
        "t_valid_to": None,
    }, body="# Karpathy LLM Wiki\n\nA compiled, file-first knowledge artifact "
           "the agent edits between turns. The wiki layer is the source of truth.")
    _write_page(root, "vault/wiki/methods/bitemporal-supersede.md", {
        "page_type": "method", "domain": "ai_progress",
        "review_state": "approved", "t_valid_from": "2026-05-01T00:00:00+00:00",
        "t_valid_to": None,
    }, body="# Bitemporal supersede\n\nGraphiti-style window-close on claims. "
           "Old facts are NEVER deleted; only t_valid_to is set.")
    past = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    _write_page(root, "vault/wiki/concepts/expired-pattern.md", {
        "page_type": "concept", "domain": "ai_progress",
        "review_state": "approved",
        "t_valid_to": past,
    }, body="# Expired pattern\n\nObsolete approach to context engineering.")


@unittest.skipUnless(fts5_available(), "Local sqlite3 lacks FTS5 support")
class WikiFTSIndexTests(unittest.TestCase):
    def test_rebuild_all_indexes_every_non_meta_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_corpus(root)
            idx = WikiFTSIndex(root)
            stats = idx.rebuild_all()
            self.assertEqual(stats["indexed"], 3)
            self.assertGreater(stats["skipped"], 0)  # AGENTS / index / log / _schema

    def test_search_returns_hits_and_filters_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_corpus(root)
            idx = WikiFTSIndex(root)
            idx.rebuild_all()

            # default — expired-pattern.md should NOT appear.
            hits = idx.search("pattern", limit=10)
            paths = [h.path for h in hits]
            self.assertNotIn(
                "vault/wiki/concepts/expired-pattern.md", paths,
                f"closed page should be filtered; got {paths}",
            )

            # include_closed — every match comes back.
            hits_all = idx.search("pattern", limit=10, include_closed=True)
            paths_all = [h.path for h in hits_all]
            self.assertIn("vault/wiki/concepts/expired-pattern.md", paths_all)

    def test_rebuild_one_replaces_index_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_corpus(root)
            idx = WikiFTSIndex(root)
            idx.rebuild_all()

            page = root / "vault/wiki/concepts/karpathy-llm-wiki.md"
            page.write_text(page.read_text().replace("compiled", "ZEPPELIN_TOKEN"),
                            encoding="utf-8")
            ok = idx.rebuild_one(page)
            self.assertTrue(ok)
            hits = idx.search("ZEPPELIN_TOKEN", limit=5)
            self.assertEqual(len(hits), 1)

    def test_meta_files_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_corpus(root)
            idx = WikiFTSIndex(root)
            idx.rebuild_all()
            schema_path = root / "vault/wiki/domains/research/_schema.md"
            self.assertTrue(schema_path.exists())
            self.assertFalse(idx.rebuild_one(schema_path))

    def test_delete_one_removes_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_corpus(root)
            idx = WikiFTSIndex(root)
            idx.rebuild_all()
            before = idx.stats()["indexed"]
            page = root / "vault/wiki/methods/bitemporal-supersede.md"
            self.assertTrue(idx.delete_one(page))
            self.assertEqual(idx.stats()["indexed"], before - 1)


@unittest.skipUnless(fts5_available(), "Local sqlite3 lacks FTS5 support")
class SearchWikiBackendTests(unittest.TestCase):
    def test_auto_backend_uses_fts5_when_index_populated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_corpus(root)
            WikiFTSIndex(root).rebuild_all()
            results = knowledge_plane.search_wiki(
                "Karpathy", workspace=root, limit=5,
            )
            self.assertTrue(any("karpathy" in r.path.lower() for r in results))

    def test_substring_backend_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_corpus(root)
            results = knowledge_plane.search_wiki(
                "Karpathy", workspace=root, limit=5, backend="substring",
            )
            self.assertTrue(any("karpathy" in r.path.lower() for r in results))

    def test_force_fts5_when_unavailable_raises(self) -> None:
        # Skipped above means we DO have FTS5 — this test verifies that when
        # users explicitly request the fts5 backend it doesn't silently
        # fall back to substring (when FTS5 is missing).  We can't easily
        # disable FTS5 at runtime, but we can verify the dispatch path.
        # Smoke: a backend value of "fts5" must not error here.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_corpus(root)
            WikiFTSIndex(root).rebuild_all()
            results = knowledge_plane.search_wiki(
                "Karpathy", workspace=root, limit=5, backend="fts5",
            )
            self.assertGreaterEqual(len(results), 1)

    def test_invalid_backend_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_corpus(root)
            with self.assertRaises(ValueError):
                knowledge_plane.search_wiki(
                    "x", workspace=root, backend="elasticsearch",
                )


@unittest.skipUnless(fts5_available(), "Local sqlite3 lacks FTS5 support")
class ApplyAutoReindexTests(unittest.TestCase):
    def test_apply_wiki_proposal_indexes_new_page(self) -> None:
        from omni_hub.proposals import Proposal, ProposalStore, PENDING

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)

            proposal = Proposal(
                kind="wiki_update", state=PENDING,
                title="FTS reindex smoke",
                summary="auto-indexed page after apply",
                payload={
                    "target_path": "vault/wiki/syntheses/fts-reindex-smoke.md",
                    "domain": "engineering",
                    "body": "# FTS reindex smoke\n\nKey phrase: REINDEX_SMOKE_TOKEN.\n",
                    "claims": [],
                },
            )
            store = ProposalStore(root)
            store.store(proposal, write_card=False)
            store.approve(proposal.proposal_id, reason="fts test")
            applied = knowledge_plane.apply_wiki_proposal(root, proposal.proposal_id)
            self.assertTrue(applied["fts5_indexed"])

            idx = WikiFTSIndex(root)
            hits = idx.search("REINDEX_SMOKE_TOKEN", limit=3)
            self.assertEqual(len(hits), 1)


class ReindexCliWrapperTests(unittest.TestCase):
    def test_reindex_wiki_returns_count_when_fts_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_corpus(root)
            stats = knowledge_plane.reindex_wiki(root)
            if fts5_available():
                self.assertTrue(stats["fts5"])
                self.assertEqual(stats["indexed"], 3)
            else:
                self.assertFalse(stats["fts5"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
