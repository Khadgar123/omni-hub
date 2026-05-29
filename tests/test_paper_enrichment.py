"""Offline tests for paper_enrichment — DBLP venue / HF artifacts / code score.

All HTTP is injected via ``fetch=`` so these run with zero network IO.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.retrieval import paper_enrichment as pe


def _router(routes):
    """Build a fake ``fetch`` that returns a canned payload per URL substring."""

    def fetch(url, *, params=None, headers=None, timeout=None):
        for needle, payload in routes.items():
            if needle in url:
                return payload(params) if callable(payload) else payload
        raise AssertionError(f"unexpected url {url}")

    return fetch


class DBLPVenueTests(unittest.TestCase):
    def test_strong_title_match_yields_accepted_venue(self) -> None:
        fetch = _router({
            "dblp.org": {"result": {"hits": {"hit": [
                {"info": {"title": "ACE: Agentic Context Engineering",
                          "venue": "ICLR", "type": "Conference and Workshop Papers",
                          "year": "2026"}},
            ]}}},
        })
        out = pe.fetch_dblp_venue("ACE: Agentic Context Engineering", fetch=fetch)
        self.assertEqual(out["venue"], "ICLR")
        self.assertEqual(out["venue_type"], "conference")
        self.assertTrue(out["accepted"])
        self.assertEqual(out["year"], "2026")

    def test_no_title_match_returns_empty(self) -> None:
        fetch = _router({
            "dblp.org": {"result": {"hits": {"hit": [
                {"info": {"title": "An Unrelated Paper About Cooking",
                          "venue": "NeurIPS", "type": "Conference and Workshop Papers"}},
            ]}}},
        })
        self.assertEqual(pe.fetch_dblp_venue("ACE Agentic Context Engineering", fetch=fetch), {})

    def test_journal_type_detected(self) -> None:
        fetch = _router({
            "dblp.org": {"result": {"hits": {"hit": {
                "info": {"title": "A Survey of X", "venue": "JMLR",
                         "type": "Journal Articles", "year": "2025"}}}}},
        })
        out = pe.fetch_dblp_venue("A Survey of X", fetch=fetch)
        self.assertEqual(out["venue_type"], "journal")


class HFArtifactsTests(unittest.TestCase):
    def test_models_and_datasets_by_arxiv(self) -> None:
        fetch = _router({
            "api/models": [{"id": "org/ace-7b"}, {"id": "org/ace-13b"}],
            "api/datasets": [{"id": "org/ace-bench"}],
        })
        out = pe.fetch_hf_artifacts("arXiv:2510.04618v2", fetch=fetch)
        self.assertEqual(out["checkpoints"], ["org/ace-7b", "org/ace-13b"])
        self.assertEqual(out["datasets"], ["org/ace-bench"])

    def test_blank_arxiv_short_circuits(self) -> None:
        out = pe.fetch_hf_artifacts("", fetch=_router({}))
        self.assertEqual(out, {"checkpoints": [], "datasets": []})


class CodeCompletenessTests(unittest.TestCase):
    def _repo_fetch(self, names, readme=""):
        import base64
        return _router({
            "/contents": [{"name": n} for n in names],
            "/readme": {"content": base64.b64encode(readme.encode()).decode()},
            "/repos/": {  # the meta call (must be matched last via order? use distinct)
                "full_name": "foo/bar", "stargazers_count": 1234,
                "license": {"spdx_id": "MIT"}, "pushed_at": "2026-01-01T00:00:00Z",
                "archived": False,
            },
        })

    def test_full_checklist_scores_one(self) -> None:
        fetch = self._repo_fetch(
            ["requirements.txt", "train.py", "eval.py", "checkpoints"],
            readme="## Results\nReproduce our benchmark numbers.",
        )
        out = pe.score_code_completeness("foo/bar", fetch=fetch)
        self.assertEqual(out["score"], 1.0)
        self.assertTrue(all(out["axes"].values()))
        self.assertEqual(out["stars"], 1234)
        self.assertEqual(out["license"], "MIT")

    def test_partial_checklist(self) -> None:
        fetch = self._repo_fetch(["train.py"], readme="")
        out = pe.score_code_completeness("foo/bar", fetch=fetch)
        self.assertTrue(out["axes"]["training"])
        self.assertFalse(out["axes"]["dependencies"])
        self.assertLess(out["score"], 0.5)

    def test_missing_repo_returns_empty(self) -> None:
        fetch = _router({"/repos/": {}})  # no full_name -> not found
        self.assertEqual(pe.score_code_completeness("foo/bar", fetch=fetch), {})


class EnrichPaperTests(unittest.TestCase):
    def test_join_is_failsoft_and_complete(self) -> None:
        import base64

        def fetch(url, *, params=None, headers=None, timeout=None):
            if "dblp.org" in url:
                return {"result": {"hits": {"hit": [
                    {"info": {"title": "ACE", "venue": "ICLR",
                              "type": "Conference and Workshop Papers", "year": "2026"}}]}}}
            if "api/models" in url:
                return [{"id": "org/ace-7b"}]
            if "api/datasets" in url:
                return [{"id": "org/ace-data"}]
            if "/contents" in url:
                return [{"name": "requirements.txt"}, {"name": "train.py"}]
            if "/readme" in url:
                return {"content": base64.b64encode(b"results").decode()}
            if "/repos/" in url:
                return {"full_name": "org/ace", "stargazers_count": 9,
                        "license": {"spdx_id": "Apache-2.0"}, "pushed_at": "x"}
            raise AssertionError(url)

        d = pe.enrich_paper(
            arxiv_id="2510.04618", title="ACE",
            code_repos=["github.com/org/ace"], fetch=fetch,
        )
        self.assertEqual(d.venue, "ICLR")
        self.assertTrue(d.accepted)
        self.assertEqual(d.checkpoints, ["org/ace-7b"])
        self.assertEqual(d.datasets, ["org/ace-data"])
        self.assertEqual(d.code_repos, ["org/ace"])
        self.assertIn("score", d.code_completeness)
        self.assertEqual(d.errors, [])
        self.assertEqual(d.provenance["venue"], "dblp")
        self.assertEqual(d.provenance["artifacts"], "huggingface")
        self.assertEqual(d.provenance["code"], "github")

    def test_one_source_failing_isolates_to_errors(self) -> None:
        def fetch(url, *, params=None, headers=None, timeout=None):
            if "dblp.org" in url:
                raise RuntimeError("dblp down")
            if "api/models" in url or "api/datasets" in url:
                return []
            raise AssertionError(url)

        d = pe.enrich_paper(arxiv_id="2510.04618", title="ACE", fetch=fetch)
        self.assertEqual(d.venue, "")                  # dblp failed
        self.assertTrue(any("dblp" in e for e in d.errors))
        self.assertEqual(d.checkpoints, [])            # hf succeeded (empty)
        # a dossier is always returned, never raised
        self.assertEqual(d.arxiv_id, "2510.04618")


if __name__ == "__main__":
    unittest.main()
