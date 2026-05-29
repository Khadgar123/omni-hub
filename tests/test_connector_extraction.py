"""Connector data-completeness tests (v0.49 — stop under-extraction).

Scholarly connectors must preserve the API's full structured payload in
``RetrievalRecord.metadata`` (citation graph, bibliographic detail,
affiliations) at the raw layer, even when the snippet stays short.  This is
the regression guard for the 2026-05-29 review's Q2/Q3 finding: "we only
captured part of what the API offered."
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.retrieval.arxiv_api import ArxivSource
from omni_hub.retrieval.crossref import CrossrefSource
from omni_hub.retrieval.openalex import OpenAlexSource
from omni_hub.retrieval.semantic_scholar import SemanticScholarSource


_FAKE_OPENALEX = {
    "results": [
        {
            "id": "https://openalex.org/W123",
            "display_name": "Attention Is All You Need",
            "publication_year": 2017,
            "doi": "https://doi.org/10.5555/3295222.3295349",
            "cited_by_count": 100000,
            "abstract_inverted_index": {"The": [0], "transformer": [1]},
            "authorships": [
                {
                    "author": {
                        "display_name": "A Vaswani",
                        "orcid": "https://orcid.org/0000-0001",
                    },
                    "is_corresponding": True,
                    "institutions": [
                        {
                            "display_name": "Google Brain",
                            "ror": "https://ror.org/x",
                            "country_code": "US",
                        }
                    ],
                }
            ],
            "topics": [{"display_name": "Deep Learning"}],
            "concepts": [
                {
                    "display_name": "Transformer",
                    "score": 0.9,
                    "level": 2,
                    "wikidata": "https://www.wikidata.org/wiki/Q1",
                }
            ],
            "keywords": [{"display_name": "self-attention"}],
            "biblio": {
                "volume": "30",
                "issue": "",
                "first_page": "5998",
                "last_page": "6008",
            },
            "grants": [{"funder_display_name": "Google", "award_id": "N/A"}],
            "referenced_works": [
                "https://openalex.org/W1",
                "https://openalex.org/W2",
            ],
            "related_works": ["https://openalex.org/W9"],
            "is_retracted": False,
            "language": "en",
            "best_oa_location": {"pdf_url": "https://arxiv.org/pdf/1706.03762"},
            "open_access": {"is_oa": True, "oa_url": "https://arxiv.org/abs/1706.03762"},
        }
    ]
}


class OpenAlexExtractionTests(unittest.TestCase):
    def _retrieve(self):
        with patch(
            "omni_hub.retrieval.openalex.http_get_json",
            return_value=_FAKE_OPENALEX,
        ):
            return OpenAlexSource(mailto="").retrieve("transformer", limit=5)

    def test_captures_citation_graph(self) -> None:
        md = self._retrieve()[0].metadata
        # The single most valuable previously-dropped field.
        self.assertEqual(
            md["referenced_works"],
            ["https://openalex.org/W1", "https://openalex.org/W2"],
        )
        self.assertEqual(md["related_works"], ["https://openalex.org/W9"])

    def test_captures_biblio_concepts_keywords_grants(self) -> None:
        md = self._retrieve()[0].metadata
        self.assertEqual(md["biblio"]["first_page"], "5998")
        self.assertEqual(md["biblio"]["last_page"], "6008")
        self.assertEqual(md["concepts"][0]["display_name"], "Transformer")
        self.assertAlmostEqual(md["concepts"][0]["score"], 0.9)
        self.assertEqual(md["keywords"], ["self-attention"])
        self.assertEqual(md["grants"][0]["funder"], "Google")
        self.assertFalse(md["is_retracted"])
        self.assertEqual(md["language"], "en")

    def test_no_regression_on_existing_fields(self) -> None:
        md = self._retrieve()[0].metadata
        self.assertEqual(md["doi"], "https://doi.org/10.5555/3295222.3295349")
        self.assertTrue(md["authors_detailed"][0]["orcid"])
        self.assertEqual(
            md["authors_detailed"][0]["institutions"][0]["display_name"],
            "Google Brain",
        )
        self.assertEqual(md["oa_pdf_url"], "https://arxiv.org/pdf/1706.03762")


_FAKE_CROSSREF = {
    "message": {
        "items": [
            {
                "title": ["Deep Learning"],
                "DOI": "10.1/x",
                "URL": "https://doi.org/10.1/x",
                "container-title": ["Nature"],
                "abstract": "<p>An abstract</p>",
                "is-referenced-by-count": 50,
                "reference-count": 30,
                "publisher": "Springer",
                "type": "journal-article",
                "published": {"date-parts": [[2024]]},
                "author": [
                    {
                        "given": "Jane", "family": "Doe",
                        "ORCID": "https://orcid.org/0000-0002",
                        "sequence": "first",
                        "affiliation": [{"name": "MIT"}],
                    }
                ],
                "link": [
                    {
                        "URL": "https://pub.example/full.pdf",
                        "content-type": "application/pdf",
                        "intended-application": "text-mining",
                    }
                ],
                "funder": [{"name": "NSF", "DOI": "10.13039/x", "award": ["1234"]}],
                "ISSN": ["1476-4687"],
                "subject": ["Multidisciplinary"],
                "volume": "500", "issue": "1", "page": "1-10",
            }
        ]
    }
}


class CrossrefExtractionTests(unittest.TestCase):
    def _md(self):
        with patch(
            "omni_hub.retrieval.crossref.http_get_json", return_value=_FAKE_CROSSREF,
        ):
            return CrossrefSource(mailto="").retrieve("deep learning", limit=5)[0].metadata

    def test_captures_full_text_links_and_orcid(self) -> None:
        md = self._md()
        self.assertEqual(md["full_text_links"][0]["url"], "https://pub.example/full.pdf")
        self.assertEqual(md["full_text_links"][0]["content_type"], "application/pdf")
        self.assertEqual(md["authors_detailed"][0]["orcid"], "https://orcid.org/0000-0002")
        self.assertEqual(md["authors_detailed"][0]["affiliations"], ["MIT"])

    def test_captures_funder_issn_subject_biblio(self) -> None:
        md = self._md()
        self.assertEqual(md["funder"][0]["name"], "NSF")
        self.assertEqual(md["funder"][0]["awards"], ["1234"])
        self.assertEqual(md["issn"], ["1476-4687"])
        self.assertEqual(md["subject"], ["Multidisciplinary"])
        self.assertEqual(md["volume"], "500")
        self.assertEqual(md["page"], "1-10")


_FAKE_S2 = {
    "data": [
        {
            "title": "T", "abstract": "A", "year": 2024,
            "authors": [{"name": "Jane Doe", "authorId": "1234"}],
            "venue": "ICLR", "citationCount": 10, "influentialCitationCount": 2,
            "tldr": {"text": "one-liner"}, "externalIds": {"DOI": "10.1/x"},
            "openAccessPdf": {"url": "https://x/p.pdf"},
            "references": [{"externalIds": {"ArXiv": "2401.00001"}}],
            "fieldsOfStudy": ["Computer Science"],
            "publicationDate": "2024-05-01",
            "publicationTypes": ["JournalArticle"],
            "journal": {"name": "ICLR", "pages": "1-10"},
        }
    ]
}


class SemanticScholarExtractionTests(unittest.TestCase):
    def _md(self):
        with patch(
            "omni_hub.retrieval.semantic_scholar.http_get_json", return_value=_FAKE_S2,
        ):
            return SemanticScholarSource(api_key="").retrieve("t", limit=5)[0].metadata

    def test_captures_author_id_and_fields(self) -> None:
        md = self._md()
        self.assertEqual(md["authors_detailed"][0]["author_id"], "1234")
        self.assertEqual(md["fields_of_study"], ["Computer Science"])
        self.assertEqual(md["publication_date"], "2024-05-01")
        self.assertEqual(md["publication_types"], ["JournalArticle"])
        self.assertEqual(md["journal"]["name"], "ICLR")

    def test_reference_graph_preserved(self) -> None:
        md = self._md()
        self.assertEqual(md["reference_ids"], ["arxiv:2401.00001"])


_FAKE_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <title>Test Paper</title>
    <summary>An abstract.</summary>
    <published>2025-01-01T00:00:00Z</published>
    <updated>2025-02-01T00:00:00Z</updated>
    <id>http://arxiv.org/abs/2501.00001v2</id>
    <author><name>Jane Doe</name><arxiv:affiliation>MIT</arxiv:affiliation></author>
    <category term="cs.LG"/>
    <arxiv:primary_category term="cs.AI"/>
    <arxiv:doi>10.1234/abc</arxiv:doi>
    <arxiv:journal_ref>NeurIPS 2025</arxiv:journal_ref>
    <arxiv:comment>Accepted at NeurIPS 2025</arxiv:comment>
  </entry>
</feed>"""


class ArxivExtractionTests(unittest.TestCase):
    def _md(self):
        with patch(
            "omni_hub.retrieval.arxiv_api.http_get_text",
            return_value=(_FAKE_ARXIV_XML, {}),
        ):
            return ArxivSource().retrieve("test", limit=5)[0].metadata

    def test_captures_acceptance_and_journal_signals(self) -> None:
        md = self._md()
        # The free-text comment is the cheapest acceptance signal in the feed.
        self.assertEqual(md["comment"], "Accepted at NeurIPS 2025")
        self.assertEqual(md["journal_ref"], "NeurIPS 2025")
        self.assertEqual(md["doi"], "10.1234/abc")
        self.assertEqual(md["primary_category"], "cs.AI")

    def test_captures_author_affiliation(self) -> None:
        md = self._md()
        self.assertEqual(md["authors_detailed"][0]["name"], "Jane Doe")
        self.assertEqual(md["authors_detailed"][0]["affiliation"], "MIT")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
