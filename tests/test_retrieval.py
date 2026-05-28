"""Retrieval plane tests — base contract + each connector + cascade + CLI.

Every HTTP call is mocked through ``omni_hub.retrieval.base.http_get_json``
and ``http_get_text``.  Tests pin exact request shapes (URL, params,
headers) and exact response parsing, so a future API drift surfaces here
not in production.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.retrieval import (
    Cascade,
    DEFAULT_DOMAIN_CASCADES,
    RetrievalError,
    RetrievalRecord,
    builtin_sources,
    normalize_records,
)
from omni_hub.retrieval.arxiv_api import ArxivSource
from omni_hub.retrieval.cascade import CascadeResult
from omni_hub.retrieval.gdelt import GDELTSource
from omni_hub.retrieval.jina_reader import JinaReaderFetcher
from omni_hub.retrieval.openalex import OpenAlexSource
from omni_hub.retrieval.semantic_scholar import SemanticScholarSource
from omni_hub.retrieval.wikipedia import WikipediaSource
from omni_hub.testing import cli_runner as _run_cli


class BaseRecordTests(unittest.TestCase):
    def test_record_roundtrip(self) -> None:
        r = RetrievalRecord(
            source="openalex", title="t", url="u", snippet="s",
            score=1.5, domain="research", metadata={"year": 2026},
        )
        d = r.to_dict()
        self.assertEqual(d["source"], "openalex")
        self.assertEqual(d["metadata"]["year"], 2026)

    def test_normalize_dedups_by_url(self) -> None:
        records = [
            RetrievalRecord(source="a", title="t1", url="https://x.com/a"),
            RetrievalRecord(source="b", title="t2", url="https://x.com/a"),  # dup URL
            RetrievalRecord(source="c", title="t3", url="https://x.com/b"),
            RetrievalRecord(source="d", title="t4", url=""),                  # empty URL keeps
        ]
        out = normalize_records(records)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0].source, "a")        # first wins
        self.assertEqual(out[2].source, "d")


class JinaReaderTests(unittest.TestCase):
    def test_non_url_query_returns_empty(self) -> None:
        adapter = JinaReaderFetcher()
        self.assertEqual(adapter.retrieve("just some words"), [])

    def test_url_query_calls_reader_endpoint(self) -> None:
        adapter = JinaReaderFetcher()
        fake_markdown = "# Hello\n\nWorld."
        with patch(
            "omni_hub.retrieval.jina_reader.http_get_text",
            return_value=(fake_markdown, {"content-type": "text/markdown"}),
        ) as mock:
            records = adapter.retrieve("https://example.com/post")
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.title, "Hello")
        self.assertEqual(rec.url, "https://example.com/post")
        self.assertEqual(rec.metadata["full_markdown"], fake_markdown)
        # Verify the call wrapped via r.jina.ai
        self.assertIn("r.jina.ai", mock.call_args[0][0])


class OpenAlexTests(unittest.TestCase):
    def test_search_parses_inverted_abstract(self) -> None:
        adapter = OpenAlexSource()
        fake = {
            "results": [{
                "id": "https://openalex.org/W1",
                "display_name": "Attention Is All You Need",
                "publication_year": 2017,
                "doi": "https://doi.org/10.x",
                "cited_by_count": 80000,
                "authorships": [{"author": {"display_name": "Vaswani"}}],
                "primary_location": {"source": {"display_name": "NeurIPS"}},
                "abstract_inverted_index": {"This": [0], "paper": [1], "matters": [2]},
                "open_access": {"is_oa": True},
            }],
        }
        with patch(
            "omni_hub.retrieval.openalex.http_get_json",
            return_value=fake,
        ):
            records = adapter.retrieve("attention transformer", limit=1)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.title, "Attention Is All You Need")
        self.assertEqual(rec.score, 80000.0)
        self.assertEqual(rec.metadata["year"], 2017)
        self.assertEqual(rec.metadata["venue"], "NeurIPS")
        self.assertIn("This paper matters", rec.snippet)


class SemanticScholarTests(unittest.TestCase):
    def test_uses_api_key_header_when_provided(self) -> None:
        adapter = SemanticScholarSource(api_key="secret-key")
        fake = {"data": [{"title": "P", "authors": [], "abstract": "abs", "citationCount": 3}]}
        with patch(
            "omni_hub.retrieval.semantic_scholar.http_get_json",
            return_value=fake,
        ) as mock:
            records = adapter.retrieve("x")
        kwargs = mock.call_args.kwargs
        self.assertEqual(kwargs["headers"]["x-api-key"], "secret-key")
        self.assertEqual(records[0].score, 3.0)


class ArxivTests(unittest.TestCase):
    def test_parses_atom_response(self) -> None:
        adapter = ArxivSource()
        atom = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2510.01234v1</id>
    <title>A Cool Paper</title>
    <summary>Abstract here.</summary>
    <published>2026-05-01T00:00:00Z</published>
    <author><name>Alice</name></author>
    <author><name>Bob</name></author>
    <category term="cs.AI"/>
    <category term="cs.LG"/>
  </entry>
</feed>"""
        with patch(
            "omni_hub.retrieval.arxiv_api.http_get_text",
            return_value=(atom, {}),
        ):
            records = adapter.retrieve("agent")
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.title, "A Cool Paper")
        self.assertEqual(rec.metadata["authors"], ["Alice", "Bob"])
        self.assertEqual(set(rec.metadata["categories"]), {"cs.AI", "cs.LG"})
        self.assertTrue(rec.metadata["html_url"].endswith("/2510.01234v1"))

    def test_ai_progress_domain_adds_category_filter(self) -> None:
        adapter = ArxivSource()
        with patch(
            "omni_hub.retrieval.arxiv_api.http_get_text",
            return_value=("<feed xmlns=\"http://www.w3.org/2005/Atom\"></feed>", {}),
        ) as mock:
            adapter.retrieve("x", domain="ai_progress")
        url = mock.call_args[0][0]
        self.assertIn("cat:cs.AI", url)
        self.assertIn("cat:cs.LG", url)


class WikipediaTests(unittest.TestCase):
    def test_search_uses_english_by_default(self) -> None:
        adapter = WikipediaSource()
        fake = {"pages": [{"title": "Anthropic", "id": 1, "excerpt": "An AI lab"}]}
        with patch(
            "omni_hub.retrieval.wikipedia.http_get_json",
            return_value=fake,
        ) as mock:
            records = adapter.retrieve("Anthropic")
        url = mock.call_args[0][0]
        self.assertIn("en.wikipedia.org", url)
        self.assertEqual(records[0].metadata["lang"], "en")

    def test_cjk_query_routes_to_zh_wikipedia(self) -> None:
        adapter = WikipediaSource()
        fake = {"pages": [{"title": "万象", "id": 2}]}
        with patch(
            "omni_hub.retrieval.wikipedia.http_get_json",
            return_value=fake,
        ) as mock:
            adapter.retrieve("万象中枢")
        self.assertIn("zh.wikipedia.org", mock.call_args[0][0])


class GDELTTests(unittest.TestCase):
    def test_multi_word_query_gets_quoted(self) -> None:
        adapter = GDELTSource()
        fake = {"articles": [{"title": "Headline", "url": "https://news/x", "domain": "news.com"}]}
        with patch(
            "omni_hub.retrieval.gdelt.http_get_json",
            return_value=fake,
        ) as mock:
            records = adapter.retrieve("Anthropic Claude")
        url = mock.call_args[0][0]
        self.assertIn("%22Anthropic+Claude%22", url)
        self.assertEqual(records[0].metadata["outlet_domain"], "news.com")


class CascadeTests(unittest.TestCase):
    def _stub_source(self, name: str, records: list[RetrievalRecord] | Exception) -> object:
        class Stub:
            def __init__(self) -> None:
                self.name = name
            def retrieve(self, query, *, limit=5, domain=""):
                if isinstance(records, Exception):
                    raise records
                return list(records)
        return Stub()

    def test_cascade_runs_in_order_and_dedups(self) -> None:
        a = self._stub_source("a", [
            RetrievalRecord(source="a", title="t1", url="https://x.com/1"),
        ])
        b = self._stub_source("b", [
            RetrievalRecord(source="b", title="t2", url="https://x.com/1"),  # dup URL
            RetrievalRecord(source="b", title="t3", url="https://x.com/2"),
        ])
        cascade = Cascade(
            {"a": a, "b": b},
            cascades={"x": ["a", "b"]},
        )
        result = cascade.retrieve("q", domain="x")
        self.assertEqual(len(result.records), 2)
        self.assertEqual(result.records[0].source, "a")
        self.assertEqual(result.records[1].source, "b")
        self.assertEqual(result.sources_succeeded, ["a", "b"])

    def test_one_source_failing_does_not_abort_cascade(self) -> None:
        a = self._stub_source("a", RetrievalError("rate limited"))
        b = self._stub_source("b", [RetrievalRecord(source="b", title="ok", url="u")])
        cascade = Cascade({"a": a, "b": b}, cascades={"x": ["a", "b"]})
        result = cascade.retrieve("q", domain="x")
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].source, "b")
        self.assertEqual(len(result.errors), 1)
        self.assertIn("rate limited", result.errors[0]["error"])

    def test_unknown_source_in_cascade_collected_as_error(self) -> None:
        cascade = Cascade({}, cascades={"x": ["nope"]})
        result = cascade.retrieve("q", domain="x")
        self.assertEqual(result.errors[0]["source"], "nope")

    def test_default_domain_cascades_cover_8_profiles(self) -> None:
        for d in ("engineering", "research", "finance", "policy",
                  "international_relations", "ai_progress", "default"):
            self.assertIn(d, DEFAULT_DOMAIN_CASCADES)

    def test_builtin_sources_registry_contains_all_free_connectors(self) -> None:
        sources = builtin_sources()
        for expected in (
            "arxiv", "gdelt", "jina_reader",
            "openalex", "semantic_scholar", "wikipedia",
        ):
            self.assertIn(expected, sources)


class RetrieveCliTests(unittest.TestCase):
    def test_retrieve_command_dispatches_to_cascade(self) -> None:
        # Patch the bottom-of-stack http calls so the cascade runs but no
        # network IO happens.  We patch each source's http_get_json/text.
        fake_wikipedia = {"pages": [{"title": "Anthropic", "id": 1, "excerpt": "Lab"}]}
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "omni_hub.retrieval.wikipedia.http_get_json",
                return_value=fake_wikipedia,
            ), patch(
                "omni_hub.retrieval.openalex.http_get_json",
                side_effect=RetrievalError("offline"),
            ), patch(
                "omni_hub.retrieval.gdelt.http_get_json",
                side_effect=RetrievalError("offline"),
            ):
                result = _run_cli(Path(tmp), [
                    "retrieve", "--query", "Anthropic", "--domain", "default",
                    "--per-source-limit", "3", "--total-limit", "5",
                ])
        self.assertEqual(result["status"], "succeeded")
        out = result["output"]
        self.assertGreaterEqual(out["count"], 1)
        self.assertIn("wikipedia", out["sources_succeeded"])
        # OpenAlex and GDELT were offline → errors collected
        self.assertGreaterEqual(len(out["errors"]), 2)

    def test_retrieve_with_explicit_sources_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake = {"pages": []}
            with patch(
                "omni_hub.retrieval.wikipedia.http_get_json", return_value=fake,
            ):
                result = _run_cli(Path(tmp), [
                    "retrieve", "--query", "x",
                    "--sources", "wikipedia",
                ])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["output"]["sources_tried"], ["wikipedia"])


class CaptureUrlBugFixTests(unittest.TestCase):
    """Regression for the 3 silent bugs the SOTA audit flagged."""

    def test_pdf_content_type_skips_text_decoding(self) -> None:
        from omni_hub.connectors.web import build_resource_from_body
        # Pretend we got raw PDF bytes (they'd have been latin-1'd before).
        resource = build_resource_from_body(
            "https://example.com/paper.pdf",
            body="%PDF-1.4\nbinary garbage here \x00\x01\x02",
            content_type="application/pdf",
        )
        self.assertEqual(resource.source_kind, "pdf_document")
        self.assertEqual(resource.text, "")            # was: corrupted bytes-as-text
        self.assertEqual(resource.body, "")            # not stored
        self.assertTrue(resource.metadata["is_pdf"])
        self.assertIn("pymupdf4llm", resource.metadata["pdf_extraction_hint"])

    def test_image_content_type_also_skips(self) -> None:
        from omni_hub.connectors.web import build_resource_from_body
        resource = build_resource_from_body(
            "https://example.com/cat.jpg",
            body="\xff\xd8\xff garbage",
            content_type="image/jpeg",
        )
        self.assertEqual(resource.source_kind, "binary")
        self.assertEqual(resource.text, "")

    def test_decode_body_returns_empty_for_pdf_bytes(self) -> None:
        from omni_hub.connectors.web import _decode_body
        self.assertEqual(
            _decode_body(b"%PDF-1.4 \xff\xff\xff", "application/pdf"),
            "",
        )

    def test_youtube_transcript_falls_back_quietly_when_yt_dlp_missing(self) -> None:
        from omni_hub.connectors.web import _fetch_youtube_transcript
        with patch("omni_hub.connectors.web.shutil.which", return_value=None):
            self.assertEqual(
                _fetch_youtube_transcript("https://youtu.be/x"), "",
            )

    def test_build_resource_includes_youtube_transcript_when_supplied(self) -> None:
        from omni_hub.connectors.web import build_resource_from_body
        resource = build_resource_from_body(
            "https://youtu.be/abc123",
            body="<html></html>",
            content_type="text/html",
            youtube_transcript="Here is the transcript text.",
        )
        self.assertEqual(resource.source_kind, "youtube_video")
        self.assertIn("Here is the transcript text.", resource.text)
        self.assertTrue(resource.metadata["has_transcript"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
