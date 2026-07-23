"""Hash-bound contracts for the full Discord blogger derivation run.

The contracts contain no Discord content.  They freeze only identifiers,
paths, upper bounds, and SHA-256 commitments so every downstream reducer can
prove which immutable baseline it consumed.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping, Sequence

from ._storage import safe_workspace_path


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BASELINE_SOURCE_FIELDS = {
    "design": "design_sha256",
    "plan": "plan_sha256",
    "code": "code_sha256",
    "target_snapshot": "target_snapshot_sha256",
    "closure_audit": "closure_audit_sha256",
}


@dataclass(frozen=True, slots=True)
class BaselineRunContract:
    run_id: str
    design_sha256: str
    plan_sha256: str
    code_sha256: str
    target_snapshot_sha256: str
    closure_audit_sha256: str
    inventory_path: str
    inventory_sha256: str
    corpus_commitment: str
    baseline_upper_bound: str
    target_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DerivationRunContract:
    baseline_contract_sha256: str
    identity_registry_sha256: str
    classifier_schema_sha256: str
    classifier_evaluation_sha256: str
    model_attempt_profile_sha256: str
    media_input_manifest_sha256: str
    instrument_registry_sha256: str
    adapter_policy_sha256: str
    market_manifest_sha256: str
    h2_delta_sha256: str | None
    h2_union_sha256: str | None
    family_census_sha256: str | None
    source_revalidation_sha256: str | None


def canonical_json_bytes(value: object) -> bytes:
    """Return the one accepted JSON byte representation.

    ``allow_nan=False`` rejects values that JSON permits only as a
    non-standard extension.  Mapping keys must already be strings so distinct
    Python keys cannot collapse to the same JSON object member.
    """

    _validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def deterministic_entity_id(kind: str, *canonical_parts: object) -> str:
    """Derive a domain-separated stable identifier from strict canonical JSON."""

    if not isinstance(kind, str) or not kind or kind.strip() != kind:
        raise ValueError("entity kind must be a non-empty canonical string")
    digest = canonical_json_sha256({"kind": kind, "parts": canonical_parts})
    return f"{kind}:{digest}"


def load_baseline_run_contract(
    contract_path: Path | str,
    *,
    root: Path | str | None = None,
    source_paths: Mapping[str, Path | str],
    expected_target_ids: Sequence[str] | None = None,
) -> BaselineRunContract:
    """Load and revalidate a baseline contract against every bound input.

    ``source_paths`` must contain ``design``, ``plan``, ``code``,
    ``target_snapshot`` and ``closure_audit``.  The inventory path comes from
    the contract and is resolved beneath ``root`` to prevent path substitution.
    """

    contract_file = Path(contract_path).resolve()
    workspace = Path(root).resolve() if root is not None else contract_file.parent
    _require_under_root(contract_file, workspace, "contract")
    payload = _read_canonical_object(contract_file, "baseline contract")
    required = {
        "run_id",
        *_BASELINE_SOURCE_FIELDS.values(),
        "inventory_path",
        "inventory_sha256",
        "corpus_commitment",
        "baseline_upper_bound",
        "target_ids",
    }
    if set(payload) != required:
        missing = sorted(required - set(payload))
        unexpected = sorted(set(payload) - required)
        raise ValueError(
            f"baseline contract fields mismatch: missing={missing}, unexpected={unexpected}"
        )

    for source_name, hash_field in _BASELINE_SOURCE_FIELDS.items():
        if source_name not in source_paths:
            raise ValueError(f"missing baseline source path: {source_name}")
        source_path = Path(source_paths[source_name]).resolve()
        _require_under_root(source_path, workspace, source_name)
        _require_file_sha(source_path, payload[hash_field], source_name)
    target_snapshot = _read_json_object(
        Path(source_paths["target_snapshot"]).resolve(), "target snapshot"
    )

    inventory_relative = payload["inventory_path"]
    if not isinstance(inventory_relative, str) or not inventory_relative:
        raise ValueError("inventory_path must be a non-empty relative path")
    inventory_path = safe_workspace_path(workspace, inventory_relative)
    _require_file_sha(inventory_path, payload["inventory_sha256"], "inventory")
    inventory = _read_json_object(inventory_path, "inventory")

    corpus = _require_sha(payload["corpus_commitment"], "corpus commitment")
    inventory_corpus = _inventory_corpus_commitment(inventory)
    if inventory_corpus != corpus:
        raise ValueError("inventory corpus commitment does not match baseline contract")

    target_ids = _target_id_tuple(payload["target_ids"], "contract")
    inventory_ids = _inventory_target_ids(inventory)
    if set(target_ids) != set(inventory_ids) or len(target_ids) != len(inventory_ids):
        raise ValueError("contract target IDs do not exactly match inventory target IDs")
    snapshot_ids = _snapshot_target_ids(target_snapshot)
    if set(target_ids) != set(snapshot_ids) or len(target_ids) != len(snapshot_ids):
        raise ValueError(
            "contract target IDs do not exactly match target snapshot IDs"
        )
    if expected_target_ids is not None:
        expected = _target_id_tuple(list(expected_target_ids), "expected")
        if set(target_ids) != set(expected) or len(target_ids) != len(expected):
            raise ValueError("contract target IDs do not exactly match expected target IDs")

    values = dict(payload)
    for field in _BASELINE_SOURCE_FIELDS.values():
        values[field] = _require_sha(values[field], field)
    values["inventory_sha256"] = _require_sha(
        values["inventory_sha256"], "inventory SHA-256"
    )
    values["corpus_commitment"] = corpus
    values["target_ids"] = tuple(sorted(target_ids, key=_id_sort_key))
    for text_field in ("run_id", "baseline_upper_bound"):
        if not isinstance(values[text_field], str) or not values[text_field]:
            raise ValueError(f"{text_field} must be a non-empty string")
    return BaselineRunContract(**values)


def finalize_derivation_contract(
    *,
    baseline_contract_sha256: str,
    identity_registry_sha256: str,
    classifier_schema_sha256: str,
    classifier_evaluation_sha256: str,
    model_attempt_profile_sha256: str,
    media_input_manifest_sha256: str,
    instrument_registry_sha256: str,
    adapter_policy_sha256: str,
    market_manifest_sha256: str,
    h2_delta_sha256: str | None = None,
    h2_union_sha256: str | None = None,
    family_census_sha256: str | None = None,
    source_revalidation_sha256: str | None = None,
) -> DerivationRunContract:
    """Validate and freeze the hashes consumed by deterministic reducers."""

    required = {
        "baseline_contract_sha256": baseline_contract_sha256,
        "identity_registry_sha256": identity_registry_sha256,
        "classifier_schema_sha256": classifier_schema_sha256,
        "classifier_evaluation_sha256": classifier_evaluation_sha256,
        "model_attempt_profile_sha256": model_attempt_profile_sha256,
        "media_input_manifest_sha256": media_input_manifest_sha256,
        "instrument_registry_sha256": instrument_registry_sha256,
        "adapter_policy_sha256": adapter_policy_sha256,
        "market_manifest_sha256": market_manifest_sha256,
    }
    validated = {
        name: _require_sha(value, name) for name, value in required.items()
    }
    optional = {
        "h2_delta_sha256": h2_delta_sha256,
        "h2_union_sha256": h2_union_sha256,
        "family_census_sha256": family_census_sha256,
        "source_revalidation_sha256": source_revalidation_sha256,
    }
    present = [value is not None for value in optional.values()]
    if any(present) and not all(present):
        raise ValueError("h2 derivation hashes must be supplied as one complete group")
    validated.update(
        {
            name: None if value is None else _require_sha(value, name)
            for name, value in optional.items()
        }
    )
    return DerivationRunContract(**validated)


def _read_canonical_object(path: Path, label: str) -> dict[str, object]:
    raw = path.read_bytes()
    value = _decode_json(raw, label)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    if raw != canonical_json_bytes(value):
        raise ValueError(f"{label} must use strict canonical JSON bytes")
    return value


def _read_json_object(path: Path, label: str) -> dict[str, object]:
    value = _decode_json(path.read_bytes(), label)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    _validate_json_value(value)
    return value


def _decode_json(raw: bytes, label: str) -> object:
    try:
        text = raw.decode("utf-8")
        return json.loads(
            text,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{label} is not strict UTF-8 JSON") from exc


def _validate_json_value(value: object) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("canonical JSON rejects non-finite numbers")
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("canonical JSON object keys must be strings")
            _validate_json_value(item)
        return
    raise TypeError(f"value is not representable as canonical JSON: {type(value).__name__}")


def _require_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _require_file_sha(path: Path, expected: object, label: str) -> None:
    expected_sha = _require_sha(expected, f"{label} SHA-256")
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{label} input must be a regular file")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected_sha:
        raise ValueError(f"{label} SHA-256 drift")


def _require_under_root(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"{label} path is outside the run root") from exc


def _inventory_corpus_commitment(inventory: Mapping[str, object]) -> str:
    candidates = [
        inventory.get("authorized_corpus_commitment_sha256"),
        inventory.get("corpus_commitment"),
    ]
    provenance = inventory.get("provenance")
    if isinstance(provenance, Mapping):
        candidates.extend(
            [
                provenance.get("corpus_commitment"),
                provenance.get("authorized_corpus_commitment_sha256"),
            ]
        )
    for candidate in candidates:
        if isinstance(candidate, str):
            return _require_sha(candidate, "inventory corpus commitment")
    raise ValueError("inventory corpus commitment is missing")


def _inventory_target_ids(inventory: Mapping[str, object]) -> tuple[str, ...]:
    targets = inventory.get("targets")
    if not isinstance(targets, list):
        raise ValueError("inventory targets must be a list")
    identifiers: list[str] = []
    for row in targets:
        if not isinstance(row, Mapping):
            raise ValueError("inventory target row must be an object")
        identifier = row.get("target_id", row.get("id"))
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("inventory target ID must be a non-empty string")
        identifiers.append(identifier)
    return _target_id_tuple(identifiers, "inventory")


def _snapshot_target_ids(snapshot: Mapping[str, object]) -> tuple[str, ...]:
    targets = snapshot.get("targets")
    if not isinstance(targets, list):
        raise ValueError("target snapshot targets must be a list")
    identifiers: list[str] = []
    for row in targets:
        if not isinstance(row, Mapping):
            raise ValueError("target snapshot target row must be an object")
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("target snapshot target ID must be a non-empty string")
        identifiers.append(identifier)
    return _target_id_tuple(identifiers, "target snapshot")


def _target_id_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} target IDs must be an array")
    identifiers = tuple(value)
    if any(not isinstance(item, str) or not item for item in identifiers):
        raise ValueError(f"{label} target IDs must be non-empty strings")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError(f"{label} target IDs contain duplicates")
    return identifiers


def _id_sort_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)
