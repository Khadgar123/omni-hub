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
import urllib.parse
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
from omni_hub.retrieval.base import http_get_json
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


class _FakeHTTPResponse:
    def __init__(self, body: bytes = b"{}") -> None:
        self._body = body
        self.headers = {"content-type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class BaseHTTPTests(unittest.TestCase):
    def test_json_get_expands_sequence_params(self) -> None:
        captured_urls: list[str] = []

        def fake_urlopen(req, timeout):
            captured_urls.append(req.full_url)
            return _FakeHTTPResponse()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            http_get_json(
                "https://example.test/search",
                params={
                    "fields[]": ["title", "abstract"],
                    "conditions[term]": "EPA rule",
                    "skip": None,
                },
            )

        parsed = urllib.parse.urlparse(captured_urls[0])
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(query["fields[]"], ["title", "abstract"])
        self.assertEqual(query["conditions[term]"], ["EPA rule"])
        self.assertNotIn("skip", query)
        self.assertNotIn("%5B%27title%27", captured_urls[0])


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


class BraveSearchTests(unittest.TestCase):
    def test_requires_api_key(self) -> None:
        from omni_hub.retrieval.web_search import BraveSearchSource

        self.assertEqual(BraveSearchSource(api_key="").check()[0], "off")

    def test_parses_web_results(self) -> None:
        from omni_hub.retrieval.web_search import BraveSearchSource

        fake = {
            "web": {
                "results": [{
                    "title": "Source Title",
                    "url": "https://example.com/page?utm_source=x",
                    "description": "Useful source snippet.",
                    "age": "May 27, 2026",
                    "profile": {"name": "Example"},
                }],
            },
        }
        adapter = BraveSearchSource(api_key="secret-token")
        with patch(
            "omni_hub.retrieval.web_search.http_get_json",
            return_value=fake,
        ) as mock:
            records = adapter.retrieve("source query", limit=1)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.source, "brave_search")
        self.assertEqual(rec.title, "Source Title")
        self.assertEqual(rec.url, "https://example.com/page?utm_source=x")
        self.assertTrue(rec.canonical_id.startswith("web:"))
        self.assertEqual(rec.metadata["age"], "May 27, 2026")
        self.assertEqual(mock.call_args.kwargs["headers"]["X-Subscription-Token"], "secret-token")


class CrossrefTests(unittest.TestCase):
    def test_parses_work_metadata(self) -> None:
        from omni_hub.retrieval.crossref import CrossrefSource

        fake = {
            "message": {
                "items": [{
                    "title": ["A Verified Knowledge Paper"],
                    "DOI": "10.1234/ABC",
                    "URL": "https://doi.org/10.1234/ABC",
                    "abstract": "<jats:p>Structured abstract text.</jats:p>",
                    "is-referenced-by-count": 17,
                    "published-print": {"date-parts": [[2024, 5, 1]]},
                    "container-title": ["Journal of Tests"],
                    "author": [
                        {"given": "Ada", "family": "Lovelace"},
                        {"name": "Collective Author"},
                    ],
                }],
            },
        }
        with patch(
            "omni_hub.retrieval.crossref.http_get_json",
            return_value=fake,
        ) as mock:
            records = CrossrefSource().retrieve("verified knowledge", limit=1)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.source, "crossref")
        self.assertEqual(rec.canonical_id, "doi:10.1234/abc")
        self.assertEqual(rec.score, 17.0)
        self.assertEqual(rec.metadata["year"], 2024)
        self.assertEqual(rec.metadata["venue"], "Journal of Tests")
        self.assertEqual(rec.metadata["authors"], ["Ada Lovelace", "Collective Author"])
        self.assertIn("Structured abstract text.", rec.snippet)
        self.assertEqual(mock.call_args.kwargs["params"]["query"], "verified knowledge")


class WikidataTests(unittest.TestCase):
    def test_entity_search_parses_qids(self) -> None:
        from omni_hub.retrieval.wikidata import WikidataSource

        fake = {
            "search": [{
                "id": "Q42",
                "title": "Q42",
                "label": "Douglas Adams",
                "description": "English writer and humorist",
                "concepturi": "http://www.wikidata.org/entity/Q42",
            }],
        }
        with patch(
            "omni_hub.retrieval.wikidata.http_get_json",
            return_value=fake,
        ) as mock:
            records = WikidataSource().retrieve("Douglas Adams", limit=1)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.source, "wikidata")
        self.assertEqual(rec.title, "Douglas Adams")
        self.assertEqual(rec.canonical_id, "wikidata:Q42")
        self.assertEqual(rec.metadata["qid"], "Q42")
        self.assertEqual(mock.call_args.kwargs["params"]["action"], "wbsearchentities")


class WikidataSPARQLTests(unittest.TestCase):
    def test_entity_search_sparql_parses_bindings(self) -> None:
        from omni_hub.retrieval.wikidata import WikidataSPARQLSource

        fake = {
            "results": {
                "bindings": [{
                    "item": {"value": "http://www.wikidata.org/entity/Q42"},
                    "itemLabel": {"value": "Douglas Adams"},
                    "itemDescription": {"value": "English writer and humorist"},
                }],
            },
        }
        with patch(
            "omni_hub.retrieval.wikidata.http_get_json",
            return_value=fake,
        ) as mock:
            records = WikidataSPARQLSource().retrieve("Douglas Adams", limit=1)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.source, "wikidata_sparql")
        self.assertEqual(rec.canonical_id, "wikidata:Q42")
        self.assertEqual(rec.title, "Douglas Adams")
        self.assertIn("SERVICE wikibase:mwapi", mock.call_args.kwargs["params"]["query"])


class BiomedicalSourceTests(unittest.TestCase):
    def test_europe_pmc_parses_rest_results(self) -> None:
        from omni_hub.retrieval.biomedical import EuropePMCSource

        fake = {
            "resultList": {
                "result": [{
                    "id": "123456",
                    "source": "MED",
                    "title": "Evidence synthesis in medicine",
                    "abstractText": "A clinical evidence abstract.",
                    "doi": "10.1000/MED.ABC",
                    "pmid": "123456",
                    "journalTitle": "Journal of Evidence",
                    "pubYear": "2024",
                    "authorString": "Ada Lovelace, Alan Turing",
                    "citedByCount": "9",
                    "isOpenAccess": "Y",
                }],
            },
        }
        with patch(
            "omni_hub.retrieval.biomedical.http_get_json",
            return_value=fake,
        ) as mock:
            records = EuropePMCSource().retrieve("evidence medicine", limit=1)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.source, "europe_pmc")
        self.assertEqual(rec.canonical_id, "doi:10.1000/med.abc")
        self.assertEqual(rec.score, 9.0)
        self.assertEqual(rec.metadata["pmid"], "123456")
        self.assertEqual(rec.metadata["is_open_access"], True)
        self.assertEqual(mock.call_args.kwargs["params"]["format"], "json")

    def test_pubmed_search_then_summary_parses_articles(self) -> None:
        from omni_hub.retrieval.biomedical import PubMedSource

        esearch = {"esearchresult": {"idlist": ["12345"]}}
        esummary = {
            "result": {
                "uids": ["12345"],
                "12345": {
                    "uid": "12345",
                    "title": "Clinical evidence review",
                    "fulljournalname": "PubMed Test Journal",
                    "pubdate": "2024 Jan",
                    "authors": [{"name": "Ada Lovelace"}],
                    "articleids": [{"idtype": "doi", "value": "10.2000/PUBMED.X"}],
                },
            },
        }
        # efetch (abstracts) goes through http_get_text, not http_get_json —
        # mock it too so the test stays network-free and we can assert the
        # v0.46 abstract enrichment lands.
        efetch_xml = (
            '<?xml version="1.0"?><PubmedArticleSet><PubmedArticle>'
            "<MedlineCitation><PMID>12345</PMID><Article><Abstract>"
            "<AbstractText>A clinical evidence abstract.</AbstractText>"
            "</Abstract></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
        )
        with patch(
            "omni_hub.retrieval.biomedical.http_get_json",
            side_effect=[esearch, esummary],
        ) as mock, patch(
            "omni_hub.retrieval.biomedical.http_get_text",
            return_value=(efetch_xml, {}),
        ) as text_mock:
            records = PubMedSource(
                email="dev@example.org",
                api_key="ncbi-key",
            ).retrieve("clinical evidence", limit=1)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.source, "pubmed")
        self.assertEqual(rec.canonical_id, "doi:10.2000/pubmed.x")
        self.assertEqual(rec.url, "https://pubmed.ncbi.nlm.nih.gov/12345/")
        self.assertEqual(rec.metadata["pmid"], "12345")
        self.assertEqual(rec.metadata["authors"], ["Ada Lovelace"])
        # v0.46: efetch abstract is captured and preferred as the snippet.
        self.assertEqual(rec.metadata["abstract"], "A clinical evidence abstract.")
        self.assertEqual(rec.snippet, "A clinical evidence abstract.")
        self.assertEqual(mock.call_args_list[0].kwargs["params"]["db"], "pubmed")
        self.assertEqual(mock.call_args_list[1].kwargs["params"]["id"], "12345")
        self.assertEqual(text_mock.call_count, 1)  # one efetch call


class StructuredFactSourceTests(unittest.TestCase):
    def test_data_commons_requires_api_key(self) -> None:
        from omni_hub.retrieval.datacommons import DataCommonsSource

        with self.assertRaises(RetrievalError):
            DataCommonsSource(api_key="").retrieve("place=country/USA stat_var=Count_Person")

    def test_data_commons_parses_stat_series(self) -> None:
        from omni_hub.retrieval.datacommons import DataCommonsSource

        fake = {"series": {"2020": 331501080, "2021": 331893745}}
        with patch(
            "omni_hub.retrieval.datacommons.http_get_json",
            return_value=fake,
        ) as mock:
            records = DataCommonsSource(api_key="dc-key").retrieve(
                "place=country/USA stat_var=Count_Person",
                limit=1,
            )
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.source, "data_commons")
        self.assertEqual(rec.canonical_id, "dc:country/USA:Count_Person")
        self.assertEqual(rec.metadata["latest_year"], "2021")
        self.assertEqual(rec.metadata["latest_value"], 331893745)
        self.assertEqual(mock.call_args.kwargs["params"]["place"], "country/USA")


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

    def test_keeps_orcid_ror_topics_and_oa_pdf(self) -> None:
        # v0.46: API-native author/lab/ORCID structure preserved (closes the
        # author/lab/ORCID gap without parsing a PDF header).
        adapter = OpenAlexSource()
        fake = {"results": [{
            "id": "https://openalex.org/W2",
            "display_name": "Paper",
            "publication_year": 2026,
            "doi": "https://doi.org/10.y",
            "cited_by_count": 5,
            "authorships": [{
                "is_corresponding": True,
                "author": {"display_name": "Lin",
                           "orcid": "https://orcid.org/0000-0002-1825-0097"},
                "institutions": [{"display_name": "MIT",
                                  "ror": "https://ror.org/042nb2s44",
                                  "country_code": "US"}],
            }],
            "topics": [{"display_name": "Reinforcement Learning"},
                       {"display_name": "Robotics"}],
            "best_oa_location": {"pdf_url": "https://x/paper.pdf"},
            "open_access": {"is_oa": True, "oa_url": "https://x/oa"},
            "abstract_inverted_index": {"Hello": [0]},
        }]}
        with patch("omni_hub.retrieval.openalex.http_get_json", return_value=fake):
            rec = adapter.retrieve("q", limit=1)[0]
        ad = rec.metadata["authors_detailed"][0]
        self.assertEqual(ad["orcid"], "https://orcid.org/0000-0002-1825-0097")
        self.assertTrue(ad["is_corresponding"])
        self.assertEqual(ad["institutions"][0]["ror"], "https://ror.org/042nb2s44")
        self.assertEqual(ad["institutions"][0]["display_name"], "MIT")
        self.assertEqual(rec.metadata["topics"],
                         ["Reinforcement Learning", "Robotics"])
        self.assertEqual(rec.metadata["oa_pdf_url"], "https://x/paper.pdf")


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

    def test_requests_tldr_and_reference_fields_not_embedding(self) -> None:
        adapter = SemanticScholarSource()
        with patch(
            "omni_hub.retrieval.semantic_scholar.http_get_json",
            return_value={"data": []},
        ) as mock:
            adapter.retrieve("x")
        fields = mock.call_args.kwargs["params"]["fields"]
        self.assertIn("tldr", fields)
        self.assertIn("influentialCitationCount", fields)
        self.assertIn("references.externalIds", fields)
        self.assertNotIn("embedding", fields)        # no vector consumer

    def test_prefers_tldr_and_keeps_citation_graph_edges(self) -> None:
        adapter = SemanticScholarSource()
        fake = {"data": [{
            "title": "P",
            "abstract": "long abstract that should be ignored when tldr present",
            "tldr": {"text": "One-line summary."},
            "citationCount": 10,
            "influentialCitationCount": 4,
            "authors": [{"name": "A"}],
            "externalIds": {"DOI": "10.Z"},
            "references": [
                {"externalIds": {"DOI": "10.AAA"}},
                {"externalIds": {"ArXiv": "2501.00001"}},
                {"externalIds": {}},
            ],
        }]}
        with patch(
            "omni_hub.retrieval.semantic_scholar.http_get_json", return_value=fake,
        ):
            rec = adapter.retrieve("x")[0]
        self.assertEqual(rec.snippet, "One-line summary.")
        self.assertEqual(rec.metadata["influential_citation_count"], 4)
        self.assertEqual(rec.metadata["reference_ids"],
                         ["doi:10.aaa", "arxiv:2501.00001"])
        self.assertEqual(rec.metadata["reference_count"], 3)


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
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        search_query = query["search_query"][0]
        self.assertIn("cat:cs.AI", search_query)
        self.assertIn("cat:cs.LG", search_query)

    def test_multi_word_query_is_urlencoded(self) -> None:
        adapter = ArxivSource()
        with patch(
            "omni_hub.retrieval.arxiv_api.http_get_text",
            return_value=("<feed xmlns=\"http://www.w3.org/2005/Atom\"></feed>", {}),
        ) as mock:
            adapter.retrieve("truthfulqa hallucination detection", domain="ai_progress")
        url = mock.call_args[0][0]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertNotIn(" ", url)
        self.assertEqual(
            query["search_query"][0],
            "(cat:cs.AI OR cat:cs.LG OR cat:cs.CL) AND all:truthfulqa hallucination detection",
        )


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

    def test_default_domain_cascades_cover_core_profiles(self) -> None:
        # v0.19: policy split into us_policy + cn_policy + 6 new domains
        for d in ("engineering", "research", "finance",
                  "us_policy", "cn_policy",
                  "international_relations", "ai_progress", "default",
                  "meta", "fitness_wellness", "cooking", "travel",
                  "marketing", "enterprise"):
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


# ===========================================================================
# v0.10 — Channel ABC + doctor (V10-6)
# ===========================================================================


class HealthProbeTests(unittest.TestCase):
    def test_probe_default_when_no_check_method(self) -> None:
        from omni_hub.retrieval.health import probe_source

        class Bare:
            name = "bare"

        report = probe_source(Bare())
        self.assertEqual(report.status, "ok")
        self.assertIn("no probe", report.detail)
        self.assertEqual(report.tier, 0)

    def test_probe_calls_check_when_present(self) -> None:
        from omni_hub.retrieval.health import probe_source

        class WithProbe:
            name = "wp"
            tier = 1
            def check(self): return ("warn", "missing key")

        report = probe_source(WithProbe())
        self.assertEqual(report.status, "warn")
        self.assertEqual(report.detail, "missing key")
        self.assertEqual(report.tier, 1)

    def test_probe_collapses_exceptions_to_error(self) -> None:
        from omni_hub.retrieval.health import probe_source

        class Boom:
            name = "boom"
            def check(self):
                raise RuntimeError("rate limited")

        report = probe_source(Boom())
        self.assertEqual(report.status, "error")
        self.assertIn("rate limited", report.detail)

    def test_probe_rejects_malformed_return(self) -> None:
        from omni_hub.retrieval.health import probe_source

        class Bad:
            name = "bad"
            def check(self): return "not a tuple"

        report = probe_source(Bad())
        self.assertEqual(report.status, "error")

    def test_env_var_probe_masks_secret(self) -> None:
        import os
        from omni_hub.retrieval.health import env_var_probe
        os.environ["__TEST_KEY__"] = "sk-deadbeef1234"
        try:
            status, detail = env_var_probe("__TEST_KEY__")
            self.assertEqual(status, "ok")
            self.assertIn("sk-d", detail)
            self.assertNotIn("deadbeef", detail)  # masked
        finally:
            del os.environ["__TEST_KEY__"]

    def test_env_var_probe_reports_unset(self) -> None:
        from omni_hub.retrieval.health import env_var_probe
        status, _detail = env_var_probe("__NEVER_SET_VAR_XYZ__")
        self.assertEqual(status, "off")

    def test_probe_all_runs_in_order(self) -> None:
        from omni_hub.retrieval.health import probe_all, summarise
        class A: name, tier = "a", 0
        class B: name, tier = "b", 1
        reports = probe_all([A(), B()])
        self.assertEqual([r.name for r in reports], ["a", "b"])
        s = summarise(reports)
        self.assertEqual(s["ok"], 2)

    def test_builtin_sources_all_have_tier_and_check(self) -> None:
        for name, source in builtin_sources().items():
            self.assertTrue(hasattr(source, "tier"), f"{name} missing tier")
            self.assertIn(source.tier, (0, 1, 2), f"{name} bad tier {source.tier}")
            self.assertTrue(callable(getattr(source, "check", None)),
                            f"{name} missing check()")


# ===========================================================================
# v0.10 — HF Daily Papers (V10-4)
# ===========================================================================


class HFDailyPapersTests(unittest.TestCase):
    def test_parses_daily_papers_response(self) -> None:
        from omni_hub.retrieval.hf_daily_papers import HFDailyPapersSource

        fake = [{
            "paper": {
                "id": "2510.01234",
                "title": "A Cool Anthropic Paper",
                "summary": "Anthropic released Claude Agent SDK.",
                "authors": [{"name": "Vaswani"}, {"name": "Shazeer"}],
                "upvotes": 42,
                "numModels": 2,
                "numDatasets": 1,
            },
        }, {"paper": {
            "id": "2510.05678", "title": "Unrelated", "summary": "",
            "authors": [], "upvotes": 1,
        }}]
        with patch(
            "omni_hub.retrieval.hf_daily_papers.http_get_json",
            return_value=fake,
        ):
            records = HFDailyPapersSource(days_window=1).retrieve("Anthropic")
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.canonical_id, "arxiv:2510.01234")
        self.assertEqual(rec.score, 42.0)
        self.assertEqual(rec.metadata["upvotes"], 42)
        self.assertEqual(rec.metadata["num_models"], 2)


# ===========================================================================
# v0.10 — Photo (V10-9)
# ===========================================================================


class PhotoTests(unittest.TestCase):
    def test_unsplash_uses_client_id_header(self) -> None:
        from omni_hub.retrieval.photo import UnsplashSource
        fake = {"results": [{
            "id": "abc123",
            "description": "Mountain at dawn",
            "user": {"name": "Ansel"},
            "links": {"html": "https://unsplash.com/photos/abc123"},
            "urls": {"regular": "https://images.unsplash.com/x"},
            "likes": 200, "width": 4000, "height": 3000, "color": "#aabbcc",
        }]}
        with patch(
            "omni_hub.retrieval.photo.http_get_json", return_value=fake,
        ) as mock:
            records = UnsplashSource(api_key="key123").retrieve("dawn")
        self.assertEqual(mock.call_args.kwargs["headers"]["Authorization"],
                         "Client-ID key123")
        self.assertEqual(records[0].canonical_id, "unsplash:abc123")
        self.assertEqual(records[0].score, 200.0)

    def test_unsplash_raises_when_key_missing(self) -> None:
        from omni_hub.retrieval.photo import UnsplashSource
        from omni_hub.retrieval.base import RetrievalError
        src = UnsplashSource(api_key="")
        with self.assertRaises(RetrievalError):
            src.retrieve("anything")

    def test_pexels_uses_authorization_header(self) -> None:
        from omni_hub.retrieval.photo import PexelsSource
        fake = {"photos": [{
            "id": 99, "alt": "Sunset", "url": "https://pexels.com/p/99",
            "photographer": "Ansel", "photographer_url": "https://...",
            "src": {"large": "https://...x.jpg"},
            "width": 1000, "height": 600, "avg_color": "#ffaa00",
        }]}
        with patch(
            "omni_hub.retrieval.photo.http_get_json", return_value=fake,
        ) as mock:
            records = PexelsSource(api_key="pkey").retrieve("sun")
        self.assertEqual(mock.call_args.kwargs["headers"]["Authorization"], "pkey")
        self.assertEqual(records[0].canonical_id, "pexels:99")


# ===========================================================================
# v0.10 — US Gov three-piece (V10-5)
# ===========================================================================


class USGovTests(unittest.TestCase):
    def test_federal_register_parses_results(self) -> None:
        from omni_hub.retrieval.us_gov import FederalRegisterSource
        fake = {"results": [{
            "title": "Final Rule on X",
            "document_number": "2026-12345",
            "abstract": "EPA finalises the rule.",
            "publication_date": "2026-05-01",
            "type": "Rule",
            "html_url": "https://federalregister.gov/d/2026-12345",
            "agencies": [{"name": "Environmental Protection Agency"}],
        }]}
        with patch(
            "omni_hub.retrieval.us_gov.http_get_json", return_value=fake,
        ):
            records = FederalRegisterSource().retrieve("EPA rule")
        self.assertEqual(records[0].canonical_id, "fedreg:2026-12345")
        self.assertEqual(records[0].metadata["document_type"], "Rule")

    def test_regulations_gov_uses_x_api_key(self) -> None:
        from omni_hub.retrieval.us_gov import RegulationsGovSource
        fake = {"data": [{
            "id": "EPA-HQ-OAR-2026-0001",
            "attributes": {
                "title": "Comment period notice",
                "documentType": "Notice", "agencyId": "EPA",
                "postedDate": "2026-05-01", "docketId": "EPA-HQ-OAR-2026",
                "subject": "Public input.",
            },
        }]}
        with patch(
            "omni_hub.retrieval.us_gov.http_get_json", return_value=fake,
        ) as mock:
            records = RegulationsGovSource(api_key="datakey").retrieve("EPA")
        self.assertEqual(mock.call_args.kwargs["headers"]["X-Api-Key"], "datakey")
        self.assertEqual(records[0].canonical_id, "regulations:EPA-HQ-OAR-2026-0001")

    def test_congress_gov_passes_api_key_in_params(self) -> None:
        from omni_hub.retrieval.us_gov import CongressGovSource
        fake = {"bills": [{
            "congress": 119, "type": "HR", "number": 42,
            "title": "Bill to do X",
            "updateDate": "2026-05-01",
            "latestAction": {"text": "Passed the House."},
        }]}
        with patch(
            "omni_hub.retrieval.us_gov.http_get_json", return_value=fake,
        ) as mock:
            records = CongressGovSource(api_key="datakey").retrieve("X")
        self.assertEqual(mock.call_args.kwargs["params"]["api_key"], "datakey")
        self.assertEqual(records[0].canonical_id, "congress:119-hr-42")


# ===========================================================================
# v0.11 — Legal + archive sources for global-truth evidence
# ===========================================================================


class LegalAndArchiveSourceTests(unittest.TestCase):
    def test_courtlistener_parses_search_results(self) -> None:
        from omni_hub.retrieval.legal import CourtListenerSource

        fake = {"results": [{
            "cluster_id": 410113,
            "caseName": "Roe v. Wade",
            "absolute_url": "/opinion/108713/roe-v-wade/",
            "snippet": "Privacy and constitutional law.",
            "court": "scotus",
            "dateFiled": "1973-01-22",
            "docketNumber": "70-18",
            "citation": ["410 U.S. 113"],
        }]}
        with patch(
            "omni_hub.retrieval.legal.http_get_json",
            return_value=fake,
        ) as mock:
            records = CourtListenerSource().retrieve("privacy", limit=1)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.source, "courtlistener")
        self.assertEqual(rec.canonical_id, "courtlistener:410113")
        self.assertEqual(rec.url, "https://www.courtlistener.com/opinion/108713/roe-v-wade/")
        self.assertEqual(rec.metadata["court"], "scotus")
        self.assertEqual(mock.call_args.kwargs["params"]["q"], "privacy")

    def test_internet_archive_parses_advanced_search_docs(self) -> None:
        from omni_hub.retrieval.archive import InternetArchiveSource

        fake = {"response": {"docs": [{
            "identifier": "evidence-book",
            "title": "Evidence Book",
            "description": "Archived public-domain evidence.",
            "date": "1920",
            "creator": "Archive Author",
            "collection": ["opensource"],
        }]}}
        with patch(
            "omni_hub.retrieval.archive.http_get_json",
            return_value=fake,
        ) as mock:
            records = InternetArchiveSource().retrieve("evidence", limit=1)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.source, "internet_archive")
        self.assertEqual(rec.canonical_id, "ia:evidence-book")
        self.assertEqual(rec.url, "https://archive.org/details/evidence-book")
        self.assertEqual(rec.metadata["creator"], "Archive Author")
        self.assertIn("fl[]", mock.call_args.kwargs["params"])

    def test_wayback_cdx_requires_urlish_query_and_parses_snapshots(self) -> None:
        from omni_hub.retrieval.archive import WaybackCDXSource

        self.assertEqual(WaybackCDXSource().retrieve("not a url"), [])

        fake = [[
            "urlkey", "timestamp", "original", "mimetype", "statuscode", "digest",
        ], [
            "com,example)/", "20240102030405", "https://example.com/",
            "text/html", "200", "DIGEST123",
        ]]
        with patch(
            "omni_hub.retrieval.archive.http_get_json",
            return_value=fake,
        ) as mock:
            records = WaybackCDXSource().retrieve("https://example.com/", limit=1)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.source, "wayback_cdx")
        self.assertEqual(rec.canonical_id, "wayback:DIGEST123")
        self.assertEqual(rec.url, "https://web.archive.org/web/20240102030405/https://example.com/")
        self.assertEqual(mock.call_args.kwargs["params"]["url"], "https://example.com/")


# ===========================================================================
# v0.10 — twitterapi.io (V10-7)
# ===========================================================================


class TwitterApiIoTests(unittest.TestCase):
    def test_parses_advanced_search(self) -> None:
        from omni_hub.retrieval.twitterapi_io import TwitterApiIoSource
        fake = {"tweets": [{
            "id": "12345",
            "text": "Anthropic just released Claude Agent SDK!",
            "author": {"userName": "anthropicai", "name": "Anthropic"},
            "createdAt": "2026-05-01T10:00:00Z",
            "lang": "en",
            "likeCount": 100, "retweetCount": 50, "replyCount": 5, "viewCount": 1000,
        }]}
        with patch(
            "omni_hub.retrieval.twitterapi_io.http_get_json", return_value=fake,
        ) as mock:
            records = TwitterApiIoSource(api_key="tw-key").retrieve("Anthropic")
        self.assertEqual(
            mock.call_args.kwargs["headers"]["Authorization"], "Bearer tw-key",
        )
        rec = records[0]
        self.assertEqual(rec.canonical_id, "x:tweet:12345")
        self.assertEqual(rec.url, "https://x.com/anthropicai/status/12345")
        # like + 2*retweet = 100 + 100 = 200
        self.assertEqual(rec.score, 200.0)


# ===========================================================================
# v0.10 — International (ACLED + WB + IMF) (V10-8)
# ===========================================================================


class IntlTests(unittest.TestCase):
    def test_acled_parses_events(self) -> None:
        from omni_hub.retrieval.intl import ACLEDSource
        # ACLED's 2024 OAuth2 scheme: retrieve() first mints a bearer token
        # via an x-www-form-urlencoded password grant (http_post_json), then
        # reads events (http_get_json).  Mock both so the test stays
        # network-free and pins the new request shapes.
        token_resp = {"access_token": "oauth-token", "expires_in": 86400}
        fake = {"data": [{
            "event_id_cnty": "777",
            "event_date": "2026-05-20",
            "event_type": "Protests",
            "actor1": "Protesters (Country)",
            "actor2": "Police Forces",
            "country": "Country",
            "fatalities": 3,
            "notes": "Demonstration in capital.",
            "source_scale": "https://news.example/article",
        }]}
        with patch(
            "omni_hub.retrieval.intl.http_post_json", return_value=token_resp,
        ) as token_mock, patch(
            "omni_hub.retrieval.intl.http_get_json", return_value=fake,
        ):
            records = ACLEDSource(
                email="u@x.com", password="pw",
            ).retrieve("Protesters")
        self.assertEqual(
            token_mock.call_args.kwargs["content_type"],
            "application/x-www-form-urlencoded",
        )
        self.assertEqual(records[0].canonical_id, "acled:777")
        self.assertEqual(records[0].score, 3.0)

    def test_world_bank_filters_indicators_by_substring(self) -> None:
        from omni_hub.retrieval.intl import WorldBankSource
        fake_resp = [
            {"page": 1, "pages": 1, "total": 2},
            [
                {"id": "NY.GDP.MKTP.CD", "name": "GDP (current US$)",
                 "sourceNote": "Gross domestic product...",
                 "source": {"value": "World Development Indicators"},
                 "topics": [{"value": "Economy"}]},
                {"id": "SP.POP.TOTL", "name": "Population, total",
                 "sourceNote": "Total population...",
                 "source": {"value": "World Development Indicators"},
                 "topics": [{"value": "Health"}]},
            ],
        ]
        with patch(
            "omni_hub.retrieval.intl.http_get_json", return_value=fake_resp,
        ):
            records = WorldBankSource().retrieve("GDP")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].canonical_id, "wb:indicator:NY.GDP.MKTP.CD")

    def test_imf_walks_sdmx_dataflows(self) -> None:
        from omni_hub.retrieval.intl import IMFSource
        fake = {
            "Structure": {
                "Dataflows": {
                    "Dataflow": [
                        {
                            "@id": "WEO",
                            "Name": [{"@xml:lang": "en", "#text": "World Economic Outlook"}],
                        },
                        {
                            "@id": "IFS",
                            "Name": {"@xml:lang": "en", "#text": "International Financial Statistics"},
                        },
                    ],
                },
            },
        }
        with patch(
            "omni_hub.retrieval.intl.http_get_json", return_value=fake,
        ):
            records = IMFSource().retrieve("Outlook")
        self.assertEqual(records[0].canonical_id, "imf:WEO")


# ===========================================================================
# v0.10 — Finance (V10-2)
# ===========================================================================


class FinanceTests(unittest.TestCase):
    def test_edgar_parses_full_text_search(self) -> None:
        from omni_hub.retrieval.finance import EdgarSource
        fake = {"hits": {"hits": [{
            "_id": "0001193125-26-123456",
            "_source": {
                "form": "10-K", "file_date": "2026-04-15",
                "display_names": ["Anthropic, PBC"],
                "ciks": ["0001990000"],
                "adsh": "0001193125-26-123456",
            },
        }]}}
        with patch(
            "omni_hub.retrieval.finance.http_get_json", return_value=fake,
        ):
            records = EdgarSource().retrieve("Anthropic")
        rec = records[0]
        self.assertEqual(rec.canonical_id, "edgar:0001193125-26-123456")
        self.assertEqual(rec.metadata["form"], "10-K")
        self.assertIn("Anthropic", rec.title)
        # URL must use the accession-number-without-dashes form
        self.assertIn("000119312526123456", rec.url)

    def test_fred_orders_by_popularity(self) -> None:
        from omni_hub.retrieval.finance import FREDSource
        fake = {"seriess": [{
            "id": "UNRATE", "title": "Unemployment Rate", "popularity": 99,
            "frequency": "Monthly", "units": "Percent", "seasonal_adjustment": "SA",
            "observation_start": "1948-01-01", "observation_end": "2026-04-01",
            "notes": "Civilian unemployment rate.",
        }]}
        with patch(
            "omni_hub.retrieval.finance.http_get_json", return_value=fake,
        ) as mock:
            records = FREDSource(api_key="fkey").retrieve("unemployment")
        # retrieve() now fires a search call + up-to-3 latest-observation
        # calls; the *search* request is the first call.
        search_params = mock.call_args_list[0].kwargs["params"]
        self.assertEqual(search_params["api_key"], "fkey")
        self.assertEqual(search_params["order_by"], "popularity")
        self.assertEqual(records[0].canonical_id, "fred:UNRATE")
        self.assertEqual(records[0].score, 99.0)

    def test_fred_enriches_top_series_with_latest_value(self) -> None:
        # v0.46: the top-N series get their latest observation fetched so
        # "what is GDP" answers with a number, not just a series link.
        from omni_hub.retrieval.finance import FREDSource
        search = {"seriess": [{
            "id": "GDP", "title": "Gross Domestic Product", "popularity": 95,
            "frequency": "Quarterly", "units": "Billions of Dollars",
            "observation_start": "1947-01-01", "observation_end": "2026-01-01",
            "notes": "Nominal GDP.",
        }]}
        observations = {"observations": [{"date": "2026-01-01", "value": "29123.4"}]}
        with patch(
            "omni_hub.retrieval.finance.http_get_json",
            side_effect=[search, observations],
        ):
            records = FREDSource(api_key="k").retrieve("GDP")
        rec = records[0]
        self.assertEqual(rec.metadata["latest_value"], "29123.4")
        self.assertEqual(rec.metadata["latest_value_date"], "2026-01-01")
        self.assertIn("29123.4", rec.snippet)

    def test_fred_observation_missing_value_is_skipped(self) -> None:
        # FRED encodes missing data as "."; it must not become a value.
        from omni_hub.retrieval.finance import FREDSource
        search = {"seriess": [{"id": "X", "title": "X", "popularity": 1, "notes": "n"}]}
        observations = {"observations": [{"date": "2026-01-01", "value": "."}]}
        with patch(
            "omni_hub.retrieval.finance.http_get_json",
            side_effect=[search, observations],
        ):
            records = FREDSource(api_key="k").retrieve("x")
        self.assertNotIn("latest_value", records[0].metadata)

    def test_edgar_resolve_cik(self) -> None:
        from omni_hub.retrieval.finance import EdgarSource
        tickers = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
        with patch("omni_hub.retrieval.finance.http_get_json", return_value=tickers):
            cik = EdgarSource().resolve_cik("aapl")
        self.assertEqual(cik, "0000320193")

    def test_edgar_company_concept_picks_latest_period(self) -> None:
        # v0.46: on-demand XBRL fetch turns "10-K link" into a number.
        from omni_hub.retrieval.finance import EdgarSource
        tickers = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
        concept = {
            "entityName": "Apple Inc.", "label": "Revenues",
            "units": {"USD": [
                {"end": "2024-09-30", "val": 391035000000, "fy": 2024,
                 "fp": "FY", "form": "10-K", "filed": "2024-11-01"},
                {"end": "2025-09-30", "val": 416000000000, "fy": 2025,
                 "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
            ]},
        }
        with patch(
            "omni_hub.retrieval.finance.http_get_json",
            side_effect=[tickers, concept],
        ):
            out = EdgarSource().company_concept("AAPL", "Revenues")
        self.assertEqual(out["value"], 416000000000)
        self.assertEqual(out["period_end"], "2025-09-30")
        self.assertEqual(out["cik"], "0000320193")
        self.assertEqual(out["unit"], "USD")

    def test_edgar_company_concept_is_not_in_cascade_retrieve(self) -> None:
        # Guard the audit's rule: the search connector must NOT auto-fetch
        # XBRL.  retrieve() makes exactly one (search) HTTP call.
        from omni_hub.retrieval.finance import EdgarSource
        fake = {"hits": {"hits": []}}
        with patch(
            "omni_hub.retrieval.finance.http_get_json", return_value=fake,
        ) as mock:
            EdgarSource().retrieve("Apple")
        self.assertEqual(mock.call_count, 1)


# ===========================================================================
# v0.10 — trafilatura bridge (V10-3)
# ===========================================================================


class TrafilaturaBridgeTests(unittest.TestCase):
    def test_returns_not_installed_when_missing(self) -> None:
        from omni_hub.connectors import trafilatura_bridge
        with patch.object(trafilatura_bridge.shutil, "which", return_value=None):
            text, status = trafilatura_bridge.extract_main_content(
                "<html></html>", "https://x",
            )
        self.assertEqual(status, "not_installed")
        self.assertEqual(text, "")

    def test_returns_empty_on_blank_input(self) -> None:
        from omni_hub.connectors import trafilatura_bridge
        with patch.object(trafilatura_bridge.shutil, "which", return_value="/usr/bin/trafilatura"):
            text, status = trafilatura_bridge.extract_main_content(
                "", "https://x",
            )
        self.assertEqual(status, "empty")

    def test_invokes_subprocess_with_markdown_flag(self) -> None:
        from omni_hub.connectors import trafilatura_bridge

        class FakeResult:
            returncode = 0
            stdout = "# Title\n\nBody text.\n"
            stderr = ""

        with patch.object(trafilatura_bridge.shutil, "which", return_value="/x/trafilatura"), \
             patch.object(trafilatura_bridge.subprocess, "run", return_value=FakeResult()) as mock_run:
            text, status = trafilatura_bridge.extract_main_content(
                "<html>...</html>", "https://x",
            )
        self.assertEqual(status, "ok")
        self.assertIn("Body text.", text)
        cmd = mock_run.call_args.args[0]
        self.assertIn("--output", cmd)
        self.assertIn("markdown", cmd)
        self.assertIn("-u", cmd)

    def test_json_metadata_path(self) -> None:
        from omni_hub.connectors import trafilatura_bridge

        class FakeResult:
            returncode = 0
            stdout = json.dumps({"title": "T", "text": "body", "author": "A"})
            stderr = ""

        with patch.object(trafilatura_bridge.shutil, "which", return_value="/x/trafilatura"), \
             patch.object(trafilatura_bridge.subprocess, "run", return_value=FakeResult()):
            payload, status = trafilatura_bridge.extract_with_metadata(
                "<html>...</html>", "https://x",
            )
        self.assertEqual(status, "ok")
        self.assertEqual(payload["title"], "T")
        self.assertEqual(payload["author"], "A")


# ===========================================================================
# v0.10 — defuddle patterns (V10-12)
# ===========================================================================


class ExtractorsTests(unittest.TestCase):
    def test_schema_org_extracts_article_body(self) -> None:
        from omni_hub.connectors.extractors import extract_schema_org_article_body
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@type": "NewsArticle", "articleBody": "Full article body here. " }
        </script>
        </head><body><div>Stub.</div></body></html>
        """
        body = extract_schema_org_article_body(html)
        self.assertIn("Full article body", body)

    def test_schema_org_picks_longest_across_multiple_blocks(self) -> None:
        from omni_hub.connectors.extractors import extract_schema_org_article_body
        html = """
        <script type="application/ld+json">{"articleBody": "short"}</script>
        <script type="application/ld+json">
        {"@graph": [{"articleBody": "the much longer body wins"}]}
        </script>
        """
        body = extract_schema_org_article_body(html)
        self.assertEqual(body, "the much longer body wins")

    def test_schema_org_sanity_trust_dom_when_close(self) -> None:
        from omni_hub.connectors.extractors import schema_org_sanity_check
        verdict, _longer = schema_org_sanity_check(
            "A reasonably long extracted DOM article body here.",
            '<script type="application/ld+json">{"articleBody": "short"}</script>',
        )
        self.assertEqual(verdict, "trust_dom")

    def test_schema_org_sanity_trust_schema_when_dom_short(self) -> None:
        from omni_hub.connectors.extractors import schema_org_sanity_check
        long_body = "Word " * 100   # 500 chars
        verdict, longer = schema_org_sanity_check(
            "tiny stub",
            f'<script type="application/ld+json">{{"articleBody": "{long_body}"}}</script>',
        )
        self.assertEqual(verdict, "trust_schema")
        self.assertIn("Word", longer)

    def test_schema_org_no_schema_returns_no_schema(self) -> None:
        from omni_hub.connectors.extractors import schema_org_sanity_check
        verdict, _ = schema_org_sanity_check("anything", "<html></html>")
        self.assertEqual(verdict, "no_schema")

    def test_site_extractor_picks_substack(self) -> None:
        from omni_hub.connectors.extractors import site_extractor_for
        extractor = site_extractor_for("https://someauthor.substack.com/p/post")
        self.assertIsNotNone(extractor)

    def test_site_extractor_picks_linkedin(self) -> None:
        from omni_hub.connectors.extractors import site_extractor_for
        extractor = site_extractor_for("https://www.linkedin.com/pulse/x")
        self.assertIsNotNone(extractor)

    def test_extract_with_site_override_no_override_for_random_host(self) -> None:
        from omni_hub.connectors.extractors import extract_with_site_override
        text, status = extract_with_site_override("<html></html>", "https://random.example.com/x")
        self.assertEqual(status, "no_override")


# ===========================================================================
# v0.10 — XHS bridge (V10-10) — subprocess mocked
# ===========================================================================


class XHSBridgeTests(unittest.TestCase):
    def test_check_reports_off_when_binary_missing(self) -> None:
        from omni_hub.retrieval.xhs import XiaohongshuSource
        src = XiaohongshuSource()
        with patch("omni_hub.retrieval.xhs.shutil.which", return_value=None):
            status, detail = src.check()
        self.assertEqual(status, "off")
        self.assertIn("xhs", detail)

    def test_retrieve_returns_empty_when_binary_missing(self) -> None:
        from omni_hub.retrieval.xhs import XiaohongshuSource
        src = XiaohongshuSource()
        with patch("omni_hub.retrieval.xhs.shutil.which", return_value=None):
            self.assertEqual(src.retrieve("anything"), [])

    def test_retrieve_parses_json_subprocess_output(self) -> None:
        from omni_hub.retrieval.xhs import XiaohongshuSource

        class FakeProc:
            returncode = 0
            stdout = json.dumps({"results": [{
                "note_id": "abc",
                "title": "好物分享",
                "desc": "测试笔记内容",
                "user": {"nickname": "alice"},
                "url": "https://xiaohongshu.com/notes/abc",
                "liked_count": 99,
            }]})
            stderr = ""

        src = XiaohongshuSource()
        with patch("omni_hub.retrieval.xhs.shutil.which", return_value="/x/xhs"), \
             patch("omni_hub.retrieval.xhs.subprocess.run", return_value=FakeProc()):
            records = src.retrieve("好物")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].canonical_id, "xhs:note:abc")
        self.assertEqual(records[0].metadata["lang"], "zh")


# ===========================================================================
# v0.10 — Cascade integration with new domains + builtin_sources extension
# ===========================================================================


class V10CascadeIntegrationTests(unittest.TestCase):
    def test_builtin_sources_registers_all_v10_connectors(self) -> None:
        sources = builtin_sources()
        for expected in (
            "brave_search", "crossref", "wikidata",
            "wikidata_sparql", "europe_pmc", "pubmed",
            "data_commons", "courtlistener", "internet_archive", "wayback_cdx",
            "edgar", "fred", "hf_daily_papers",
            "federal_register", "regulations_gov", "congress_gov",
            "acled", "world_bank", "imf",
            "unsplash", "pexels", "x_twitter",
            "xiaohongshu", "wechat_mp",
        ):
            self.assertIn(expected, sources, f"missing {expected}")

    def test_default_domain_cascades_extended(self) -> None:
        self.assertIn("wikidata", DEFAULT_DOMAIN_CASCADES["default"])
        self.assertIn("brave_search", DEFAULT_DOMAIN_CASCADES["default"])
        self.assertIn("crossref", DEFAULT_DOMAIN_CASCADES["research"])
        self.assertIn("europe_pmc", DEFAULT_DOMAIN_CASCADES["research"])
        self.assertIn("pubmed", DEFAULT_DOMAIN_CASCADES["biomedical"])
        self.assertIn("data_commons", DEFAULT_DOMAIN_CASCADES["statistics"])
        self.assertIn("courtlistener", DEFAULT_DOMAIN_CASCADES["law"])
        self.assertIn("internet_archive", DEFAULT_DOMAIN_CASCADES["default"])
        self.assertIn("hf_daily_papers", DEFAULT_DOMAIN_CASCADES["ai_progress"])
        self.assertIn("edgar", DEFAULT_DOMAIN_CASCADES["finance"])
        self.assertIn("fred", DEFAULT_DOMAIN_CASCADES["finance"])
        self.assertIn("acled", DEFAULT_DOMAIN_CASCADES["international_relations"])
        self.assertIn("federal_register", DEFAULT_DOMAIN_CASCADES["us_policy"])
        self.assertIn("pexels", DEFAULT_DOMAIN_CASCADES["photography"])
        # Tier-2 socials: bluesky + mastodon + hackernews primary; x_twitter
        # paid (TwitterAPI.io); gdelt for news context.  reddit dropped — its
        # data-access API is approval-gated (non-commercial research request).
        self.assertEqual(
            DEFAULT_DOMAIN_CASCADES["social_en"],
            ["bluesky", "mastodon", "hackernews", "x_twitter", "gdelt"],
        )
        # v0.20: social_zh expanded with weibo + bilibili in addition to
        # the original xhs + wechat_mp; assert the head order is stable.
        self.assertEqual(
            DEFAULT_DOMAIN_CASCADES["social_zh"][:2], ["xiaohongshu", "wechat_mp"],
        )
        self.assertIn("weibo", DEFAULT_DOMAIN_CASCADES["social_zh"])
        self.assertIn("bilibili", DEFAULT_DOMAIN_CASCADES["social_zh"])


class RetrieveDoctorCliTests(unittest.TestCase):
    def test_doctor_returns_per_source_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_cli(Path(tmp), ["retrieve-doctor"])
        self.assertEqual(result["status"], "succeeded")
        out = result["output"]
        self.assertIn("rows", out)
        # Every registered source should produce one row
        names = {row["name"] for row in out["rows"]}
        self.assertIn("openalex", names)
        self.assertIn("edgar", names)
        self.assertIn("xiaohongshu", names)
        # Summary should cover all of ok / warn / off / error
        self.assertIn("ok", out["summary"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
