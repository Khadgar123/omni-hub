"""v0.42 — additional eval-flywheel tests.

Covers the three v0.42 review-driven changes:

1. **sha256 stable case_id** — promote.scan_preference must produce the
   same case_id for the same span across processes (no Python ``hash()``
   randomness).
2. **SkillAdapter routing** — EvalRunner picks the right adapter per
   case via :func:`pick_adapter`; falls back to echo when no adapter
   matches; records ``adapter_used`` per result.
3. **Holdout access barrier** — ``OMNI_EVAL_HOLDOUT`` env-gate must be
   set before EvalStore.list_cases yields holdout records.

Also verifies that all 30 v0.42 seed packs (19 domain + 11 functional)
load and run end-to-end against ``--echo-only`` (the smoke floor).
"""

from __future__ import annotations

import os
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
from omni_hub.evals.run import (
    builtin_skill_adapters,
    pick_adapter,
)
from omni_hub.evals.store import (
    HOLDOUT_ENV_GATE,
    HoldoutAccessDenied,
)
from omni_hub.harness.preference import PreferenceRecord, PreferenceStore


# ---------------------------------------------------------------------------
# 1. sha256 stable case_id
# ---------------------------------------------------------------------------


class StableCaseIdTests(unittest.TestCase):
    def test_same_span_produces_same_case_id_across_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            pref = PreferenceStore(Path(workspace) / ".omni" / "preference")
            # Push above the 100-accepted floor so scan_preference emits
            # real candidate cases (otherwise it returns the below-floor
            # sentinel with empty candidate_cases — case_id stability
            # would be vacuously true).
            for i in range(120):
                pref.append(PreferenceRecord(
                    task_id=f"t{i}", domain="research", prompt_version="v0",
                    candidate_text="x",
                    decision="accepted",
                    accepted_spans=["citation density",
                                     "ACE wiki-style context evolution"],
                ))
            candidate_a = scan_preference(workspace, "research")
            candidate_b = scan_preference(workspace, "research")
            ids_a = [c["case_id"] for c in candidate_a.candidate_cases]
            ids_b = [c["case_id"] for c in candidate_b.candidate_cases]
            self.assertEqual(
                ids_a, ids_b,
                "case_id must be stable across calls (sha256, not hash()).",
            )
            self.assertTrue(all(cid for cid in ids_a))
            # And the ids must NOT contain a negative sign or look like a
            # Python ``hash()`` output (regression on the v0.42 P1 review).
            for cid in ids_a:
                self.assertNotIn("-", cid,
                    f"case_id {cid!r} looks like Python hash() output")


# ---------------------------------------------------------------------------
# 2. SkillAdapter routing
# ---------------------------------------------------------------------------


class SkillAdapterRoutingTests(unittest.TestCase):
    def test_pick_adapter_resolves_domain_wiki(self) -> None:
        adapters = {
            "research-wiki": lambda c: "from research-wiki adapter",
            "chat-route":    lambda c: "from chat-route adapter",
        }
        case = EvalCase(
            case_id="t1", domain="research",
            eval_class=EvalClass.CAPABILITY,
            question="anything",
        )
        adapter = pick_adapter(case, adapters=adapters)
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter(case), "from research-wiki adapter")

    def test_pick_adapter_resolves_functional(self) -> None:
        adapters = {
            "chat-route": lambda c: "from chat-route adapter",
        }
        case = EvalCase(
            case_id="t2", domain="functional:chat-route",
            eval_class=EvalClass.CAPABILITY,
            question="route this",
        )
        adapter = pick_adapter(case, adapters=adapters)
        self.assertEqual(adapter(case), "from chat-route adapter")

    def test_pick_adapter_explicit_skill_id_override(self) -> None:
        adapters = {
            "research-wiki": lambda c: "wrong",
            "chat-route":    lambda c: "right",
        }
        case = EvalCase(
            case_id="t3", domain="research",
            eval_class=EvalClass.CAPABILITY,
            question="ambiguous",
            metadata={"skill_id": "chat-route"},
        )
        adapter = pick_adapter(case, adapters=adapters)
        self.assertEqual(adapter(case), "right")

    def test_pick_adapter_missing_returns_none(self) -> None:
        case = EvalCase(
            case_id="t4", domain="not-a-real-domain",
            eval_class=EvalClass.CAPABILITY,
            question="?",
        )
        adapter = pick_adapter(case, adapters={})
        self.assertIsNone(adapter)

    def test_runner_records_adapter_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            store = EvalStore(workspace)
            pack = store.create_pack(domain="research", version="v0.1")
            store.add_case(pack, EvalCase(
                case_id="adp_001", domain="research",
                eval_class=EvalClass.CAPABILITY,
                question="X", expected="Y",
            ))
            runner = EvalRunner(workspace=workspace)
            # Inject one adapter so we can check the label round-trips.
            adapters = {"research-wiki": lambda c: c.expected}
            run = runner.run(pack, adapters=adapters)
            self.assertEqual(len(run.per_case_results), 1)
            self.assertEqual(
                run.per_case_results[0].adapter_used, "research-wiki",
            )

    def test_runner_echo_only_bypasses_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            store = EvalStore(workspace)
            pack = store.create_pack(domain="research", version="v0.1")
            store.add_case(pack, EvalCase(
                case_id="echo_001", domain="research",
                eval_class=EvalClass.CAPABILITY,
                question="X", expected="Y",
            ))
            runner = EvalRunner(workspace=workspace)
            # Even if an adapter is registered, --echo-only must skip it.
            adapters = {"research-wiki": lambda c: (_ for _ in ()).throw(
                AssertionError("adapter should not be called"))}
            run = runner.run(pack, adapters=adapters, use_echo_only=True)
            self.assertEqual(run.per_case_results[0].adapter_used, "echo")

    def test_builtin_skill_adapters_cover_all_19_domains_plus_functional(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapters = builtin_skill_adapters(tmp)
            # 19 wiki adapters + the named functionals registered above.
            from omni_hub.domain_schemas import DOMAIN_SCHEMAS
            wiki_ids = {
                f"{d.replace('_', '-')}-wiki" for d in DOMAIN_SCHEMAS
            }
            self.assertTrue(wiki_ids.issubset(set(adapters.keys())),
                "missing wiki adapters: " + str(wiki_ids - set(adapters.keys())))
            for needed in (
                "chat-route", "retrieve", "context-pack",
                "app-report-build", "inbox-route", "meta-cross-skill-scan",
                "finance-screen",
                "project-plan", "pptx-build", "calendar-add",
                "schedule-plan", "task-add", "order-propose",
            ):
                self.assertIn(needed, adapters,
                    f"functional adapter {needed!r} missing from registry")


# ---------------------------------------------------------------------------
# 3. Holdout barrier (HR #12)
# ---------------------------------------------------------------------------


class HoldoutBarrierTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prior = os.environ.pop(HOLDOUT_ENV_GATE, None)

    def tearDown(self) -> None:
        if self._prior is None:
            os.environ.pop(HOLDOUT_ENV_GATE, None)
        else:
            os.environ[HOLDOUT_ENV_GATE] = self._prior

    def test_include_holdout_raises_without_env_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            store = EvalStore(workspace)
            pack = store.create_pack(domain="research", version="v0.1")
            store.add_case(pack, EvalCase(
                case_id="seed_001", domain="research",
                eval_class=EvalClass.CAPABILITY,
                question="X", expected="Y",
            ), holdout=False)
            store.add_case(pack, EvalCase(
                case_id="hold_001", domain="research",
                eval_class=EvalClass.CAPABILITY,
                question="HIDDEN", expected="HIDDEN",
            ), holdout=True)

            # Without the gate set: list_cases(include_holdout=True) raises.
            self.assertNotIn(HOLDOUT_ENV_GATE, os.environ)
            with self.assertRaises(HoldoutAccessDenied):
                store.list_cases(pack, include_holdout=True)

            # Seed-only still works.
            seed_only = store.list_cases(pack, include_holdout=False)
            self.assertEqual(len(seed_only), 1)
            self.assertEqual(seed_only[0].case_id, "seed_001")

    def test_include_holdout_allowed_with_env_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            store = EvalStore(workspace)
            pack = store.create_pack(domain="research", version="v0.1")
            store.add_case(pack, EvalCase(
                case_id="seed_001", domain="research",
                eval_class=EvalClass.CAPABILITY,
                question="X", expected="Y",
            ), holdout=False)
            store.add_case(pack, EvalCase(
                case_id="hold_001", domain="research",
                eval_class=EvalClass.CAPABILITY,
                question="HIDDEN", expected="HIDDEN",
            ), holdout=True)

            os.environ[HOLDOUT_ENV_GATE] = "1"
            try:
                both = store.list_cases(pack, include_holdout=True)
                ids = {c.case_id for c in both}
                self.assertEqual(ids, {"seed_001", "hold_001"})
            finally:
                os.environ.pop(HOLDOUT_ENV_GATE, None)


# ---------------------------------------------------------------------------
# 4. Full 30-pack smoke
# ---------------------------------------------------------------------------


class SeedPackCoverageTests(unittest.TestCase):
    def test_v042_full_coverage(self) -> None:
        store = EvalStore()
        packs = store.list_packs()
        pack_ids = {p.pack_id for p in packs}
        # 19 domain packs (any version v0.1 prefix)
        domain_ids = {pid for pid in pack_ids
                       if pid.endswith("/v0.1")
                       and not pid.startswith("functional:")}
        functional_ids = {pid for pid in pack_ids
                           if pid.startswith("functional:")
                           and pid.endswith("/v0.1")}
        self.assertGreaterEqual(
            len(domain_ids), 19,
            f"expected ≥19 domain packs at v0.1, got {len(domain_ids)}: "
            f"{sorted(domain_ids)}",
        )
        self.assertGreaterEqual(
            len(functional_ids), 11,
            f"expected ≥11 functional packs at v0.1, got "
            f"{len(functional_ids)}: {sorted(functional_ids)}",
        )

    def test_every_pack_has_at_least_one_case(self) -> None:
        store = EvalStore()
        for pack in store.list_packs():
            if not pack.pack_id.endswith("/v0.1"):
                continue
            cases = store.list_cases(pack)
            self.assertGreater(
                len(cases), 0,
                f"empty seed pack {pack.pack_id}",
            )

    def test_echo_only_smoke_runs_against_every_pack(self) -> None:
        """End-to-end --echo-only sweep across every v0.1 pack.

        Per v0.42 priority: 'eval 能跑 skill 而不是 echo' — the runner
        flag still has to work for primitive smoke.  This test catches
        runner / persistence regressions across all packs at once.
        """

        store = EvalStore()
        runner = EvalRunner()
        runs = 0
        for pack in store.list_packs():
            if not pack.pack_id.endswith("/v0.1"):
                continue
            run = runner.run(pack, use_echo_only=True)
            self.assertEqual(run.pack_id, pack.pack_id)
            self.assertGreater(len(run.per_case_results), 0)
            runs += 1
        self.assertGreaterEqual(runs, 30,
            f"smoke should cover ≥30 packs (19 domain + 11 functional), "
            f"got {runs}")


if __name__ == "__main__":              # pragma: no cover
    unittest.main()
