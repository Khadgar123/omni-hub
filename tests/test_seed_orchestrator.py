"""P0.1 — seed_orchestrator bulk adapter consumes source-specific manifest
keys (cik_list / congress) and FAILS FAST on an unrecognised spec instead of
silently searching the domain name ("enterprise", "finance").

seed_orchestrator is a script, not a package module — load it by path.
"""

import importlib.util
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PATH = _ROOT / "scripts" / "seed_orchestrator.py"


def _load():
    spec = importlib.util.spec_from_file_location("seed_orchestrator", _PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _DummySource:
    name = "edgar"

    def retrieve(self, query, *, limit=5, domain=""):
        return []


class SeedBulkAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.m = _load()

    def _run(self, spec, *, source="edgar"):
        return self.m._bulk_source(
            _ROOT, "enterprise", "run1", spec,
            {source: _DummySource()}, {"idx": 0}, True,  # dry_run=True
        )

    def test_cik_list_iterates_per_company_not_domain(self) -> None:
        # 3 CIKs × limit 5 = 15 (a domain-name fallback would be 1 × 5 = 5)
        n = self._run({"source": "edgar", "cik_list": ["A", "B", "C"],
                       "forms": ["10-K"], "limit": 5})
        self.assertEqual(n, 15)

    def test_congress_generates_a_query(self) -> None:
        n = self.m._bulk_source(
            _ROOT, "us_policy", "run1",
            {"source": "congress_gov", "congress": 119, "limit": 3},
            {"congress_gov": _DummySource()}, {"idx": 0}, True,
        )
        self.assertEqual(n, 3)  # one "119th congress" query × 3

    def test_unrecognised_spec_fails_fast(self) -> None:
        # forms/days only, no query-generating key → must raise, not fall back
        with self.assertRaises(ValueError):
            self._run({"source": "edgar", "forms": ["10-K"], "days": 90, "limit": 5})


if __name__ == "__main__":
    unittest.main()
