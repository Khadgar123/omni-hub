from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.harness import graphiti_bridge, redundancy


def _seed_sqlite(db_path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE documents (source_path TEXT PRIMARY KEY, "
            "title TEXT, summary TEXT, updated_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO documents VALUES (?, ?, ?, ?)",
            rows,
        )


class GraphitiBridgeFallbackTests(unittest.TestCase):
    def test_local_backend_lists_documents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mem.sqlite3"
            _seed_sqlite(db, [
                ("vault/a.md", "Topic A", "Summary A", "2026-05-20T10:00:00+00:00"),
                ("vault/b.md", "Topic B", "Summary B", "2026-05-21T10:00:00+00:00"),
            ])
            backend = graphiti_bridge.LocalSQLiteBackend(db)
            docs = backend.list_documents()
            self.assertEqual(len(docs), 2)
            self.assertEqual({d.title for d in docs}, {"Topic A", "Topic B"})

    def test_local_backend_search_matches_title_or_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mem.sqlite3"
            _seed_sqlite(db, [
                ("a", "ccLoad routing", "cost-aware route selection", "2026-05-01"),
                ("b", "Metapi balance", "low-balance alerts", "2026-05-02"),
            ])
            backend = graphiti_bridge.LocalSQLiteBackend(db)
            hits = backend.search("ccLoad")
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].title, "ccLoad routing")

    def test_local_backend_search_returns_empty_for_missing_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            backend = graphiti_bridge.LocalSQLiteBackend(Path(tmp) / "nope.sqlite3")
            self.assertEqual(backend.search("anything"), [])


class RedundancyScanTests(unittest.TestCase):
    def _scan_with(self, rows: list[tuple[str, str, str, str]], **kw) -> redundancy.RedundancyScanReport:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        db = root / "mem.sqlite3"
        _seed_sqlite(db, rows)
        proposals_jsonl = root / "proposals.jsonl"
        return redundancy.scan(
            db_path=db,
            prefer_backend="local",
            write_to=proposals_jsonl,
            **kw,
        )

    def test_detects_exact_duplicate_title_and_summary(self) -> None:
        report = self._scan_with([
            ("a", "Same Title", "Same body.", "2026-05-20"),
            ("b", "Same Title", "Same body.", "2026-05-20"),
            ("c", "Other", "Other.", "2026-05-20"),
        ])
        self.assertEqual(report.counts_by_kind().get("duplicate", 0), 1)

    def test_detects_conflict_when_summary_differs(self) -> None:
        report = self._scan_with([
            ("a", "Same Title", "Body version one.", "2026-05-20"),
            ("b", "Same Title", "Body version two.", "2026-05-20"),
        ])
        self.assertEqual(report.counts_by_kind().get("conflict", 0), 1)

    def test_detects_stale_when_outside_window(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        report = self._scan_with(
            [
                ("a", "Old", "Old", old),
                ("b", "Recent", "Recent", recent),
            ],
            freshness_days=365,
        )
        self.assertEqual(report.counts_by_kind().get("stale", 0), 1)

    def test_detects_low_signal_summary(self) -> None:
        report = self._scan_with(
            [
                ("a", "Vague", "In recent years, numerous studies have shown comprehensive results. Obviously this is significant.", "2026-05-20"),
                ("b", "Solid", "Our experiment reduced latency by 30% [1]. The cache hit rate rose to 92% [2].", "2026-05-20"),
            ],
            min_low_signal_ratio=0.3,
        )
        self.assertEqual(report.counts_by_kind().get("low_signal", 0), 1)

    def test_scan_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mem.sqlite3"
            _seed_sqlite(db, [
                ("a", "T", "A", "2026-05-20"),
                ("b", "T", "A", "2026-05-20"),
            ])
            out = root / "proposals.jsonl"
            redundancy.scan(db_path=db, prefer_backend="local", write_to=out)
            self.assertTrue(out.exists())
            lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertGreaterEqual(len(lines), 1)
            self.assertTrue(any(p["kind"] == "duplicate" for p in lines))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
