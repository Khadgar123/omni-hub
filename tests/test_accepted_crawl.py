"""Conference accepted-paper crawl orchestrator + venue config (v0.49).

Verifies the resmax-style port: config/venues.yaml is well-formed, entries
expand per year, and the crawl folds cross-source duplicates (an OpenReview
accepted record + an OpenAlex record for the same DOI become one paper).
Network-free (fetchers injected).
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from omni_hub.retrieval.base import RetrievalRecord


def _load_crawler():
    spec = importlib.util.spec_from_file_location(
        "crawl_accepted_under_test", _ROOT / "scripts" / "crawl_accepted.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _rec(source, *, canonical="", metadata=None, title="P"):
    return RetrievalRecord(
        source=source, title=title, canonical_id=canonical, metadata=metadata or {},
    )


class VenueConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import yaml
        cls.mod = _load_crawler()
        with (_ROOT / "config" / "venues.yaml").open(encoding="utf-8") as f:
            cls.cfg = yaml.safe_load(f)

    def test_venues_well_formed(self) -> None:
        venues = self.cfg.get("venues") or {}
        self.assertGreaterEqual(len(venues), 5)
        for vid, e in venues.items():
            self.assertTrue(e.get("display"), f"{vid} missing display")
            self.assertIn(e.get("method"), ("openreview", "openalex"), f"{vid} bad method")
            self.assertTrue(e.get("years"), f"{vid} missing years")
            if e["method"] == "openreview":
                self.assertIn("{year}", e.get("venueid_template", ""),
                              f"{vid} openreview needs venueid_template with {{year}}")
            else:
                self.assertTrue(
                    e.get("venue_name") or e.get("openalex_source_id"),
                    f"{vid} openalex needs venue_name or openalex_source_id",
                )

    def test_expand_entries_flattens_years(self) -> None:
        venues = self.cfg.get("venues") or {}
        entries = self.mod.expand_entries(venues, ["iclr"])
        years = {y for _vid, _e, y in entries}
        self.assertEqual({v for v, _, _ in entries}, {"iclr"})
        self.assertTrue(years.issubset({2024, 2025, 2026}))
        self.assertGreaterEqual(len(entries), 2)


class CrawlDedupTests(unittest.TestCase):
    def test_cross_source_duplicates_fold(self) -> None:
        mod = _load_crawler()
        entries = [
            ("iclr", {"method": "openreview", "venueid_template": "ICLR.cc/{year}/Conference"}, 2025),
            ("cvpr", {"method": "openalex", "venue_name": "CVPR"}, 2025),
        ]

        def fake_openreview(venueid):
            return [
                _rec("openreview", canonical="openreview:a",
                     metadata={"forum_id": "a", "doi": "10.1/x", "accepted": True}),
                _rec("openreview", canonical="openreview:b",
                     metadata={"forum_id": "b", "accepted": True}),
            ]

        def fake_openalex(**kw):
            # same paper as openreview:a (shares DOI 10.1/x)
            return [_rec("openalex", canonical="doi:10.1/x", metadata={"doi": "10.1/x"})]

        raw, merged = mod.crawl(
            entries, limit=10,
            openreview_fetch=fake_openreview, openalex_fetch=fake_openalex,
        )
        self.assertEqual(len(raw), 3)
        self.assertEqual(len(merged), 2)  # the shared-DOI pair folded into one


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
