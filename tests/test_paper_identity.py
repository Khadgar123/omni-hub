"""Cross-source paper dedup + conference-accepted-list crawl (v0.49).

Covers the operator's scenario: an arXiv preprint is already in the repo;
its ACCEPTED version later arrives (proceedings DOI + OpenReview thread).
The identity engine must fold all of them into ONE paper instead of
duplicating, using the Semantic Scholar / OpenAlex record (which carries
both ArXiv and DOI) as the cross-walk bridge.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.retrieval.base import RetrievalRecord
from omni_hub.retrieval.openreview import OpenReviewSource
from omni_hub.retrieval.paper_identity import (
    merge_papers,
    normalized_title,
    paper_identity_keys,
)


def _rec(source, *, canonical="", metadata=None, title="A Paper", score=0.0):
    return RetrievalRecord(
        source=source, title=title, canonical_id=canonical,
        score=score, metadata=metadata or {},
    )


class IdentityKeyTests(unittest.TestCase):
    def test_arxiv_version_stripped(self) -> None:
        r = _rec("arxiv", canonical="arxiv:2401.00001v3",
                 metadata={"arxiv_base_id": "2401.00001"})
        self.assertIn("arxiv:2401.00001", paper_identity_keys(r))

    def test_s2_bridge_carries_both_ids(self) -> None:
        r = _rec("semantic_scholar", canonical="doi:10.1/x",
                 metadata={"external_ids": {"ArXiv": "2401.00001", "DOI": "10.1/x"}})
        keys = paper_identity_keys(r)
        self.assertIn("arxiv:2401.00001", keys)
        self.assertIn("doi:10.1/x", keys)

    def test_doi_url_normalised(self) -> None:
        r = _rec("crossref", canonical="doi:10.1/x",
                 metadata={"doi": "https://doi.org/10.1/X"})
        self.assertIn("doi:10.1/x", paper_identity_keys(r))


class MergeTests(unittest.TestCase):
    def _paper_records(self):
        arxiv = _rec("arxiv", canonical="arxiv:2401.00001",
                     metadata={"arxiv_base_id": "2401.00001"}, score=1.0)
        s2 = _rec("semantic_scholar", canonical="doi:10.1/x", score=50.0,
                  metadata={"external_ids": {"ArXiv": "2401.00001", "DOI": "10.1/x"},
                            "venue": "ICLR 2026"})
        crossref = _rec("crossref", canonical="doi:10.1/x",
                        metadata={"doi": "10.1/x"}, score=5.0)
        other = _rec("arxiv", canonical="arxiv:2402.99999",
                     metadata={"arxiv_base_id": "2402.99999"}, title="Other")
        return arxiv, s2, crossref, other

    def test_preprint_and_accepted_fold_into_one(self) -> None:
        arxiv, s2, crossref, other = self._paper_records()
        merged = merge_papers([arxiv, s2, crossref, other])
        self.assertEqual(len(merged), 2)  # one paper + one unrelated
        paper = max(merged, key=lambda r: len(r.metadata.get("merged_sources", [])))
        self.assertEqual(
            set(paper.metadata["merged_sources"]),
            {"arxiv", "semantic_scholar", "crossref"},
        )
        # accepted/published fields backfilled onto the merged record
        self.assertEqual(paper.metadata["venue"], "ICLR 2026")
        self.assertIn("arxiv:2401.00001", paper.metadata["merged_ids"])
        self.assertIn("doi:10.1/x", paper.metadata["merged_ids"])

    def test_openreview_accepted_folds_via_doi_bridge(self) -> None:
        # The exact "防止重复" case: preprint already present; the accepted
        # OpenReview record (carrying the DOI) + the S2 bridge fold it in.
        arxiv, s2, _crossref, _other = self._paper_records()
        openreview = _rec("openreview", canonical="openreview:abc",
                          metadata={"forum_id": "abc", "doi": "10.1/x",
                                    "accepted": True, "venue": "ICLR 2026"})
        merged = merge_papers([arxiv, s2, openreview])
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0].metadata.get("accepted"))

    def test_distinct_papers_not_merged(self) -> None:
        a = _rec("arxiv", canonical="arxiv:2401.00001",
                 metadata={"arxiv_base_id": "2401.00001"})
        b = _rec("arxiv", canonical="arxiv:2401.00002",
                 metadata={"arxiv_base_id": "2401.00002"})
        self.assertEqual(len(merge_papers([a, b])), 2)


_FAKE_VENUE = {
    "notes": [
        {
            "forum": "fABC", "id": "fABC",
            "content": {
                "title": {"value": "Accepted Paper One"},
                "abstract": {"value": "abstract one"},
                "venue": {"value": "ICLR 2026 Poster"},
                "doi": {"value": "10.1/accepted1"},
                "authors": {"value": ["Jane Doe"]},
            },
        }
    ]
}


class VenueCrawlTests(unittest.TestCase):
    def test_venue_submissions_yields_accepted_list(self) -> None:
        with patch(
            "omni_hub.retrieval.openreview.http_get_json", return_value=_FAKE_VENUE,
        ):
            recs = OpenReviewSource().venue_submissions("ICLR.cc/2026/Conference")
        self.assertEqual(len(recs), 1)
        md = recs[0].metadata
        self.assertTrue(md["accepted"])
        self.assertEqual(md["venueid"], "ICLR.cc/2026/Conference")
        self.assertEqual(recs[0].canonical_id, "openreview:fABC")
        # carries the DOI so identity resolution can bridge to the preprint
        self.assertEqual(md["doi"], "10.1/accepted1")


class DictRecordTests(unittest.TestCase):
    """The wiki-ingest path passes plain dicts (evidence.jsonl), not
    RetrievalRecord — merge_papers must fold those too (the 3b ingest fold)."""

    def test_merge_papers_on_dict_records(self) -> None:
        arxiv = {"source": "arxiv", "canonical_id": "arxiv:2401.00001",
                 "title": "P", "snippet": "a",
                 "metadata": {"arxiv_base_id": "2401.00001"}}
        s2 = {"source": "semantic_scholar", "canonical_id": "doi:10.1/x",
              "title": "P", "snippet": "b", "score": 50.0,
              "metadata": {"external_ids": {"ArXiv": "2401.00001", "DOI": "10.1/x"},
                           "venue": "ICLR 2026"}}
        other = {"source": "arxiv", "canonical_id": "arxiv:2402.99999",
                 "title": "Q", "metadata": {"arxiv_base_id": "2402.99999"}}
        merged = merge_papers([arxiv, s2, other])
        self.assertEqual(len(merged), 2)
        paper = max(merged, key=lambda r: len(r["metadata"].get("merged_sources", [])))
        self.assertEqual(set(paper["metadata"]["merged_sources"]),
                         {"arxiv", "semantic_scholar"})
        self.assertEqual(paper["metadata"]["venue"], "ICLR 2026")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
