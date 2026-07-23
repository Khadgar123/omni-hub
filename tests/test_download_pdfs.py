"""PDF corpus downloader — URL resolution + resumable/fail-soft download
(v0.49).  Network-free: fetch/sleep/clock are injected.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "download_pdfs_under_test", _ROOT / "scripts" / "download_pdfs.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


M = _load()

_VALID_PDF = b"%PDF-1.7\n" + b"x" * 1100 + b"\n%%EOF\n"


class ResolveUrlTests(unittest.TestCase):
    def test_arxiv_from_metadata(self) -> None:
        u = M.resolve_pdf_url({"canonical_id": "doi:10/x",
                               "metadata": {"arxiv_base_id": "2401.00001"}})
        self.assertEqual(u, "https://arxiv.org/pdf/2401.00001.pdf")

    def test_arxiv_from_canonical(self) -> None:
        u = M.resolve_pdf_url({"canonical_id": "arxiv:2401.00002", "metadata": {}})
        self.assertEqual(u, "https://arxiv.org/pdf/2401.00002.pdf")

    def test_open_access_pdf(self) -> None:
        u = M.resolve_pdf_url({"canonical_id": "doi:10/x",
                               "metadata": {"oa_pdf_url": "https://x/p.pdf"}})
        self.assertEqual(u, "https://x/p.pdf")

    def test_openreview(self) -> None:
        u = M.resolve_pdf_url({"canonical_id": "openreview:abc", "metadata": {"forum_id": "abc"}})
        self.assertEqual(u, "https://openreview.net/pdf?id=abc")

    def test_crossref_full_text_link(self) -> None:
        u = M.resolve_pdf_url({"canonical_id": "doi:10/x", "metadata": {
            "full_text_links": [{"url": "https://pub/x.pdf", "content_type": "application/pdf"}]}})
        self.assertEqual(u, "https://pub/x.pdf")

    def test_no_url(self) -> None:
        self.assertEqual(M.resolve_pdf_url({"canonical_id": "x", "metadata": {}}), "")


class DownloadTests(unittest.TestCase):
    def _records(self):
        return [
            {"canonical_id": "arxiv:2401.00001", "metadata": {"arxiv_base_id": "2401.00001"}},
            {"canonical_id": "openreview:abc", "metadata": {"forum_id": "abc"}},
            {"canonical_id": "nopdf", "metadata": {}},
        ]

    def test_download_and_resume(self) -> None:
        calls = []
        fake = lambda url: _VALID_PDF  # noqa: E731
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "papers"
            stats, manifest = M.download_corpus(
                self._records(), out, fetch=fake,
                sleep=lambda s: calls.append(s), clock=lambda: 0.0,
            )
            self.assertEqual(stats["downloaded"], 2)   # arxiv + openreview
            self.assertEqual(stats["no_url"], 1)
            self.assertEqual(len(list(out.glob("*.pdf"))), 2)
            # resume: second run skips both existing
            stats2, _ = M.download_corpus(
                self._records(), out, fetch=fake,
                sleep=lambda s: None, clock=lambda: 0.0,
            )
            self.assertEqual(stats2["downloaded"], 0)
            self.assertEqual(stats2["skipped_existing"], 2)

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "papers"
            stats, _ = M.download_corpus(
                self._records(), out, dry_run=True,
                fetch=lambda u: (_ for _ in ()).throw(AssertionError("must not fetch")),
            )
            self.assertEqual(stats["would_download"], 2)
            self.assertEqual(len(list(out.glob("*.pdf"))), 0)

    def test_fail_soft_on_fetch_error(self) -> None:
        def boom(url):
            raise OSError("network down")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "papers"
            stats, manifest = M.download_corpus(
                self._records(), out, fetch=boom,
                sleep=lambda s: None, clock=lambda: 0.0,
            )
            self.assertEqual(stats["failed"], 2)
            self.assertEqual(stats["downloaded"], 0)
            self.assertTrue(any(m["status"] == "failed" for m in manifest))


class IntegrityTests(unittest.TestCase):
    def test_valid_pdf_accepts_real_rejects_junk(self) -> None:
        self.assertTrue(M._valid_pdf(_VALID_PDF))
        self.assertFalse(M._valid_pdf(b""))
        self.assertFalse(M._valid_pdf(b"<html>error</html>"))      # HTML error page
        self.assertFalse(M._valid_pdf(b"%PDF-1.7 short no eof"))    # too small / no EOF
        self.assertFalse(M._valid_pdf(b"x" * 4000))                # no %PDF magic

    def test_corrupt_download_not_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "papers"
            recs = [{"canonical_id": "arxiv:2401.1",
                     "metadata": {"arxiv_base_id": "2401.1"}}]
            stats, _ = M.download_corpus(
                recs, out, fetch=lambda u: b"<html>429 rate limited</html>",
                sleep=lambda s: None, clock=lambda: 0.0,
            )
            self.assertEqual(stats["corrupt"], 1)
            self.assertEqual(stats["downloaded"], 0)
            self.assertEqual(len(list(out.glob("*.pdf"))), 0)  # corrupt not written

    def test_verify_corpus_flags_and_deletes_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "good.pdf").write_bytes(_VALID_PDF)
            (out / "bad.pdf").write_bytes(b"<html>nope</html>")
            v = M.verify_corpus(out, delete=False)
            self.assertEqual(v["checked"], 2)
            self.assertEqual(v["ok"], 1)
            self.assertEqual(v["corrupt"], 1)
            self.assertTrue((out / "bad.pdf").exists())
            v2 = M.verify_corpus(out, delete=True)
            self.assertEqual(v2["removed"], 1)
            self.assertFalse((out / "bad.pdf").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
