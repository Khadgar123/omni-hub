"""PubMed efetch abstract parsing (v0.46 connector-completeness fix).

esummary (used for PubMed metadata) does not return abstracts; the new
efetch path does.  We test the pure XML parser network-free.
"""

import unittest

from omni_hub.retrieval.biomedical import _parse_pubmed_abstracts_xml

_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>12345</PMID>
      <Article>
        <Abstract>
          <AbstractText Label="BACKGROUND">Creatine supplementation is studied.</AbstractText>
          <AbstractText Label="RESULTS">It increases strength.</AbstractText>
        </Abstract>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>67890</PMID>
      <Article>
        <Abstract><AbstractText>A single-block abstract.</AbstractText></Abstract>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>99999</PMID>
      <Article><Abstract></Abstract></Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>"""


class PubmedAbstractParseTests(unittest.TestCase):
    def test_labelled_and_single(self) -> None:
        out = _parse_pubmed_abstracts_xml(_XML)
        self.assertEqual(set(out), {"12345", "67890"})  # 99999 has no text → skipped
        self.assertIn("BACKGROUND: Creatine supplementation", out["12345"])
        self.assertIn("RESULTS: It increases strength", out["12345"])
        self.assertEqual(out["67890"], "A single-block abstract.")

    def test_malformed_is_empty(self) -> None:
        self.assertEqual(_parse_pubmed_abstracts_xml("<not-closed"), {})
        self.assertEqual(_parse_pubmed_abstracts_xml(""), {})
        self.assertEqual(_parse_pubmed_abstracts_xml("   "), {})


if __name__ == "__main__":
    unittest.main()
