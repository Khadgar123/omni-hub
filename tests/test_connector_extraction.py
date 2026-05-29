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

from omni_hub.retrieval.openalex import OpenAlexSource


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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
