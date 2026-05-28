"""v0.18 P1 tests: WorkflowKernel / Signal-Query / Projection registry +
snapshots / Graph / DSPy 5-component skill / ResearchFlow typed records."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub import knowledge_plane
from omni_hub.models import (
    ExperimentRecord,
    IdeaProposal,
    ResearchClaimCandidate,
    ResearchEvidencePack,
    SourceRef,
)
from omni_hub.harness.dspy_compile import (
    CompiledSkill,
    SkillMetric,
    SkillModule,
    SkillOptimizer,
    SkillSignature,
    compile_skill_md,
)
from omni_hub.harness.preference import PreferenceRecord, PreferenceStore
from omni_hub.projection import (
    GraphProjectionBuilder,
    ProjectionRegistry,
    build_default_projection_registry,
)
from omni_hub.wiki_graph import (
    query_community,
    query_neighbours,
    rebuild_graph,
)
from omni_hub.workflow import (
    StepState,
    WorkflowState,
    WorkflowStore,
)


# ---------------------------------------------------------------------------
# v0.18-F/G WorkflowKernel + Signal/Query
# ---------------------------------------------------------------------------


class WorkflowKernelTests(unittest.TestCase):
    def test_create_workflow_seeds_steps_in_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(tmp)
            run = store.create_workflow(
                template_name="research_batch",
                trace_id="trace-wf-1",
                first_step_inputs={"query": "x"},
                step_op_names=["retrieve", "wiki_ingest", "propose_review"],
            )
            self.assertEqual(run.state, WorkflowState.PENDING)
            self.assertEqual(run.cursor, 0)
            steps = store.list_steps(run.workflow_run_id)
            self.assertEqual([s.op_name for s in steps],
                              ["retrieve", "wiki_ingest", "propose_review"])
            self.assertEqual(steps[0].inputs, {"query": "x"})

    def test_advance_cursor_through_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(tmp)
            run = store.create_workflow(
                template_name="x", trace_id="t",
                first_step_inputs={}, step_op_names=["a", "b"],
            )
            r1 = store.advance_cursor(run.workflow_run_id, to_state=WorkflowState.RUNNING)
            self.assertEqual(r1.cursor, 1)
            self.assertEqual(r1.state, WorkflowState.RUNNING)
            store.suspend(run.workflow_run_id, reason="awaiting approval")
            r2 = store.get_workflow(run.workflow_run_id)
            self.assertEqual(r2.state, WorkflowState.SUSPENDED)
            self.assertEqual(r2.last_error, "awaiting approval")
            r3 = store.resume(run.workflow_run_id)
            self.assertEqual(r3.state, WorkflowState.RUNNING)

    def test_update_step_records_artifact_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(tmp)
            run = store.create_workflow(
                template_name="x", trace_id="t",
                first_step_inputs={}, step_op_names=["a"],
            )
            step = store.list_steps(run.workflow_run_id)[0]
            store.update_step(step.step_id, state=StepState.RUNNING, increment_attempts=True)
            s1 = store.get_step(step.step_id)
            self.assertEqual(s1.state, StepState.RUNNING)
            self.assertEqual(s1.attempts, 1)
            self.assertIsNotNone(s1.started_at)
            store.update_step(step.step_id, state=StepState.DONE,
                              artifact={"foo": "bar"})
            s2 = store.get_step(step.step_id)
            self.assertEqual(s2.state, StepState.DONE)
            self.assertEqual(s2.artifact, {"foo": "bar"})
            self.assertIsNotNone(s2.ended_at)


class SignalQueryTests(unittest.TestCase):
    def test_signal_is_consumed_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(tmp)
            run = store.create_workflow(
                template_name="x", trace_id="t",
                first_step_inputs={}, step_op_names=["a"],
            )
            sig1 = store.send_signal(run.workflow_run_id, "evidence_added",
                                       {"url": "https://example.com/1"})
            sig2 = store.send_signal(run.workflow_run_id, "evidence_added",
                                       {"url": "https://example.com/2"})
            consumed = store.consume_signals(run.workflow_run_id)
            self.assertEqual(len(consumed), 2)
            self.assertEqual(consumed[0]["payload"]["url"], "https://example.com/1")
            # Second consume returns empty (atomic claim).
            self.assertEqual(store.consume_signals(run.workflow_run_id), [])

    def test_query_returns_state_snapshot_without_advancing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(tmp)
            run = store.create_workflow(
                template_name="x", trace_id="trace-q",
                first_step_inputs={}, step_op_names=["a", "b", "c"],
            )
            response = store.serve_query(run.workflow_run_id, "state")
            self.assertEqual(response["state"], "pending")
            self.assertEqual(response["cursor"], 0)
            self.assertEqual(response["total_steps"], 3)
            self.assertEqual(response["trace_id"], "trace-q")
            # Cursor did NOT advance via query.
            self.assertEqual(store.get_workflow(run.workflow_run_id).cursor, 0)


# ---------------------------------------------------------------------------
# v0.18-H/I Projection registry + snapshots + outbox cursors
# ---------------------------------------------------------------------------


class ProjectionRegistryTests(unittest.TestCase):
    def test_default_registry_seeds_four_builders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = build_default_projection_registry(tmp)
            names = {b.name for b in registry.builders()}
            self.assertEqual(names, {
                "wiki_fts5", "claims_ledger", "preference_jsonl", "wiki_graph",
            })

    def test_overview_returns_each_projection_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            registry = build_default_projection_registry(root)
            overview = registry.overview()
            self.assertGreaterEqual(overview["count"], 4)
            for entry in overview["projections"]:
                self.assertIn("schema_version", entry)
                self.assertIn("cursor", entry)


class CursorTests(unittest.TestCase):
    def test_advance_and_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectionRegistry(tmp)
            cursor = registry.get_cursor("test_projection")
            self.assertEqual(cursor.last_event_seq, 0)
            registry.advance_cursor("test_projection", 42)
            self.assertEqual(registry.get_cursor("test_projection").last_event_seq, 42)
            registry.advance_cursor("test_projection", 100)
            self.assertEqual(registry.get_cursor("test_projection").last_event_seq, 100)

    def test_list_cursors_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectionRegistry(tmp)
            registry.advance_cursor("b", 2)
            registry.advance_cursor("a", 1)
            cursors = registry.list_cursors()
            self.assertEqual([c.projection_name for c in cursors], ["a", "b"])


class SnapshotTests(unittest.TestCase):
    def test_record_then_current_then_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_plane.init_layout(root)
            registry = build_default_projection_registry(root)
            # Rebuild claims_ledger projection (trivial pseudo-projection)
            snap1 = registry.rebuild("claims_ledger")
            self.assertEqual(snap1.projection_name, "claims_ledger")
            self.assertIsNotNone(registry.current_snapshot("claims_ledger"))
            # Build a 2nd snapshot
            snap2 = registry.rebuild("claims_ledger")
            self.assertNotEqual(snap1.snapshot_id, snap2.snapshot_id)
            # Rollback to snap1
            rolled = registry.rollback("claims_ledger", snap1.snapshot_id)
            self.assertEqual(rolled.snapshot_id, snap1.snapshot_id)
            current = registry.current_snapshot("claims_ledger")
            self.assertEqual(current.snapshot_id, snap1.snapshot_id)


# ---------------------------------------------------------------------------
# v0.18-J Graph projection (entities + communities)
# ---------------------------------------------------------------------------


def _seed_graph_claims(root: Path) -> None:
    """Add several claims that share canonical_ids → connected component."""

    knowledge_plane.init_layout(root)
    ledger = root / ".omni" / "claims.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    records = [
        {
            "claim_id": "c1", "domain": "ai_progress",
            "statement": "ACE evolves context across sessions.",
            "support": [{"source_id": "arxiv:2510.04618"}],
            "review_state": "approved",
            "t_valid_from": now, "t_valid_to": None,
        },
        {
            "claim_id": "c2", "domain": "ai_progress",
            "statement": "GEPA optimises prompts reflectively.",
            "support": [{"source_id": "arxiv:2510.04618"},
                         {"source_id": "arxiv:2507.19457"}],
            "review_state": "approved",
            "t_valid_from": now, "t_valid_to": None,
        },
        {
            "claim_id": "c3", "domain": "ai_progress",
            "statement": "Dreaming consolidates memory offline.",
            "support": [{"source_id": "arxiv:2507.19457"}],
            "review_state": "approved",
            "t_valid_from": now, "t_valid_to": None,
        },
    ]
    with ledger.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


class GraphProjectionTests(unittest.TestCase):
    def test_rebuild_creates_nodes_and_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph_claims(root)
            snap = rebuild_graph(root)
            self.assertGreater(len(snap.nodes), 0)
            self.assertGreater(len(snap.edges), 0)
            node_ids = {n.node_id for n in snap.nodes}
            self.assertIn("arxiv:2510.04618", node_ids)
            self.assertIn("arxiv:2507.19457", node_ids)

    def test_community_detection_groups_co_cited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph_claims(root)
            snap = rebuild_graph(root)
            # Both arxiv ids are co-cited in c2 → same community
            community = None
            for c in snap.communities:
                if "arxiv:2510.04618" in c.node_ids:
                    community = c
                    break
            self.assertIsNotNone(community, f"no community contains 2510.04618; got {snap.communities}")
            self.assertIn("arxiv:2507.19457", community.node_ids)
            self.assertTrue(community.summary)

    def test_query_neighbours_returns_co_cited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph_claims(root)
            rebuild_graph(root)
            result = query_neighbours(root, node_id="arxiv:2510.04618")
            ids = {n["node_id"] for n in result["neighbours"]}
            self.assertIn("arxiv:2507.19457", ids)

    def test_query_community_returns_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph_claims(root)
            rebuild_graph(root)
            result = query_community(root, node_id="arxiv:2510.04618")
            self.assertIsNotNone(result["community"])
            self.assertTrue(result["community"]["summary"])


# ---------------------------------------------------------------------------
# v0.18-K Skill projection 5-component compile artifact
# ---------------------------------------------------------------------------


class SkillCompiledArtifactTests(unittest.TestCase):
    def _seed_preference(self, root: Path) -> PreferenceStore:
        store = PreferenceStore(root / ".omni" / "preference")
        store.append(PreferenceRecord(
            task_id="t", domain="research", prompt_version="v0",
            candidate_text="# good answer with [1] citation\n\nbody",
            decision="accepted",
            accepted_spans=["good answer"],
            reason="cited",
        ))
        store.append(PreferenceRecord(
            task_id="t2", domain="research", prompt_version="v0",
            candidate_text="bad uncited claim",
            decision="rejected",
            rejected_spans=["uncited"],
            reason="no citation",
        ))
        return store

    def test_compile_emits_5_component_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self._seed_preference(root)
            report = compile_skill_md(
                domain="research",
                output_root=root / ".agents" / "skills",
                preference_store=store,
            )
            self.assertTrue(report.compiled_skill)
            cs = report.compiled_skill
            self.assertIn("signature", cs)
            self.assertIn("module", cs)
            self.assertIn("metric", cs)
            self.assertIn("optimizer", cs)
            self.assertEqual(cs["signature"]["skill_id"], "research-wiki")
            self.assertEqual(cs["module"]["module_type"], "PromptedFewShot")
            self.assertGreater(cs["optimizer"]["trace_count"], 0)


# ---------------------------------------------------------------------------
# v0.18-L ResearchFlow typed records
# ---------------------------------------------------------------------------


class ResearchFlowTypedRecordsTests(unittest.TestCase):
    def test_evidence_pack_serialisable(self) -> None:
        pack = ResearchEvidencePack(
            pack_id="pack-001",
            question="What is ACE?",
            domain="research",
            sources=[SourceRef(
                source_id="openalex",
                canonical_id="doi:10.x/ace",
                url="https://example.org",
                cite_id="R1",
                retrieval_run_id="r-001",
            )],
            evidence_paths=["vault/evidence/research/x.json"],
            raw_paths=["vault/raw/research/r-001/y.md"],
        )
        d = pack.to_dict()
        self.assertEqual(d["pack_id"], "pack-001")
        self.assertEqual(d["sources"][0]["cite_id"], "R1")

    def test_claim_candidate_carries_research_metadata(self) -> None:
        cand = ResearchClaimCandidate(
            candidate_id="cand-001",
            statement="ACE evolves context as a wiki.",
            methodology_tags=["context-engineering"],
            paper_link="https://arxiv.org/abs/2510.04618",
            venue_year="ICLR_2026",
            confidence=0.7,
        )
        d = cand.to_dict()
        self.assertEqual(d["venue_year"], "ICLR_2026")
        self.assertIn("context-engineering", d["methodology_tags"])

    def test_idea_proposal_with_stress_test_payload(self) -> None:
        idea = IdeaProposal(
            idea_id="idea-001",
            title="Bitemporal context",
            motivation="Mem0 ADD-only loses history",
            method_sketch="Add t_valid_from/to to context entries",
            expected_outcome="Higher LongMemEval temporal score",
            derived_from_claims=["c1", "c2"],
            reviewer_stress_test={"weakness": "compute cost", "score": 0.7},
        )
        self.assertEqual(idea.reviewer_stress_test["score"], 0.7)

    def test_experiment_record_judgment_enum(self) -> None:
        exp = ExperimentRecord(
            experiment_id="exp-001",
            idea_id="idea-001",
            hypothesis="bitemporal beats ADD-only",
            setup="run LongMemEval on both",
            actual_outcome="bitemporal +3.2 pts",
            judgment="supports",
            artifacts=["vault/evidence/research/exp-001.json"],
        )
        self.assertEqual(exp.judgment, "supports")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
