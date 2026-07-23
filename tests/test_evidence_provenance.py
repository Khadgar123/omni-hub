"""v0.46 bronze provenance — raw_hash (content fingerprint) + license on
evidence records and raw markdown.  Closes the 'raw must be traceable'
gap without a heavyweight per-attempt ApiCallLedger.
"""

import json
import tempfile
import unittest
from pathlib import Path

from omni_hub.knowledge_plane import (
    _record_license,
    _record_raw_hash,
    _write_evidence_files,
    init_layout,
)


class EvidenceProvenanceTests(unittest.TestCase):
    def test_raw_hash_and_license_written_to_evidence_and_raw(self) -> None:
        rec = {
            "source": "github", "title": "Repo", "url": "https://gh/x",
            "snippet": "code", "canonical_id": "github:o/r",
            "metadata": {"license": "MIT"},
        }
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d).resolve()  # match production (ingest resolves the workspace)
            init_layout(ws)
            written = _write_evidence_files(ws, "engineering", "run1", [rec])
            ev = json.loads((ws / written[0]).read_text(encoding="utf-8"))
            raw_md = (ws / ev["raw_path"]).read_text(encoding="utf-8")
        self.assertEqual(len(ev["raw_hash"]), 64)                 # sha256 hex
        self.assertEqual(ev["license"], "MIT")
        self.assertEqual(_record_raw_hash(rec), ev["raw_hash"])   # stable
        self.assertIn(f"raw_hash: {ev['raw_hash']}", raw_md)
        self.assertIn("license:", raw_md)

    def test_license_extraction_variants(self) -> None:
        self.assertEqual(
            _record_license({"metadata": {"license": {"spdx_id": "Apache-2.0"}}}),
            "Apache-2.0",
        )
        self.assertEqual(
            _record_license({"metadata": {"license": ["CC-BY", "4.0"]}}), "CC-BY, 4.0",
        )
        self.assertEqual(_record_license({"metadata": {"oa_status": "gold"}}), "gold")
        self.assertEqual(_record_license({"metadata": {}}), "")

    def test_raw_hash_is_run_independent(self) -> None:
        # content-addressed: run_id / idx are NOT part of the hash
        rec = {
            "source": "s", "title": "t", "url": "u", "snippet": "x",
            "canonical_id": "c", "metadata": {"k": 1},
        }
        self.assertEqual(_record_raw_hash(rec), _record_raw_hash(dict(rec)))
        rec2 = dict(rec, snippet="different")
        self.assertNotEqual(_record_raw_hash(rec), _record_raw_hash(rec2))


if __name__ == "__main__":
    unittest.main()
