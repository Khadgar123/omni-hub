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

    def test_retrieve_with_grader_and_persist_evidence(self) -> None:
        fake_wp = {"pages": [{
            "title": "Anthropic", "id": 1,
            "excerpt": "Anthropic is an AI safety lab founded in 2021.",
        }]}
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "omni_hub.retrieval.wikipedia.http_get_json", return_value=fake_wp,
            ):
                result = _run_cli(Path(tmp), [
                    "retrieve", "--query", "Anthropic",
                    "--sources", "wikipedia",
                    "--fusion", "rrf",
                    "--grader", "heuristic",
                    "--cache",
                    "--persist-evidence",
                    "--run-id", "test-run-1",
                ])
            self.assertEqual(result["status"], "succeeded")
            out = result["output"]
            self.assertEqual(out["fusion"], "rrf")
            self.assertIn("evidence", out)
            self.assertEqual(out["evidence"]["run_id"], "test-run-1")
            # Evidence + cache files should exist on disk
            evidence_path = Path(out["evidence"]["evidence_path"])
            self.assertTrue(evidence_path.exists())
            self.assertTrue(
                (Path(tmp) / ".omni" / "retrieval_cache.sqlite3").exists(),
            )


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


class RRFFusionTests(unittest.TestCase):
    """S0-1 regression: RRF fusion ranks cross-source records correctly."""

    def test_record_in_two_sources_outranks_record_in_one(self) -> None:
        from omni_hub.retrieval.cascade import reciprocal_rank_fusion
        # Paper P appears at rank 1 in source A and rank 2 in source B.
        # Paper Q appears only at rank 1 in source B.
        p_a = RetrievalRecord(source="a", title="P", url="https://x/p", canonical_id="doi:10.p")
        p_b = RetrievalRecord(source="b", title="P", url="https://y/p", canonical_id="doi:10.p")
        q_b = RetrievalRecord(source="b", title="Q", url="https://y/q", canonical_id="doi:10.q")
        out = reciprocal_rank_fusion([[p_a], [q_b, p_b]])
        # P should rank above Q because it appears in 2 sources
        self.assertEqual(out[0].canonical_id, "doi:10.p")
        self.assertEqual(out[1].canonical_id, "doi:10.q")
        self.assertGreater(out[0].score, out[1].score)

    def test_rrf_uses_canonical_id_when_urls_differ(self) -> None:
        from omni_hub.retrieval.cascade import reciprocal_rank_fusion
        # Same DOI from arxiv + openalex — different URLs but same canonical_id
        a = RetrievalRecord(source="arxiv", title="P", url="https://arxiv.org/abs/X", canonical_id="doi:10.x")
        b = RetrievalRecord(source="openalex", title="P", url="https://openalex/W1", canonical_id="doi:10.x")
        out = reciprocal_rank_fusion([[a], [b]])
        self.assertEqual(len(out), 1)             # collapsed into one
        # Score reflects 2 source hits
        from omni_hub.retrieval.cascade import RRF_K
        self.assertAlmostEqual(out[0].score, 1/(RRF_K+1) + 1/(RRF_K+1))


class ParallelDispatchTests(unittest.TestCase):
    """S0-1 regression: cascade fans out via ThreadPoolExecutor."""

    def test_cascade_calls_sources_in_parallel(self) -> None:
        import time
        from omni_hub.retrieval import Cascade
        from omni_hub.retrieval.base import RetrievalSource

        class SlowSource:
            def __init__(self, name: str, delay: float) -> None:
                self.name = name
                self.delay = delay
            def retrieve(self, query, *, limit=5, domain=""):
                time.sleep(self.delay)
                return [RetrievalRecord(source=self.name, title=f"r-{self.name}", url=f"https://x/{self.name}")]

        sources = {"a": SlowSource("a", 0.15), "b": SlowSource("b", 0.15), "c": SlowSource("c", 0.15)}
        cascade = Cascade(sources, cascades={"d": ["a", "b", "c"]})
        start = time.monotonic()
        result = cascade.retrieve("q", domain="d")
        elapsed = time.monotonic() - start
        # 3 sources × 0.15s sequential = 0.45s; parallel should be <0.30s.
        self.assertLess(elapsed, 0.30, msg=f"cascade not parallel: {elapsed:.3f}s")
        self.assertEqual(len(result.records), 3)
        self.assertEqual(set(result.sources_succeeded), {"a", "b", "c"})

    def test_cascade_assigns_cite_ids(self) -> None:
        from omni_hub.retrieval import Cascade

        class SourceA:
            name = "a"
            def retrieve(self, query, *, limit=5, domain=""):
                return [RetrievalRecord(source="a", title="x", url="u1"),
                        RetrievalRecord(source="a", title="y", url="u2")]

        cascade = Cascade({"a": SourceA()}, cascades={"d": ["a"]})
        result = cascade.retrieve("q", domain="d")
        self.assertEqual(result.records[0].cite_id, "R1")
        self.assertEqual(result.records[1].cite_id, "R2")

    def test_rrf_fusion_via_cascade(self) -> None:
        from omni_hub.retrieval import Cascade

        class A:
            name = "a"
            def retrieve(self, q, *, limit=5, domain=""):
                return [RetrievalRecord(source="a", title="P", url="ua", canonical_id="doi:p"),
                        RetrievalRecord(source="a", title="X", url="ux")]

        class B:
            name = "b"
            def retrieve(self, q, *, limit=5, domain=""):
                return [RetrievalRecord(source="b", title="Y", url="uy"),
                        RetrievalRecord(source="b", title="P", url="ub", canonical_id="doi:p")]

        cascade = Cascade({"a": A(), "b": B()}, cascades={"d": ["a", "b"]})
        result = cascade.retrieve("q", domain="d", fusion="rrf")
        # P (in both) should be rank 1
        self.assertEqual(result.records[0].canonical_id, "doi:p")
        self.assertEqual(result.fusion, "rrf")


class CanonicalIdSemanticDedupTests(unittest.TestCase):
    """S0-2 regression: connectors populate canonical_id for cross-source dedup."""

    def test_openalex_emits_doi_canonical_id(self) -> None:
        from omni_hub.retrieval.openalex import OpenAlexSource
        with patch(
            "omni_hub.retrieval.openalex.http_get_json",
            return_value={"results": [{
                "id": "https://openalex.org/W1",
                "display_name": "Paper",
                "doi": "https://doi.org/10.x/foo",
                "cited_by_count": 1,
                "authorships": [],
                "abstract_inverted_index": {},
            }]},
        ):
            records = OpenAlexSource().retrieve("p")
        self.assertEqual(records[0].canonical_id, "doi:10.x/foo")

    def test_arxiv_emits_versionless_canonical_id(self) -> None:
        from omni_hub.retrieval.arxiv_api import ArxivSource
        atom = """<feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2510.01234v2</id><title>X</title><summary>s</summary>
<published>2026-05-01T00:00:00Z</published></entry></feed>"""
        with patch(
            "omni_hub.retrieval.arxiv_api.http_get_text",
            return_value=(atom, {}),
        ):
            records = ArxivSource().retrieve("x")
        self.assertEqual(records[0].canonical_id, "arxiv:2510.01234")

    def test_wikipedia_emits_lang_scoped_page_id(self) -> None:
        from omni_hub.retrieval.wikipedia import WikipediaSource
        with patch(
            "omni_hub.retrieval.wikipedia.http_get_json",
            return_value={"pages": [{"title": "Anthropic", "id": 12345}]},
        ):
            records = WikipediaSource().retrieve("Anthropic")
        self.assertEqual(records[0].canonical_id, "wp:en:12345")


class TTLCacheTests(unittest.TestCase):
    """S0-3 regression: SQLite TTL cache hits, misses, and integrates with cascade."""

    def test_put_then_get_returns_records(self) -> None:
        from omni_hub.retrieval.cache import TTLCache
        with tempfile.TemporaryDirectory() as tmp:
            cache = TTLCache(tmp)
            cache.put("wikipedia", "anthropic", "default", [
                RetrievalRecord(source="wikipedia", title="Anthropic", url="u"),
            ])
            hit = cache.get("wikipedia", "anthropic", "default")
            self.assertIsNotNone(hit)
            self.assertEqual(hit[0].title, "Anthropic")

    def test_expired_entry_returns_none(self) -> None:
        from omni_hub.retrieval.cache import TTLCache
        with tempfile.TemporaryDirectory() as tmp:
            cache = TTLCache(tmp)
            cache.put("gdelt", "q", "policy", [
                RetrievalRecord(source="gdelt", title="x", url="u"),
            ], ttl_sec=0)
            # ttl_sec=0 means expires_at == now, so immediately stale
            import time; time.sleep(0.01)
            self.assertIsNone(cache.get("gdelt", "q", "policy"))

    def test_cache_key_normalises_query_whitespace_and_case(self) -> None:
        from omni_hub.retrieval.cache import TTLCache
        with tempfile.TemporaryDirectory() as tmp:
            cache = TTLCache(tmp)
            cache.put("openalex", "  Attention  ", "research", [
                RetrievalRecord(source="openalex", title="x", url="u"),
            ])
            hit = cache.get("openalex", "attention", "research")
            self.assertIsNotNone(hit)

    def test_per_source_default_ttl_differs(self) -> None:
        from omni_hub.retrieval.cache import TTLCache
        with tempfile.TemporaryDirectory() as tmp:
            cache = TTLCache(tmp)
            self.assertEqual(cache.ttl_for("wikipedia"), 7 * 24 * 3600)
            self.assertEqual(cache.ttl_for("gdelt"), 3600)
            self.assertEqual(cache.ttl_for("jina_reader"), 300)
            # Unknown source → fallback
            self.assertEqual(cache.ttl_for("nope"), 6 * 3600)

    def test_cascade_uses_cache_to_skip_network(self) -> None:
        from omni_hub.retrieval import Cascade
        from omni_hub.retrieval.cache import TTLCache

        call_count = {"a": 0}

        class CountedSource:
            name = "a"
            def retrieve(self, q, *, limit=5, domain=""):
                call_count["a"] += 1
                return [RetrievalRecord(source="a", title=q, url=f"u/{q}")]

        with tempfile.TemporaryDirectory() as tmp:
            cache = TTLCache(tmp)
            cascade = Cascade({"a": CountedSource()},
                              cascades={"d": ["a"]},
                              cache=cache)
            r1 = cascade.retrieve("foo", domain="d")
            r2 = cascade.retrieve("foo", domain="d")  # should hit cache
            self.assertEqual(call_count["a"], 1)
            self.assertEqual(len(r1.records), 1)
            self.assertEqual(len(r2.records), 1)

    def test_invalidate_drops_entries(self) -> None:
        from omni_hub.retrieval.cache import TTLCache
        with tempfile.TemporaryDirectory() as tmp:
            cache = TTLCache(tmp)
            cache.put("a", "q1", "", [RetrievalRecord(source="a", title="x", url="u1")])
            cache.put("b", "q2", "", [RetrievalRecord(source="b", title="y", url="u2")])
            self.assertEqual(cache.invalidate("a"), 1)
            self.assertIsNone(cache.get("a", "q1", ""))
            self.assertIsNotNone(cache.get("b", "q2", ""))


class CitationRenderingTests(unittest.TestCase):
    """S1-2 regression: <cite r="Rn"/> → [n] + References block."""

    def _records(self) -> list[RetrievalRecord]:
        return [
            RetrievalRecord(
                source="openalex", title="Paper One", url="https://x.com/1",
                snippet="First abstract.", cite_id="R1",
            ),
            RetrievalRecord(
                source="arxiv", title="Paper Two", url="https://x.com/2",
                snippet="Second abstract.", cite_id="R2",
            ),
            RetrievalRecord(
                source="wikipedia", title="Topic Three", url="https://x.com/3",
                snippet="Third blurb.", cite_id="R3",
            ),
        ]

    def test_marker_converts_to_inline_number(self) -> None:
        from omni_hub.retrieval.citations import render_with_citations
        text = 'Foo bar <cite r="R2"/> baz.'
        out = render_with_citations(text, self._records())
        self.assertIn("Foo bar [2] baz.", out.body)
        self.assertEqual(len(out.references), 1)
        self.assertEqual(out.references[0].cite_id, "R2")
        self.assertEqual(out.unknown_ids, [])

    def test_references_block_appended(self) -> None:
        from omni_hub.retrieval.citations import render_with_citations
        text = 'Claim <cite r="R1"/> claim <cite r="R3"/>.'
        out = render_with_citations(text, self._records())
        self.assertIn("## References", out.body)
        self.assertIn("[1] [Paper One](https://x.com/1)", out.body)
        self.assertIn("[3] [Topic Three](https://x.com/3)", out.body)
        # Order should follow first-use, not record order
        self.assertLess(out.body.index("[1]"), out.body.index("[3]"))

    def test_unknown_id_collected_and_dropped(self) -> None:
        from omni_hub.retrieval.citations import render_with_citations
        text = 'See <cite r="R99"/> for details.'
        out = render_with_citations(text, self._records())
        self.assertEqual(out.unknown_ids, ["R99"])
        # The marker was stripped (no [99])
        self.assertNotIn("[99]", out.body)
        self.assertEqual(out.references, [])

    def test_adjacent_markers_compact_without_space(self) -> None:
        from omni_hub.retrieval.citations import render_with_citations
        text = 'Claim<cite r="R1"/><cite r="R3"/>.'
        out = render_with_citations(text, self._records())
        self.assertIn("Claim[1][3].", out.body)

    def test_no_markers_returns_text_unchanged(self) -> None:
        from omni_hub.retrieval.citations import render_with_citations
        out = render_with_citations("Plain text, no cites.", self._records())
        self.assertEqual(out.body, "Plain text, no cites.")
        self.assertEqual(out.references, [])

    def test_structured_citations_for_mcp_clients(self) -> None:
        from omni_hub.retrieval.citations import render_to_structured_citations
        out = render_to_structured_citations(self._records())
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]["id"], "R1")
        self.assertEqual(out[0]["n"], 1)
        self.assertEqual(out[0]["url"], "https://x.com/1")


class GraderTests(unittest.TestCase):
    """S1-3 regression: HeuristicGrader + LLMJudgeGrader verdict logic."""

    def test_heuristic_grades_paywall_as_incorrect(self) -> None:
        from omni_hub.retrieval.graders import HeuristicGrader
        rec = RetrievalRecord(
            source="x", title="Article",
            snippet="You have reached your article limit. Subscribe to continue.",
            url="https://x.com/a",
        )
        self.assertEqual(HeuristicGrader()(query="climate", record=rec), "incorrect")

    def test_heuristic_grades_404_as_incorrect(self) -> None:
        from omni_hub.retrieval.graders import HeuristicGrader
        rec = RetrievalRecord(
            source="x", title="Oops", snippet="404 Not Found", url="https://x.com/a",
        )
        self.assertEqual(HeuristicGrader()(query="anything", record=rec), "incorrect")

    def test_heuristic_grades_empty_record_as_incorrect(self) -> None:
        from omni_hub.retrieval.graders import HeuristicGrader
        rec = RetrievalRecord(source="x", title="", snippet="", url="")
        self.assertEqual(HeuristicGrader()(query="q", record=rec), "incorrect")

    def test_heuristic_grades_good_overlap_as_correct(self) -> None:
        from omni_hub.retrieval.graders import HeuristicGrader
        rec = RetrievalRecord(
            source="x", title="Anthropic Claude Agent SDK",
            snippet="The Anthropic Claude Agent SDK provides Python tooling.",
            url="https://x.com/a",
        )
        self.assertEqual(
            HeuristicGrader()(query="Claude Agent SDK Python", record=rec),
            "correct",
        )

    def test_heuristic_grades_zero_overlap_as_incorrect(self) -> None:
        from omni_hub.retrieval.graders import HeuristicGrader
        rec = RetrievalRecord(
            source="x", title="Cooking recipes",
            snippet="Pasta carbonara with pancetta and pecorino.",
            url="https://x.com/a",
        )
        self.assertEqual(
            HeuristicGrader()(query="kubernetes networking", record=rec),
            "incorrect",
        )

    def test_heuristic_short_snippet_is_ambiguous(self) -> None:
        from omni_hub.retrieval.graders import HeuristicGrader
        rec = RetrievalRecord(
            source="x", title="Claude SDK",
            snippet="tiny",       # below default 20-char threshold
            url="https://x.com/a",
        )
        self.assertEqual(
            HeuristicGrader()(query="Claude SDK", record=rec), "ambiguous",
        )

    def test_llm_judge_parses_correct(self) -> None:
        from omni_hub.retrieval.graders import LLMJudgeGrader
        grader = LLMJudgeGrader(model_call=lambda _p: "correct")
        rec = RetrievalRecord(source="x", title="t", snippet="s", url="u")
        self.assertEqual(grader("q", rec), "correct")

    def test_llm_judge_parses_incorrect_anywhere_in_response(self) -> None:
        from omni_hub.retrieval.graders import LLMJudgeGrader
        # Model is chatty but answer is parseable
        grader = LLMJudgeGrader(
            model_call=lambda _p: "After review, this looks incorrect to me.",
        )
        rec = RetrievalRecord(source="x", title="t", snippet="s", url="u")
        self.assertEqual(grader("q", rec), "incorrect")

    def test_llm_judge_falls_back_to_ambiguous_on_unparseable(self) -> None:
        from omni_hub.retrieval.graders import LLMJudgeGrader
        grader = LLMJudgeGrader(model_call=lambda _p: "meh")
        rec = RetrievalRecord(source="x", title="t", snippet="s", url="u")
        self.assertEqual(grader("q", rec), "ambiguous")

    def test_llm_judge_falls_back_when_model_raises(self) -> None:
        from omni_hub.retrieval.graders import LLMJudgeGrader
        def boom(_p: str) -> str:
            raise RuntimeError("model down")
        grader = LLMJudgeGrader(model_call=boom)
        rec = RetrievalRecord(source="x", title="t", snippet="s", url="u")
        self.assertEqual(grader("q", rec), "ambiguous")

    def test_cascade_drops_grader_incorrect_records(self) -> None:
        from omni_hub.retrieval import Cascade
        from omni_hub.retrieval.graders import HeuristicGrader

        class Source:
            name = "x"
            def retrieve(self, q, *, limit=5, domain=""):
                return [
                    RetrievalRecord(
                        source="x", title="404 Not Found",
                        snippet="404 Not Found", url="https://x.com/dead",
                    ),
                    RetrievalRecord(
                        source="x", title="Good match for q",
                        snippet="The query q is well-explained in this fine document.",
                        url="https://x.com/good",
                    ),
                ]

        cascade = Cascade({"x": Source()}, cascades={"d": ["x"]})
        result = cascade.retrieve("query q", domain="d", grader=HeuristicGrader())
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].url, "https://x.com/good")
        self.assertEqual(result.graded_dropped, 1)


class EvidenceStoreTests(unittest.TestCase):
    """S1-4 regression: .omni/retrieval/<run_id>/{evidence,sources,manifest}."""

    def _sample_cascade_dict(self) -> dict:
        return {
            "query": "Anthropic",
            "domain": "research",
            "fusion": "rrf",
            "sources_tried": ["openalex", "wikipedia"],
            "sources_succeeded": ["wikipedia"],
            "graded_dropped": 0,
            "errors": [{"source": "openalex", "error": "offline"}],
            "records": [
                {
                    "source": "wikipedia", "title": "Anthropic",
                    "url": "https://en.wikipedia.org/wiki/Anthropic",
                    "snippet": "AI safety lab.", "score": 0.0,
                    "fetched_at": "2026-05-28T00:00:00Z", "domain": "research",
                    "metadata": {}, "canonical_id": "wp:en:1234",
                    "cite_id": "R1",
                },
            ],
        }

    def test_write_creates_three_files(self) -> None:
        from omni_hub.retrieval.evidence import EvidenceStore
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(tmp)
            artifact = store.write(self._sample_cascade_dict())
            self.assertTrue(artifact.run_manifest_path.exists())
            self.assertTrue(artifact.sources_path.exists())
            self.assertTrue(artifact.evidence_path.exists())
            self.assertEqual(artifact.record_count, 1)

    def test_evidence_jsonl_one_record_per_line(self) -> None:
        from omni_hub.retrieval.evidence import EvidenceStore
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(tmp)
            artifact = store.write(self._sample_cascade_dict())
            lines = artifact.evidence_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            rec = json.loads(lines[0])
            self.assertEqual(rec["cite_id"], "R1")
            self.assertEqual(rec["canonical_id"], "wp:en:1234")

    def test_sources_json_dedupes_urls_and_keys_by_cite_id(self) -> None:
        from omni_hub.retrieval.evidence import EvidenceStore
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(tmp)
            artifact = store.write(self._sample_cascade_dict())
            payload = json.loads(artifact.sources_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["count"], 1)
            self.assertEqual(
                payload["urls"], ["https://en.wikipedia.org/wiki/Anthropic"],
            )
            self.assertIn("R1", payload["by_cite_id"])
            self.assertEqual(
                payload["by_cite_id"]["R1"]["source"], "wikipedia",
            )

    def test_manifest_carries_diagnostics(self) -> None:
        from omni_hub.retrieval.evidence import EvidenceStore
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(tmp)
            artifact = store.write(self._sample_cascade_dict())
            manifest = json.loads(
                artifact.run_manifest_path.read_text(encoding="utf-8"),
            )
            self.assertEqual(manifest["query"], "Anthropic")
            self.assertEqual(manifest["domain"], "research")
            self.assertEqual(manifest["fusion"], "rrf")
            self.assertEqual(manifest["sources_succeeded"], ["wikipedia"])
            self.assertEqual(manifest["record_count"], 1)
            self.assertIn("written_at", manifest)

    def test_read_manifest_roundtrip(self) -> None:
        from omni_hub.retrieval.evidence import EvidenceStore
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(tmp)
            artifact = store.write(
                self._sample_cascade_dict(), run_id="custom-run-id",
            )
            manifest = store.read_manifest("custom-run-id")
            self.assertEqual(manifest["run_id"], "custom-run-id")
            self.assertEqual(manifest["query"], "Anthropic")

    def test_list_runs_returns_sorted_descending(self) -> None:
        from omni_hub.retrieval.evidence import EvidenceStore
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(tmp)
            store.write(self._sample_cascade_dict(), run_id="20260101T000000Z-aaaaaaaa")
            store.write(self._sample_cascade_dict(), run_id="20260301T000000Z-bbbbbbbb")
            store.write(self._sample_cascade_dict(), run_id="20260201T000000Z-cccccccc")
            runs = store.list_runs()
            self.assertEqual(runs[0], "20260301T000000Z-bbbbbbbb")
            self.assertEqual(runs[-1], "20260101T000000Z-aaaaaaaa")

    def test_extra_manifest_merges_into_run(self) -> None:
        from omni_hub.retrieval.evidence import EvidenceStore
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(tmp)
            artifact = store.write(
                self._sample_cascade_dict(),
                extra_manifest={"task_id": "task-42", "lane": "claude"},
            )
            manifest = json.loads(
                artifact.run_manifest_path.read_text(encoding="utf-8"),
            )
            self.assertEqual(manifest["task_id"], "task-42")
            self.assertEqual(manifest["lane"], "claude")

    def test_read_manifest_missing_raises(self) -> None:
        from omni_hub.retrieval.evidence import EvidenceStore
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(tmp)
            with self.assertRaises(FileNotFoundError):
                store.read_manifest("nope")


class PlannerTests(unittest.TestCase):
    """S1-1 regression: plan(query, ..., model_call) shape & fallbacks."""

    def test_no_model_call_returns_noop_plan(self) -> None:
        from omni_hub.retrieval.planner import plan
        p = plan(
            "anthropic claude agent sdk",
            available_sources=["openalex", "wikipedia", "gdelt"],
        )
        self.assertEqual(p.rewritten_query, "anthropic claude agent sdk")
        self.assertEqual(p.sources, ["openalex", "wikipedia", "gdelt"])
        self.assertEqual(p.sub_queries, [])
        self.assertFalse(p.deep)

    def test_model_response_parsed_into_plan(self) -> None:
        from omni_hub.retrieval.planner import plan
        response = json.dumps({
            "rewritten_query": "Anthropic Claude Agent SDK Python TypeScript",
            "sub_queries": ["claude-code hooks", "claude agent SDK"],
            "sources": ["openalex", "arxiv"],
            "deep": True,
        })
        p = plan(
            "claude agent sdk",
            available_sources=["openalex", "arxiv", "wikipedia"],
            model_call=lambda _prompt: response,
        )
        self.assertEqual(
            p.rewritten_query,
            "Anthropic Claude Agent SDK Python TypeScript",
        )
        self.assertEqual(p.sources, ["openalex", "arxiv"])
        self.assertEqual(len(p.sub_queries), 2)
        self.assertTrue(p.deep)

    def test_json_inside_markdown_fence_still_parsed(self) -> None:
        from omni_hub.retrieval.planner import plan
        response = (
            "```json\n"
            '{"rewritten_query": "x", "sources": ["wikipedia"]}\n'
            "```"
        )
        p = plan(
            "x", available_sources=["wikipedia", "openalex"],
            model_call=lambda _p: response,
        )
        self.assertEqual(p.sources, ["wikipedia"])

    def test_source_whitelist_strips_unknown(self) -> None:
        from omni_hub.retrieval.planner import plan
        response = json.dumps({
            "rewritten_query": "x",
            "sources": ["wikipedia", "made_up_source", "openalex"],
        })
        p = plan(
            "x",
            available_sources=["wikipedia", "openalex"],
            model_call=lambda _p: response,
        )
        self.assertEqual(set(p.sources), {"wikipedia", "openalex"})

    def test_empty_sources_falls_back_to_all_available(self) -> None:
        from omni_hub.retrieval.planner import plan
        response = json.dumps({"rewritten_query": "x", "sources": ["unknown"]})
        p = plan(
            "x",
            available_sources=["wikipedia", "openalex"],
            model_call=lambda _p: response,
        )
        self.assertEqual(set(p.sources), {"wikipedia", "openalex"})

    def test_model_raising_falls_back_to_noop(self) -> None:
        from omni_hub.retrieval.planner import plan
        def boom(_p: str) -> str:
            raise RuntimeError("model offline")
        p = plan(
            "x",
            available_sources=["wikipedia"],
            model_call=boom,
        )
        self.assertEqual(p.rewritten_query, "x")
        self.assertEqual(p.sources, ["wikipedia"])

    def test_garbage_response_falls_back(self) -> None:
        from omni_hub.retrieval.planner import plan
        p = plan(
            "x",
            available_sources=["wikipedia"],
            model_call=lambda _p: "garbage no JSON here",
        )
        self.assertEqual(p.rewritten_query, "x")
        self.assertEqual(p.sources, ["wikipedia"])

    def test_max_sources_truncates(self) -> None:
        from omni_hub.retrieval.planner import plan
        response = json.dumps({
            "rewritten_query": "x",
            "sources": ["a", "b", "c", "d", "e"],
        })
        p = plan(
            "x",
            available_sources=["a", "b", "c", "d", "e"],
            model_call=lambda _p: response,
            max_sources=2,
        )
        self.assertEqual(len(p.sources), 2)

    def test_plan_to_dict_is_json_serialisable(self) -> None:
        from omni_hub.retrieval.planner import plan
        p = plan("x", available_sources=["wikipedia"])
        json.dumps(p.to_dict())   # must not raise


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
