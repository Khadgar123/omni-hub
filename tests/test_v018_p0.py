"""v0.18 P0 tests: Command.preview / CommandRegistry / trace_id / PolicyDecision / expected_version."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub import knowledge_plane
from omni_hub.builtins import build_default_registry
from omni_hub.command_registry import (
    WikiApplyProposalPayload,
    WikiSupersedePayload,
    build_default_command_registry,
    derive_json_schema,
)
from omni_hub.models import (
    ConcurrentModificationError,
    OperationSpec,
    OperationStatus,
    ProjectionDiff,
    RiskLevel,
)
from omni_hub.policy import PolicyEngine
from omni_hub.proposals import PENDING, Proposal, ProposalStore
from omni_hub.runner import OperationRunner


# ---------------------------------------------------------------------------
# v0.18-A: ProjectionDiff + preview path
# ---------------------------------------------------------------------------


def _seed_wiki_proposal(root: Path) -> str:
    """Create a pending wiki_update Proposal we can preview / apply."""

    knowledge_plane.init_layout(root)
    proposal = Proposal(
        kind="wiki_update", state=PENDING,
        title="Preview test", summary="testing v0.18 preview path",
        source_path=".omni/retrieval/test-run",
        payload={
            "target_path": "vault/wiki/syntheses/preview-test.md",
            "domain": "engineering",
            "body": "# Preview test\n\nBody content for v0.18-A preview path.\n",
            "claims": [
                {"claim_id": "c_prev_1",
                 "domain": "engineering",
                 "statement": "Preview path emits ProjectionDiff",
                 "support": [], "against": [], "confidence": 0.6,
                 "review_state": "proposed",
                 "t_valid_from": datetime.now(UTC).isoformat(),
                 "t_valid_to": None, "supersedes": [], "superseded_by": None},
            ],
        },
    )
    store = ProposalStore(root)
    store.store(proposal, write_card=False)
    store.approve(proposal.proposal_id, reason="v0.18 preview test")
    return proposal.proposal_id


class CommandPreviewTests(unittest.TestCase):
    def test_apply_proposal_preview_returns_projection_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = _seed_wiki_proposal(root)

            diff = knowledge_plane.preview_apply_wiki_proposal(
                root, pid, trace_id="trace-test-1",
            )
            self.assertIsInstance(diff, ProjectionDiff)
            self.assertEqual(diff.command_name, "wiki_apply_proposal")
            self.assertEqual(diff.trace_id, "trace-test-1")
            self.assertGreater(len(diff.changes), 0)
            self.assertIn("wiki", diff.counts_by_projection)
            self.assertIn("claims", diff.counts_by_projection)
            self.assertIn("preference", diff.counts_by_projection)

    def test_preview_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = _seed_wiki_proposal(root)
            before_files = set((root / "vault").rglob("*.md"))
            knowledge_plane.preview_apply_wiki_proposal(root, pid)
            after_files = set((root / "vault").rglob("*.md"))
            self.assertEqual(before_files, after_files,
                              "preview must NOT create or modify wiki files")
            # claims.jsonl must not exist
            self.assertFalse((root / ".omni" / "claims.jsonl").exists())

    def test_supersede_preview_returns_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            ledger = root / ".omni" / "claims.jsonl"
            ledger.parent.mkdir(parents=True, exist_ok=True)
            now = datetime.now(UTC).isoformat()
            with ledger.open("a", encoding="utf-8") as fh:
                for cid in ("c_old", "c_new"):
                    fh.write(json.dumps({
                        "claim_id": cid, "domain": "research",
                        "statement": f"{cid} body", "review_state": "approved",
                        "target_path": f"vault/wiki/concepts/{cid}.md",
                        "t_valid_from": now, "t_valid_to": None,
                        "supersedes": [], "superseded_by": None,
                    }) + "\n")

            diff = knowledge_plane.preview_supersede_claim(
                root, new_claim_id="c_new", old_claim_id="c_old",
                trace_id="trace-sup-1",
            )
            self.assertEqual(diff.command_name, "wiki_supersede")
            self.assertIn("claims", diff.counts_by_projection)
            # Old claim unchanged on disk after preview
            disk = ledger.read_text(encoding="utf-8")
            self.assertIn('"t_valid_to": null', disk)


# ---------------------------------------------------------------------------
# v0.18-B: CommandRegistry + JSON-schema derivation
# ---------------------------------------------------------------------------


class CommandRegistryTests(unittest.TestCase):
    def test_default_registry_seeds_six_commands(self) -> None:
        registry = build_default_command_registry()
        names = {d.name for d in registry.list()}
        self.assertEqual(names, {
            "wiki_ingest", "wiki_apply_proposal", "wiki_supersede",
            "wiki_conflict_resolve", "claims_show", "harness_compile_skill",
        })

    def test_describe_emits_json_schema(self) -> None:
        registry = build_default_command_registry()
        bundle = registry.describe()
        self.assertEqual(bundle["schema_version"], "v0.18")
        wiki_supersede = next(c for c in bundle["commands"] if c["name"] == "wiki_supersede")
        schema = wiki_supersede["json_schema"]
        self.assertEqual(schema["type"], "object")
        self.assertIn("new_claim_id", schema["properties"])
        self.assertIn("old_claim_id", schema["properties"])
        self.assertEqual(set(schema["required"]),
                          {"new_claim_id", "old_claim_id"})

    def test_validate_payload_coerces_dataclass(self) -> None:
        registry = build_default_command_registry()
        validated = registry.validate_payload("wiki_apply_proposal", {"proposal": "p-1"})
        self.assertEqual(validated, {"proposal": "p-1"})

    def test_validate_payload_rejects_missing_required(self) -> None:
        registry = build_default_command_registry()
        with self.assertRaises(ValueError):
            registry.validate_payload("wiki_supersede", {"new_claim_id": "x"})

    def test_derive_json_schema_handles_optional(self) -> None:
        schema = derive_json_schema(WikiSupersedePayload)
        # expected_version is Optional[int] → nullable
        ev = schema["properties"]["expected_version"]
        self.assertTrue(ev.get("nullable"))
        self.assertEqual(ev["type"], "integer")


# ---------------------------------------------------------------------------
# v0.18-C: trace_id propagation
# ---------------------------------------------------------------------------


class TraceIdPropagationTests(unittest.TestCase):
    def test_runner_injects_trace_id_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = OperationRunner(build_default_registry(root))
            spec = OperationSpec(
                name="memory_stats", action="stats",
                payload={}, risk_level=RiskLevel.READ_ONLY,
            )
            self.assertEqual(spec.trace_id, "")
            result = runner.run(spec)
            self.assertNotEqual(spec.trace_id, "")
            self.assertEqual(result.trace_id, spec.trace_id)

    def test_runner_preserves_provided_trace_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = OperationRunner(build_default_registry(root))
            spec = OperationSpec(
                name="memory_stats", action="stats",
                payload={}, risk_level=RiskLevel.READ_ONLY,
                trace_id="caller-trace-XYZ",
            )
            result = runner.run(spec)
            self.assertEqual(result.trace_id, "caller-trace-XYZ")

    def test_wiki_apply_propagates_trace_id_into_claim_and_preference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = _seed_wiki_proposal(root)
            applied = knowledge_plane.apply_wiki_proposal(
                root, pid, trace_id="propagation-trace-Z",
            )
            self.assertEqual(applied["trace_id"], "propagation-trace-Z")
            self.assertGreater(applied["new_ledger_version"], 0)


# ---------------------------------------------------------------------------
# v0.18-D: structured PolicyDecision
# ---------------------------------------------------------------------------


class PolicyDecisionStructuredTests(unittest.TestCase):
    def test_local_write_allow(self) -> None:
        engine = PolicyEngine()
        spec = OperationSpec(name="x", action="y", risk_level=RiskLevel.LOCAL_WRITE)
        decision = engine.evaluate(spec)
        self.assertEqual(decision.decision, "allow")
        self.assertEqual(decision.violations, [])

    def test_external_publish_require_approval(self) -> None:
        engine = PolicyEngine()
        spec = OperationSpec(name="x", action="y", risk_level=RiskLevel.EXTERNAL_PUBLISH)
        decision = engine.evaluate(spec)
        self.assertEqual(decision.decision, "require_approval")
        self.assertTrue(any("EXTERNAL_PUBLISH" in v or "L3" in v for v in decision.violations))

    def test_sandbox_execution_require_sandbox(self) -> None:
        engine = PolicyEngine()
        spec = OperationSpec(name="x", action="y", risk_level=RiskLevel.SANDBOX_EXECUTION)
        decision = engine.evaluate(spec)
        self.assertEqual(decision.decision, "require_sandbox")
        self.assertTrue(decision.requires_sandbox)

    def test_dry_run_skips_approval_gate(self) -> None:
        engine = PolicyEngine()
        spec = OperationSpec(
            name="x", action="y",
            risk_level=RiskLevel.EXTERNAL_PUBLISH,
            dry_run=True,
        )
        decision = engine.evaluate(spec)
        # preview always allowed (READ_ONLY in effect)
        self.assertEqual(decision.decision, "allow")
        self.assertFalse(decision.requires_approval)


# ---------------------------------------------------------------------------
# v0.18-E: expected_version optimistic concurrency
# ---------------------------------------------------------------------------


class OptimisticConcurrencyTests(unittest.TestCase):
    def _seed_two_claims(self, root: Path) -> None:
        knowledge_plane.init_layout(root)
        ledger = root / ".omni" / "claims.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC).isoformat()
        for cid in ("c_old", "c_new"):
            with ledger.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "claim_id": cid, "domain": "research",
                    "statement": f"{cid} statement", "review_state": "approved",
                    "support": [], "against": [], "confidence": 0.7,
                    "t_valid_from": now, "t_valid_to": None,
                    "supersedes": [], "superseded_by": None,
                }) + "\n")

    def test_ledger_version_reports_line_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(knowledge_plane.claim_ledger_version(root), 0)
            self._seed_two_claims(root)
            self.assertEqual(knowledge_plane.claim_ledger_version(root), 2)

    def test_supersede_accepts_matching_expected_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_two_claims(root)
            knowledge_plane.supersede_claim(
                root, new_claim_id="c_new", old_claim_id="c_old",
                expected_version=2,
            )

    def test_supersede_rejects_stale_expected_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_two_claims(root)
            with self.assertRaises(ConcurrentModificationError):
                knowledge_plane.supersede_claim(
                    root, new_claim_id="c_new", old_claim_id="c_old",
                    expected_version=99,
                )

    def test_supersede_no_check_when_version_none(self) -> None:
        """Backward compat — legacy callers pass no expected_version."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_two_claims(root)
            result = knowledge_plane.supersede_claim(
                root, new_claim_id="c_new", old_claim_id="c_old",
            )
            self.assertIn("new_ledger_version", result)


# ---------------------------------------------------------------------------
# Integration: preview via CLI runner end-to-end
# ---------------------------------------------------------------------------


class CliPreviewIntegrationTests(unittest.TestCase):
    def test_wiki_apply_preview_via_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = _seed_wiki_proposal(root)
            runner = OperationRunner(build_default_registry(root))
            spec = OperationSpec(
                name="wiki_apply_proposal", action="apply",
                payload={"proposal": pid},
                risk_level=RiskLevel.LOCAL_WRITE,
                dry_run=True,
            )
            result = runner.run(spec)
            self.assertEqual(result.status, OperationStatus.SUCCEEDED)
            self.assertEqual(result.output["command_name"], "wiki_apply_proposal")
            self.assertGreater(result.output["total_changes"], 0)
            self.assertNotEqual(result.trace_id, "")
            # No actual write happened
            self.assertFalse((root / "vault/wiki/syntheses/preview-test.md").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
