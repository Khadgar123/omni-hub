"""Xiaohongshu MediaCrawler-snapshot reader (v0.49 broker switch).

The XHS connector now reads MediaCrawler JSON snapshots (primary) and only
falls back to the legacy `xhs` CLI when no snapshot dir exists (that fallback
is covered by tests/test_retrieval.py::XHSBridgeTests).  Network-free.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.retrieval import xhs as XHS
from omni_hub.retrieval.xhs import XiaohongshuSource

_FIXTURE = [
    {
        "note_id": "n1", "title": "红烧肉做法", "desc": "详细步骤分享",
        "nickname": "chef", "note_url": "https://www.xiaohongshu.com/explore/n1",
        "liked_count": "1.2万", "comment_count": 120, "collected_count": "3400",
        "tag_list": ["美食", "家常菜"],
    },
    {
        "note_id": "n2", "title": "旅行攻略", "desc": "云南五日",
        "nickname": "tripper", "liked_count": 88, "tag_list": ["旅行"],
    },
]


class CountParseTests(unittest.TestCase):
    def test_count_variants(self) -> None:
        self.assertEqual(XHS._count("1.2万"), 12000.0)
        self.assertEqual(XHS._count("3.4k"), 3400.0)
        self.assertEqual(XHS._count(88), 88.0)
        self.assertEqual(XHS._count(""), 0.0)
        self.assertEqual(XHS._count("999+"), 999.0)


class SnapshotReadTests(unittest.TestCase):
    def _src(self, payload):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        (d / "search_contents_1.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8",
        )
        return XiaohongshuSource(data_dir=d)

    def test_check_ok_with_snapshots(self) -> None:
        src = self._src(_FIXTURE)
        status, _ = src.check()
        self.assertEqual(status, "ok")

    def test_retrieve_filters_and_maps(self) -> None:
        src = self._src(_FIXTURE)
        recs = src.retrieve("红烧肉")
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r.canonical_id, "xhs:note:n1")
        self.assertEqual(r.score, 12000.0)          # "1.2万" parsed
        self.assertEqual(r.metadata["author"], "chef")
        self.assertEqual(r.metadata["lang"], "zh")
        self.assertEqual(r.metadata["snapshot"], "mediacrawler")

    def test_retrieve_tag_match_and_miss(self) -> None:
        src = self._src(_FIXTURE)
        self.assertEqual(len(src.retrieve("旅行")), 1)      # matches tag/title
        self.assertEqual(len(src.retrieve("zzznomatch")), 0)

    def test_dict_envelope(self) -> None:
        src = self._src({"data": _FIXTURE})
        self.assertGreaterEqual(len(src.retrieve("美食")), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
