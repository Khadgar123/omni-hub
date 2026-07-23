"""v0.46 paper-artifact connectors: OpenReview, GitHub, Hugging Face Hub.

These close the Q3 gaps the audit flagged: peer-review/acceptance
(OpenReview), code/checkpoint OSS audit (GitHub + HF Hub) — replacing the
dead Papers-With-Code pathway.  All stdlib (urllib via http_get_json).
"""

import unittest
from unittest.mock import patch

from omni_hub.retrieval import builtin_sources
from omni_hub.retrieval.cascade import DEFAULT_DOMAIN_CASCADES
from omni_hub.retrieval.github import GitHubRepoSource
from omni_hub.retrieval.hf_hub import HFHubSource
from omni_hub.retrieval.openreview import OpenReviewSource


class OpenReviewTests(unittest.TestCase):
    def test_search_maps_submissions(self) -> None:
        fake = {"notes": [{
            "id": "AbC123", "forum": "AbC123",
            "content": {"title": {"value": "Test Paper"},
                        "abstract": {"value": "We do X."},
                        "venue": {"value": "ICLR 2026"}},
        }]}
        with patch("omni_hub.retrieval.openreview.http_get_json", return_value=fake):
            recs = OpenReviewSource().retrieve("X")
        self.assertEqual(recs[0].title, "Test Paper")
        self.assertEqual(recs[0].metadata["forum_id"], "AbC123")
        self.assertIn("forum?id=AbC123", recs[0].url)

    def test_forum_thread_extracts_reviews_and_decision(self) -> None:
        sub = "ICLR.cc/2026/Conference/Submission1/-/"
        fake = {"notes": [
            {"id": "F1", "forum": "F1", "invitations": [sub + "Submission"],
             "content": {"title": {"value": "Paper"}}},
            {"id": "R1", "forum": "F1", "invitations": [sub + "Official_Review"],
             "content": {"rating": {"value": "8: accept"}, "confidence": {"value": "4"},
                         "summary": {"value": "Strong."}}},
            {"id": "R2", "forum": "F1", "invitations": [sub + "Official_Review"],
             "content": {"rating": {"value": "6"}, "summary": {"value": "OK."}}},
            {"id": "D1", "forum": "F1", "invitations": [sub + "Decision"],
             "content": {"decision": {"value": "Accept (Poster)"}}},
        ]}
        with patch("omni_hub.retrieval.openreview.http_get_json", return_value=fake):
            thread = OpenReviewSource().forum_thread("F1")
        self.assertEqual(thread["n_reviews"], 2)
        self.assertEqual(thread["ratings"], [8.0, 6.0])
        self.assertEqual(thread["avg_rating"], 7.0)
        self.assertTrue(thread["accepted"])
        self.assertEqual(thread["title"], "Paper")

    def test_forum_thread_accepts_url(self) -> None:
        with patch(
            "omni_hub.retrieval.openreview.http_get_json", return_value={"notes": []},
        ) as mock:
            OpenReviewSource().forum_thread("https://openreview.net/forum?id=ZZ9")
        self.assertEqual(mock.call_args.kwargs["params"]["forum"], "ZZ9")


class GitHubTests(unittest.TestCase):
    def test_search_maps_repos(self) -> None:
        fake = {"items": [{
            "full_name": "openai/gym", "stargazers_count": 35000,
            "license": {"spdx_id": "MIT"}, "pushed_at": "2026-05-01T00:00:00Z",
            "html_url": "https://github.com/openai/gym", "description": "RL envs",
            "forks_count": 8000, "open_issues_count": 50, "language": "Python",
        }]}
        with patch("omni_hub.retrieval.github.http_get_json", return_value=fake):
            rec = GitHubRepoSource().retrieve("gym")[0]
        self.assertEqual(rec.title, "openai/gym")
        self.assertEqual(rec.metadata["stars"], 35000)
        self.assertEqual(rec.metadata["license"], "MIT")
        self.assertEqual(rec.score, 35000.0)

    def test_repo_audit_surfaces_release_assets(self) -> None:
        meta = {"full_name": "a/b", "stargazers_count": 10,
                "license": {"spdx_id": "Apache-2.0"},
                "pushed_at": "2026-01-01T00:00:00Z", "open_issues_count": 2,
                "html_url": "https://github.com/a/b", "archived": False}
        releases = [{"tag_name": "v1.0", "published_at": "2026-01-02",
                     "assets": [{"name": "model.ckpt"}]}]
        with patch("omni_hub.retrieval.github.http_get_json",
                   side_effect=[meta, releases]):
            audit = GitHubRepoSource().repo_audit("a/b")
        self.assertEqual(audit["license"], "Apache-2.0")
        self.assertTrue(audit["has_releases"])
        self.assertEqual(audit["releases"][0]["assets"], ["model.ckpt"])

    def test_repo_audit_accepts_url(self) -> None:
        meta = {"full_name": "a/b", "html_url": "u"}
        with patch("omni_hub.retrieval.github.http_get_json",
                   side_effect=[meta, []]) as mock:
            GitHubRepoSource().repo_audit("https://github.com/a/b")
        self.assertIn("/repos/a/b", mock.call_args_list[0].args[0])


class HFHubTests(unittest.TestCase):
    def test_search_maps_models(self) -> None:
        fake = [{"id": "meta-llama/Llama-3", "downloads": 1000000, "likes": 500,
                 "pipeline_tag": "text-generation", "library_name": "transformers",
                 "tags": ["llm"], "lastModified": "2026-05-01"}]
        with patch("omni_hub.retrieval.hf_hub.http_get_json", return_value=fake):
            rec = HFHubSource().retrieve("llama")[0]
        self.assertEqual(rec.title, "meta-llama/Llama-3")
        self.assertEqual(rec.metadata["downloads"], 1000000)
        self.assertEqual(rec.metadata["pipeline_tag"], "text-generation")

    def test_model_info(self) -> None:
        fake = {"id": "x/y", "downloads": 5, "likes": 1, "pipeline_tag": "fill-mask",
                "tags": [], "lastModified": "2026-01-01", "gated": False}
        with patch("omni_hub.retrieval.hf_hub.http_get_json", return_value=fake):
            info = HFHubSource().model_info("x/y")
        self.assertEqual(info["model_id"], "x/y")
        self.assertEqual(info["downloads"], 5)


class PaperConnectorRegistrationTests(unittest.TestCase):
    def test_new_connectors_registered(self) -> None:
        names = set(builtin_sources())
        for n in ("openreview", "github", "hf_hub"):
            self.assertIn(n, names)

    def test_new_sources_wired_into_cascades(self) -> None:
        self.assertIn("openreview", DEFAULT_DOMAIN_CASCADES["ai_progress"])
        self.assertIn("openreview", DEFAULT_DOMAIN_CASCADES["research"])
        self.assertIn("github", DEFAULT_DOMAIN_CASCADES["agent_systems"])
        self.assertIn("github", DEFAULT_DOMAIN_CASCADES["engineering"])
        self.assertIn("hf_hub", DEFAULT_DOMAIN_CASCADES["ai_progress"])


if __name__ == "__main__":
    unittest.main()
