"""Offline tests for hybrid wiki search (sqlite-vec KNN + RRF fusion).

Uses a deterministic fake embedder so the whole suite runs with no model
download and no network. Skips the KNN tests if the local sqlite build lacks
the sqlite-vec extension (mirrors the FTS5 skip pattern).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.wiki_vec import WikiVecIndex, rrf_fuse, sqlite_vec_available


def _fake_embed(texts: list[str]) -> list[list[float]]:
    # 3-dim bag-of-keyword vector, L2-normalized — deterministic, offline.
    # Normalization makes L2 distance rank by direction (cosine-equivalent),
    # which is how real sentence embedders (bge-m3) behave; without it, raw
    # term-count magnitude would dominate sqlite-vec's default L2 metric.
    import math

    out: list[list[float]] = []
    for t in texts:
        v = [
            float(t.lower().count("cat")),
            float(t.lower().count("dog")),
            float(t.lower().count("fish")),
        ]
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / norm for x in v])
    return out


class RRFFusionTests(unittest.TestCase):
    def test_doc_in_both_lists_ranks_first(self) -> None:
        fused = rrf_fuse(["x.md", "a.md", "b.md"], ["a.md", "y.md"], limit=5)
        self.assertEqual(fused[0], "a.md")

    def test_limit_truncates(self) -> None:
        fused = rrf_fuse(["a.md", "b.md", "c.md"], ["d.md"], limit=2)
        self.assertEqual(len(fused), 2)

    def test_deterministic_tie_break(self) -> None:
        # disjoint lists, equal scores -> lexical-first order preserved
        a = rrf_fuse(["a.md"], ["b.md"], limit=2)
        b = rrf_fuse(["a.md"], ["b.md"], limit=2)
        self.assertEqual(a, b)
        self.assertEqual(a[0], "a.md")

    def test_empty_inputs(self) -> None:
        self.assertEqual(rrf_fuse([], [], limit=5), [])


@unittest.skipUnless(sqlite_vec_available(), "sqlite-vec extension unavailable")
class WikiVecIndexTests(unittest.TestCase):
    def _index(self, root: Path) -> WikiVecIndex:
        idx = WikiVecIndex(root, embed_fn=_fake_embed)
        idx.rebuild([("a.md", "cat cat cat"), ("b.md", "dog dog"), ("c.md", "fish")])
        return idx

    def test_rebuild_reports_dim_and_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = WikiVecIndex(root, embed_fn=_fake_embed).rebuild(
                [("a.md", "cat"), ("b.md", "dog")]
            )
            self.assertTrue(out["ok"])
            self.assertEqual(out["indexed"], 2)
            self.assertEqual(out["dim"], 3)

    def test_knn_finds_nearest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hits = self._index(root).search("cat please", limit=2)
            self.assertTrue(hits)
            self.assertEqual(hits[0][0], "a.md")

    def test_search_failsoft_without_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # no rebuild() -> no db file -> empty, never raises
            self.assertEqual(
                WikiVecIndex(root, embed_fn=_fake_embed).search("cat"), []
            )


if __name__ == "__main__":
    unittest.main()
