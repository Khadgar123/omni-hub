"""v0.41 — Eval flywheel tests.

Covers:
* EvalStore create/get/list-packs + JSONL append for cases (capability /
  regression / calibration).
* HR #11 (no-overwrite-v0.X): create_pack on existing version raises.
* HR #12 (holdout discipline): private holdout writes to separate file
  (not counted in eval_class_counts; not yet gitignored at this layer —
  that's a separate .gitignore concern).
* HR #13 (graduation through Proposal): propose_pack_upgrade emits a
  Proposal[T] with the right kind + payload, NOT a direct write.
* EvalRunner runs against a stub candidate and records into the
  ``.omni/eval_runs.sqlite3`` runs table.
* All 5 v0.41 seed packs load.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.evals import (
    EvalCase,
    EvalClass,
    EvalPack,
    EvalRunner,
    EvalStore,
    propose_pack_upgrade,
)
from omni_hub.evals.promote import scan_preference
from omni_hub.harness.preference import PreferenceRecord, PreferenceStore
from omni_hub.proposals import ProposalStore


class EvalStoreTests(unittest.TestCase):
    def test_create_pack_then_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EvalStore(tmp)
            pack = store.create_pack(
                domain="finance", version="v0.1",
                source="hand-curated test seed",
            )
            self.assertEqual(pack.pack_id, "finance/v0.1")
            packs = store.list_packs()
            self.assertEqual(len(packs), 1)
            self.assertEqual(packs[0].domain, "finance")

    def test_hr11_no_overwrite_v0X(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EvalStore(tmp)
            pack = store.create_pack(domain="research", version="v0.1")
            # Add a case so seed.jsonl is non-empty.
            store.add_case(pack, EvalCase(
                case_id="r1", domain="research",
                eval_class=EvalClass.CAPABILITY,
                question="q", expected="e",
            ))
            with self.assertRaises(ValueError) as ctx:
                store.create_pack(domain="research", version="v0.1")
            self.assertIn("HR #11", str(ctx.exception))

    def test_add_case_counts_by_class(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EvalStore(tmp)
            pack = store.create_pack(domain="meta", version="v0.1")
            for ec in (EvalClass.CAPABILITY, EvalClass.CAPABILITY,
                        EvalClass.REGRESSION, EvalClass.CALIBRATION):
                store.add_case(pack, EvalCase(
                    case_id=f"m_{ec.value}_{id(ec)}",
                    domain="meta", eval_class=ec,
                    question="q", expected="e",
                ))
            refreshed = store.get_pack("meta", "v0.1")
            self.assertEqual(refreshed.eval_class_counts.get("capability", 0), 2)
            self.assertEqual(refreshed.eval_class_counts.get("regression", 0), 1)
            self.assertEqual(refreshed.eval_class_counts.get("calibration", 0), 1)

    def test_hr12_holdout_writes_to_separate_file(self) -> None:
        import os as _os
        from omni_hub.evals.store import HOLDOUT_ENV_GATE

        with tempfile.TemporaryDirectory() as tmp:
            store = EvalStore(tmp)
            pack = store.create_pack(domain="research", version="v0.1")
            store.add_case(pack, EvalCase(
                case_id="public_1", domain="research",
                eval_class=EvalClass.CAPABILITY,
                question="q1", expected="e1",
            ))
            store.add_case(pack, EvalCase(
                case_id="hidden_1", domain="research",
                eval_class=EvalClass.CAPABILITY,
                question="q2", expected="e2",
            ), holdout=True)
            seed = store.list_cases(pack)
            self.assertEqual([c.case_id for c in seed], ["public_1"])
            # v0.42 HR #12 — opt-in via env-gate to read holdout.
            prior = _os.environ.pop(HOLDOUT_ENV_GATE, None)
            _os.environ[HOLDOUT_ENV_GATE] = "1"
            try:
                both = store.list_cases(pack, include_holdout=True)
            finally:
                if prior is None:
                    _os.environ.pop(HOLDOUT_ENV_GATE, None)
                else:
                    _os.environ[HOLDOUT_ENV_GATE] = prior
            self.assertEqual({c.case_id for c in both},
                              {"public_1", "hidden_1"})
            # Class counts only reflect public seed (private holdout is
            # invisible to the manifest by design).
            refreshed = store.get_pack("research", "v0.1")
            self.assertEqual(refreshed.eval_class_counts.get("capability", 0), 1)


class EvalRunnerTests(unittest.TestCase):
    def test_runner_runs_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EvalStore(tmp)
            pack = store.create_pack(domain="meta", version="v0.1")
            store.add_case(pack, EvalCase(
                case_id="echo_cap_1", domain="meta",
                eval_class=EvalClass.CAPABILITY,
                question="What is omni-hub's main repo dependency stance?",
                expected="stdlib-only; pin SDKs as agent-harness forks",
            ))
            runner = EvalRunner(workspace=tmp, judge="heuristic")
            run = runner.run(pack)
            self.assertEqual(run.pack_id, "meta/v0.1")
            self.assertEqual(len(run.per_case_results), 1)
            self.assertGreaterEqual(run.composite_score, 0.0)
            self.assertLessEqual(run.composite_score, 1.0)
            # Persisted in eval_runs.sqlite3 — list_runs should see it.
            rows = runner.list_runs(pack_id="meta/v0.1")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["run_id"], run.run_id)


class GraduationTests(unittest.TestCase):
    def test_below_floor_returns_below_floor_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Seed 5 records — far below the 100 floor.
            pref = PreferenceStore(Path(tmp) / ".omni" / "preference")
            for i in range(5):
                pref.append(PreferenceRecord(
                    task_id=f"t{i}", domain="research", prompt_version="v0",
                    candidate_text="x",
                    decision="accepted",
                    accepted_spans=[f"sample span {i}"],
                ))
            candidate = scan_preference(tmp, "research", accepted_floor=100)
            self.assertIsNotNone(candidate)
            self.assertEqual(candidate.accepted_count, 5)
            self.assertEqual(candidate.candidate_cases, [])
            self.assertIn("below graduation floor", candidate.note)

    def test_above_floor_emits_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pref = PreferenceStore(Path(tmp) / ".omni" / "preference")
            # 100+ accepted records, all with the same accepted span so
            # frequency tally is non-trivial.
            for i in range(120):
                pref.append(PreferenceRecord(
                    task_id=f"t{i}", domain="research", prompt_version="v0",
                    candidate_text="x",
                    decision="accepted",
                    accepted_spans=["citation density",
                                     f"specific phrase {i % 7}"],
                ))
            for i in range(10):
                pref.append(PreferenceRecord(
                    task_id=f"r{i}", domain="research", prompt_version="v0",
                    candidate_text="x",
                    decision="rejected",
                    rejected_spans=["uncited claim"],
                ))
            candidate = propose_pack_upgrade(tmp, "research", "v0.2")
            self.assertGreater(len(candidate.candidate_cases), 0)
            # Proposal landed in .omni/proposals.sqlite3.
            self.assertTrue(candidate.proposal_id)
            store = ProposalStore(tmp)
            matches = [
                p for p in store.list(kind="eval_pack_upgrade", limit=10)
                if p.proposal_id == candidate.proposal_id
            ]
            self.assertEqual(len(matches), 1)
            prop = matches[0]
            self.assertEqual(prop.kind, "eval_pack_upgrade")
            self.assertEqual(prop.payload["domain"], "research")
            self.assertEqual(prop.payload["new_version"], "v0.2")


class V041SeedSanityTests(unittest.TestCase):
    """The repo ships 5 v0.41 seed packs; verify each loads + has cases."""

    def test_five_seed_packs_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        store = EvalStore(repo_root)
        ids = {p.pack_id for p in store.list_packs()}
        for required in (
            "research/v0.1",
            "engineering/v0.1",
            "finance/v0.1",
            "meta/v0.1",
            "chat-relationships/v0.1",
        ):
            self.assertIn(required, ids, f"missing v0.41 seed pack: {required}")

    def test_meta_seed_pack_carries_capability_and_regression(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        store = EvalStore(repo_root)
        pack = store.get_pack("meta", "v0.1")
        self.assertIsNotNone(pack)
        cases = store.list_cases(pack)
        kinds = {c.eval_class for c in cases}
        self.assertIn(EvalClass.CAPABILITY, kinds)
        self.assertIn(EvalClass.REGRESSION, kinds)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
