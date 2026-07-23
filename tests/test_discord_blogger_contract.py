from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omni_hub.discord_blogger_contract import (
    BaselineRunContract,
    deterministic_entity_id,
    finalize_derivation_contract,
    load_baseline_run_contract,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class BaselineRunContractTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, dict[str, Path], dict[str, object]]:
        inputs = root / "inputs"
        inputs.mkdir()
        source_paths: dict[str, Path] = {}
        for name in ("design", "plan", "code", "target_snapshot", "closure_audit"):
            path = inputs / f"{name}.json"
            value = (
                {"kind": name, "version": 1, "targets": [{"id": "10"}, {"id": "20"}]}
                if name == "target_snapshot"
                else {"kind": name, "version": 1}
            )
            path.write_bytes(_canonical(value))
            source_paths[name] = path
        corpus = "9" * 64
        inventory = {
            "authorized_corpus_commitment_sha256": corpus,
            "targets": [{"target_id": "10"}, {"target_id": "20"}],
        }
        inventory_path = inputs / "inventory.json"
        inventory_path.write_bytes(_canonical(inventory))
        payload: dict[str, object] = {
            "run_id": "full-v2-test",
            "design_sha256": hashlib.sha256(source_paths["design"].read_bytes()).hexdigest(),
            "plan_sha256": hashlib.sha256(source_paths["plan"].read_bytes()).hexdigest(),
            "code_sha256": hashlib.sha256(source_paths["code"].read_bytes()).hexdigest(),
            "target_snapshot_sha256": hashlib.sha256(
                source_paths["target_snapshot"].read_bytes()
            ).hexdigest(),
            "closure_audit_sha256": hashlib.sha256(
                source_paths["closure_audit"].read_bytes()
            ).hexdigest(),
            "inventory_path": "inputs/inventory.json",
            "inventory_sha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
            "corpus_commitment": corpus,
            "baseline_upper_bound": "2026-07-21T00:57:18.979Z",
            "target_ids": ["10", "20"],
        }
        contract_path = root / "baseline.json"
        contract_path.write_bytes(_canonical(payload))
        return contract_path, source_paths, payload

    def test_load_revalidates_every_bound_file_and_exact_target_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path, source_paths, _ = self._fixture(root)
            contract = load_baseline_run_contract(
                contract_path,
                root=root,
                source_paths=source_paths,
                expected_target_ids=("20", "10"),
            )
            self.assertIsInstance(contract, BaselineRunContract)
            self.assertEqual(contract.target_ids, ("10", "20"))

    def test_rejects_inventory_hash_and_corpus_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path, source_paths, payload = self._fixture(root)
            inventory_path = root / "inputs/inventory.json"
            inventory = json.loads(inventory_path.read_bytes())
            inventory["authorized_corpus_commitment_sha256"] = "8" * 64
            inventory_path.write_bytes(_canonical(inventory))
            payload["inventory_sha256"] = hashlib.sha256(
                inventory_path.read_bytes()
            ).hexdigest()
            contract_path.write_bytes(_canonical(payload))
            with self.assertRaisesRegex(ValueError, "corpus"):
                load_baseline_run_contract(
                    contract_path,
                    root=root,
                    source_paths=source_paths,
                    expected_target_ids=("10", "20"),
                )

            inventory["authorized_corpus_commitment_sha256"] = "9" * 64
            inventory_path.write_bytes(_canonical(inventory))
            with self.assertRaisesRegex(ValueError, "inventory.*SHA-256"):
                load_baseline_run_contract(
                    contract_path,
                    root=root,
                    source_paths=source_paths,
                    expected_target_ids=("10", "20"),
                )

    def test_rejects_source_sha_drift_and_unsafe_inventory_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path, source_paths, payload = self._fixture(root)
            source_paths["plan"].write_bytes(_canonical({"kind": "changed"}))
            with self.assertRaisesRegex(ValueError, "plan.*SHA-256"):
                load_baseline_run_contract(
                    contract_path,
                    root=root,
                    source_paths=source_paths,
                    expected_target_ids=("10", "20"),
                )
            source_paths["plan"].write_bytes(
                _canonical({"kind": "plan", "version": 1})
            )
            payload["inventory_path"] = "../inventory.json"
            contract_path.write_bytes(_canonical(payload))
            with self.assertRaises(PermissionError):
                load_baseline_run_contract(
                    contract_path,
                    root=root,
                    source_paths=source_paths,
                    expected_target_ids=("10", "20"),
                )

    def test_rejects_root_contained_absolute_inventory_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path, source_paths, payload = self._fixture(root)
            payload["inventory_path"] = str((root / "inputs/inventory.json").resolve())
            contract_path.write_bytes(_canonical(payload))
            with self.assertRaisesRegex(ValueError, "relative"):
                load_baseline_run_contract(
                    contract_path,
                    root=root,
                    source_paths=source_paths,
                    expected_target_ids=("10", "20"),
                )

    def test_hash_and_parse_use_one_snapshot_of_each_bound_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path, source_paths, _ = self._fixture(root)
            inventory_path = (root / "inputs/inventory.json").resolve()
            snapshot_path = source_paths["target_snapshot"].resolve()
            inventory_first = inventory_path.read_bytes()
            snapshot_first = snapshot_path.read_bytes()
            inventory_second = _canonical(
                {
                    **json.loads(inventory_first),
                    "unbound_substitution": True,
                }
            )
            snapshot_second = _canonical(
                {
                    **json.loads(snapshot_first),
                    "unbound_substitution": True,
                }
            )
            original_read_bytes = Path.read_bytes
            counts = {inventory_path: 0, snapshot_path: 0}

            def changing_read(path: Path) -> bytes:
                resolved = path.resolve()
                if resolved in counts:
                    counts[resolved] += 1
                    if counts[resolved] > 1:
                        return (
                            inventory_second
                            if resolved == inventory_path
                            else snapshot_second
                        )
                return original_read_bytes(path)

            with patch.object(Path, "read_bytes", changing_read):
                load_baseline_run_contract(
                    contract_path,
                    root=root,
                    source_paths=source_paths,
                    expected_target_ids=("10", "20"),
                )
            self.assertEqual(counts[inventory_path], 1)
            self.assertEqual(counts[snapshot_path], 1)

    def test_rejects_duplicate_missing_or_unexpected_target_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path, source_paths, payload = self._fixture(root)
            payload["target_ids"] = ["10", "10"]
            contract_path.write_bytes(_canonical(payload))
            with self.assertRaisesRegex(ValueError, "target"):
                load_baseline_run_contract(
                    contract_path,
                    root=root,
                    source_paths=source_paths,
                    expected_target_ids=("10", "20"),
                )

    def test_rejects_hash_valid_target_snapshot_set_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path, source_paths, payload = self._fixture(root)
            source_paths["target_snapshot"].write_bytes(
                _canonical(
                    {
                        "kind": "target_snapshot",
                        "version": 1,
                        "targets": [{"id": "10"}, {"id": "30"}],
                    }
                )
            )
            payload["target_snapshot_sha256"] = hashlib.sha256(
                source_paths["target_snapshot"].read_bytes()
            ).hexdigest()
            contract_path.write_bytes(_canonical(payload))
            with self.assertRaisesRegex(ValueError, "target snapshot"):
                load_baseline_run_contract(
                    contract_path,
                    root=root,
                    source_paths=source_paths,
                    expected_target_ids=("10", "20"),
                )

    def test_contract_json_must_already_be_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path, source_paths, payload = self._fixture(root)
            contract_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "canonical JSON"):
                load_baseline_run_contract(
                    contract_path,
                    root=root,
                    source_paths=source_paths,
                    expected_target_ids=("10", "20"),
                )


class DerivationContractTests(unittest.TestCase):
    def test_finalization_validates_hashes_and_h2_group(self) -> None:
        required = {
            "baseline_contract_sha256": "1" * 64,
            "identity_registry_sha256": "2" * 64,
            "classifier_schema_sha256": "3" * 64,
            "classifier_evaluation_sha256": "4" * 64,
            "model_attempt_profile_sha256": "5" * 64,
            "media_input_manifest_sha256": "6" * 64,
            "instrument_registry_sha256": "7" * 64,
            "adapter_policy_sha256": "8" * 64,
            "market_manifest_sha256": "9" * 64,
        }
        contract = finalize_derivation_contract(**required)
        self.assertEqual(contract.baseline_contract_sha256, "1" * 64)
        with self.assertRaisesRegex(ValueError, "h2"):
            finalize_derivation_contract(**required, h2_delta_sha256="a" * 64)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            finalize_derivation_contract(
                **{**required, "market_manifest_sha256": "not-a-sha"}
            )

    def test_deterministic_ids_use_canonical_json_and_domain_separation(self) -> None:
        left = deterministic_entity_id("message_revision", {"b": 2, "a": 1}, ("x", 3))
        right = deterministic_entity_id("message_revision", {"a": 1, "b": 2}, ["x", 3])
        other = deterministic_entity_id("event_revision", {"a": 1, "b": 2}, ["x", 3])
        self.assertEqual(left, right)
        self.assertNotEqual(left, other)
        self.assertRegex(left, r"^message_revision:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
