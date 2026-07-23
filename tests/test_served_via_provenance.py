"""Provenance: served_via flows cascade -> evidence -> claim support.

Review gap #4 ("降级源不一定差"): every record carries a DESCRIPTIVE
served_via (live | fallback | cache) kept separate from MEASURED quality, so
downstream can audit *how* evidence was obtained without assuming a
fallback/cache record is worse. This pins the full chain end-to-end and the
mark_served_via helper connectors use to flag a real internal fallback.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub import knowledge_plane
from omni_hub.retrieval.base import (
    SERVED_VIA_CACHE,
    SERVED_VIA_FALLBACK,
    SERVED_VIA_LIVE,
    RetrievalRecord,
    mark_served_via,
)


class MarkServedViaTests(unittest.TestCase):
    def test_stamps_metadata_in_place(self) -> None:
        recs = [
            RetrievalRecord(source="s", title="t", url="", snippet="x"),
            RetrievalRecord(source="s", title="u", url="", snippet="y"),
        ]
        out = mark_served_via(recs, SERVED_VIA_FALLBACK)
        self.assertIs(out, recs)
        self.assertTrue(all(r.metadata["served_via"] == "fallback" for r in recs))

    def test_constants_are_distinct(self) -> None:
        self.assertEqual(
            {SERVED_VIA_LIVE, SERVED_VIA_FALLBACK, SERVED_VIA_CACHE},
            {"live", "fallback", "cache"},
        )


class ServedViaReachesClaimTests(unittest.TestCase):
    def test_fallback_provenance_flows_to_claim_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            run_id = "prov"
            rd = root / ".omni" / "retrieval" / run_id
            rd.mkdir(parents=True)
            (rd / "run_manifest.json").write_text(
                json.dumps({
                    "run_id": run_id, "domain": "ai_progress",
                    "query": "scaling laws", "fusion": "rrf",
                    "sources_succeeded": ["arxiv"],
                }),
                encoding="utf-8",
            )
            # evidence record carrying served_via=fallback, as a real
            # fallback-marked connector would emit
            rec = {
                "source": "arxiv", "title": "Scaling", "url": "http://x/1",
                "cite_id": "R1",
                "snippet": "Large language models improve predictably as "
                           "compute and data scale up.",
                "canonical_id": "arxiv:1",
                "metadata": {"served_via": "fallback", "source_tier": 0},
            }
            (rd / "evidence.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")

            ing = knowledge_plane.ingest_retrieval_evidence(
                root, run_id=run_id, domain="ai_progress"
            )
            from omni_hub.proposals import ProposalStore
            ProposalStore(root).approve(ing["proposal_id"], reason="test")
            knowledge_plane.apply_wiki_proposal(root, ing["proposal_id"])

            claims = [
                json.loads(l)
                for l in (root / ".omni/claims.jsonl").read_text().splitlines()
                if l.strip()
            ]
            self.assertEqual(len(claims), 1)
            support = claims[0]["support"][0]
            # the descriptive provenance survived the whole pipeline
            self.assertEqual(support["served_via"], "fallback")
            self.assertIn("source_tier", support)


if __name__ == "__main__":
    unittest.main()
