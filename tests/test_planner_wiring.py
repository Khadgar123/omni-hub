"""v0.46: the retrieval planner is now reachable from the retrieve op.

Before this, ``planner.plan()`` had zero production callers (dead code).
The op exposes an opt-in ``plan`` flag; the model is operator-pinned via
``builtins.PLANNER_MODEL_CALL`` (same gate as the LLM grader).  With no
model it degrades to the full domain cascade — a safe no-op.

These tests run network-free by patching ``builtin_sources`` with fakes.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omni_hub import builtins as ohb
from omni_hub.models import OperationSpec, RiskLevel
from omni_hub.retrieval.cascade import DEFAULT_DOMAIN_CASCADES


class _FakeSource:
    def __init__(self, name: str) -> None:
        self.name = name

    def retrieve(self, query, *, limit=5, domain=""):
        return []


def _fake_sources():
    names = set(DEFAULT_DOMAIN_CASCADES["default"]) | {"wikipedia"}
    return {n: _FakeSource(n) for n in names}


class PlannerWiringTests(unittest.TestCase):
    def _run(self, payload, model_call):
        with tempfile.TemporaryDirectory() as d:
            op = ohb.make_retrieve_cascade(Path(d))
            spec = OperationSpec(
                name="retrieve_cascade", action="retrieve",
                payload=payload, risk_level=RiskLevel.READ_ONLY,
            )
            with patch("omni_hub.retrieval.builtin_sources", _fake_sources):
                with patch.object(ohb, "PLANNER_MODEL_CALL", model_call):
                    return op(spec)

    def test_plan_flag_without_model_is_full_cascade_noop(self) -> None:
        out = self._run(
            {"query": "agent memory", "domain": "default", "plan": True}, None,
        )
        self.assertIn("plan", out)
        self.assertEqual(out["plan"]["rewritten_query"], "agent memory")
        self.assertEqual(
            set(out["plan"]["sources"]), set(DEFAULT_DOMAIN_CASCADES["default"]),
        )

    def test_plan_flag_with_model_narrows_sources(self) -> None:
        def fake_model(prompt: str) -> str:
            return '{"rewritten_query": "rewritten Q", "sources": ["wikipedia"]}'

        out = self._run({"query": "q", "domain": "default", "plan": True}, fake_model)
        self.assertEqual(out["plan"]["sources"], ["wikipedia"])
        self.assertEqual(out["plan"]["rewritten_query"], "rewritten Q")

    def test_no_plan_flag_emits_no_plan_meta(self) -> None:
        out = self._run({"query": "q", "domain": "default"}, None)
        self.assertNotIn("plan", out)


if __name__ == "__main__":
    unittest.main()
