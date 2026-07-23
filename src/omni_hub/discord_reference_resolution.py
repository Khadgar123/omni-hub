"""Deterministic local resolution overlays for nested Discord replies.

The overlay is separate from immutable raw pages and message-evidence rows.  It
contains Discord IDs and cryptographic digests only; content and URLs are never
copied into the audit model.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping

from .discord_message_evidence import extract_message_evidence


_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ReferenceResolutionOccurrence:
    root_channel_id: str
    root_message_id: str
    root_message_sha256: str
    evidence_sha256: str
    occurrence_sha256: str


@dataclass(frozen=True, slots=True)
class ReferenceResolutionBinding:
    top_level_message_sha256: str
    top_level_evidence_sha256: str


@dataclass(frozen=True, slots=True)
class ReferenceResolutionEdge:
    source_channel_id: str
    source_message_id: str
    target_channel_id: str
    target_message_id: str
    outcome: str
    reason_code: str
    occurrence_count: int
    occurrences: tuple[ReferenceResolutionOccurrence, ...]
    bindings: tuple[ReferenceResolutionBinding, ...]
    dependency_edges: tuple[str, ...]
    resolution_depth: int | None


@dataclass(frozen=True, slots=True)
class ReferenceResolutionCounts:
    raw_errors: int
    unique_edges: int
    occurrences: int
    local_resolved: int
    deleted: int
    unresolved: int
    effective_errors: int


@dataclass(frozen=True, slots=True)
class ReferenceResolutionAudit:
    schema_version: int
    kind: str
    max_depth: int
    counts: ReferenceResolutionCounts
    edges: tuple[ReferenceResolutionEdge, ...]

    def to_mapping(self) -> dict[str, Any]:
        value = _strict_json_copy(asdict(self))
        assert isinstance(value, dict)
        return value


@dataclass(frozen=True, slots=True)
class _Root:
    message: dict[str, Any]
    evidence: dict[str, Any]
    channel_id: str
    message_id: str
    message_sha256: str
    evidence_sha256: str
    root_pointer: str


@dataclass(frozen=True, slots=True)
class _Occurrence:
    root: _Root
    source_channel_id: str
    source_message_id: str
    target_channel_id: str
    target_message_id: str
    relative_pointer: str
    occurrence_sha256: str


@dataclass(frozen=True, slots=True)
class _ResolutionPlan:
    state: str
    reason_code: str
    bindings: tuple[tuple[str, str], ...]
    dependencies: tuple[tuple[str, str, str, str], ...]


def resolve_local_references(
    records: Iterable[Mapping[str, Any]],
    *,
    max_depth: int = 8,
) -> ReferenceResolutionAudit:
    """Resolve nested reply omissions from verified top-level message rows."""

    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 0:
        raise ValueError("Discord reference resolution max_depth is invalid")
    roots = _normalize_roots(records)
    index: dict[tuple[str, str], list[_Root]] = {}
    occurrences: list[_Occurrence] = []
    for root in roots:
        index.setdefault((root.channel_id, root.message_id), []).append(root)
        occurrences.extend(_nested_unknown_occurrences(root))

    unique_occurrences = {
        occurrence.occurrence_sha256: occurrence for occurrence in occurrences
    }
    grouped: dict[tuple[str, str, str, str], list[_Occurrence]] = {}
    for occurrence in unique_occurrences.values():
        key = (
            occurrence.source_channel_id,
            occurrence.source_message_id,
            occurrence.target_channel_id,
            occurrence.target_message_id,
        )
        grouped.setdefault(key, []).append(occurrence)

    edge_keys = frozenset(grouped)
    plans = {
        key: _build_resolution_plan(key, index, edge_keys) for key in edge_keys
    }
    outcomes = _resolve_fixed_point(plans, max_depth=max_depth)

    edges: list[ReferenceResolutionEdge] = []
    for key in sorted(grouped, key=_edge_sort_key):
        edge_occurrences = sorted(
            grouped[key], key=lambda item: item.occurrence_sha256
        )
        outcome, reason, depth = outcomes[key]
        plan = plans[key]
        public_occurrences = tuple(
            ReferenceResolutionOccurrence(
                root_channel_id=item.root.channel_id,
                root_message_id=item.root.message_id,
                root_message_sha256=item.root.message_sha256,
                evidence_sha256=item.root.evidence_sha256,
                occurrence_sha256=item.occurrence_sha256,
            )
            for item in edge_occurrences
        )
        edges.append(
            ReferenceResolutionEdge(
                source_channel_id=key[0],
                source_message_id=key[1],
                target_channel_id=key[2],
                target_message_id=key[3],
                outcome=outcome,
                reason_code=reason,
                occurrence_count=len(public_occurrences),
                occurrences=public_occurrences,
                bindings=tuple(
                    ReferenceResolutionBinding(
                        top_level_message_sha256=message_sha256,
                        top_level_evidence_sha256=evidence_sha256,
                    )
                    for message_sha256, evidence_sha256 in plan.bindings
                ),
                dependency_edges=tuple(
                    _edge_id(dependency) for dependency in plan.dependencies
                ),
                resolution_depth=depth,
            )
        )

    raw_errors = sum(edge.occurrence_count for edge in edges)
    local_resolved = sum(
        edge.occurrence_count for edge in edges if edge.outcome == "local_resolved"
    )
    deleted = sum(
        edge.occurrence_count for edge in edges if edge.outcome == "deleted"
    )
    unresolved = raw_errors - local_resolved - deleted
    return ReferenceResolutionAudit(
        schema_version=_SCHEMA_VERSION,
        kind="discord_local_reference_resolution",
        max_depth=max_depth,
        counts=ReferenceResolutionCounts(
            raw_errors=raw_errors,
            unique_edges=len(edges),
            occurrences=raw_errors,
            local_resolved=local_resolved,
            deleted=deleted,
            unresolved=unresolved,
            effective_errors=unresolved,
        ),
        edges=tuple(edges),
    )


def canonical_reference_resolution_bytes(
    audit: ReferenceResolutionAudit,
) -> bytes:
    if not isinstance(audit, ReferenceResolutionAudit):
        raise TypeError("Discord reference resolution audit model is required")
    return _canonical_json_bytes(_strict_json_copy(audit.to_mapping()))


def reference_resolution_sha256(
    audit: ReferenceResolutionAudit,
) -> str:
    return hashlib.sha256(canonical_reference_resolution_bytes(audit)).hexdigest()


def _normalize_roots(records: Iterable[Mapping[str, Any]]) -> list[_Root]:
    if isinstance(records, (str, bytes, bytearray, Mapping)):
        raise TypeError("Discord reference resolution records must be an iterable")
    roots: dict[tuple[str, str], _Root] = {}
    for supplied in records:
        if not isinstance(supplied, Mapping):
            raise TypeError("Discord reference resolution record must be a mapping")
        normalized = _strict_json_copy(supplied)
        if "message" in normalized:
            raw_message = normalized.get("message")
            evidence = normalized.get("evidence")
            if not isinstance(raw_message, dict) or not isinstance(evidence, dict):
                raise ValueError("Discord reference resolution envelope is invalid")
        else:
            raw_message = normalized
            message_sha = _canonical_sha256(raw_message)
            evidence = _strict_json_copy(
                asdict(
                    extract_message_evidence(
                        raw_message,
                        stream="local_reference_resolution",
                        evidence_path=f"in-memory/{message_sha}.json",
                        json_pointer="",
                    )
                )
            )
        message_sha = _canonical_sha256(raw_message)
        evidence_sha = _canonical_sha256(evidence)
        if normalized.get("message_sha256", message_sha) != message_sha:
            raise ValueError("Discord reference resolution message hash mismatch")
        if normalized.get("evidence_sha256", evidence_sha) != evidence_sha:
            raise ValueError("Discord reference resolution evidence hash mismatch")
        channel_id = _snowflake(raw_message.get("channel_id"))
        message_id = _snowflake(raw_message.get("id"))
        if channel_id is None or message_id is None:
            raise ValueError("Discord reference resolution root identity is invalid")
        root_pointer = _validate_evidence(
            raw_message, evidence, channel_id, message_id
        )
        root = _Root(
            message=raw_message,
            evidence=evidence,
            channel_id=channel_id,
            message_id=message_id,
            message_sha256=message_sha,
            evidence_sha256=evidence_sha,
            root_pointer=root_pointer,
        )
        roots[(message_sha, evidence_sha)] = root
    return sorted(
        roots.values(),
        key=lambda item: (
            int(item.channel_id),
            int(item.message_id),
            item.evidence_sha256,
        ),
    )


def _validate_evidence(
    raw_message: dict[str, Any],
    evidence: dict[str, Any],
    channel_id: str,
    message_id: str,
) -> str:
    nodes = evidence.get("nodes")
    diagnostics = evidence.get("diagnostics")
    if not isinstance(nodes, list) or not isinstance(diagnostics, list):
        raise ValueError("Discord reference resolution evidence shape is invalid")
    root_nodes = [
        node
        for node in nodes
        if isinstance(node, dict) and node.get("kind") == "root"
    ]
    if len(root_nodes) != 1:
        raise ValueError("Discord reference resolution root evidence is invalid")
    root = root_nodes[0]
    pointer = root.get("json_pointer")
    if (
        root.get("channel_id") != channel_id
        or root.get("message_id") != message_id
        or not isinstance(pointer, str)
    ):
        raise ValueError("Discord reference resolution evidence identity mismatch")
    rebuilt = _strict_json_copy(
        asdict(
            extract_message_evidence(
                raw_message,
                stream="reference_resolution_validation",
                evidence_path="in-memory/validation.json",
                json_pointer=pointer,
            )
        )
    )
    expected_relevant_nodes = [
        node
        for node in rebuilt["nodes"]
        if node["kind"] in {"root", "referenced_message"}
        and "/message_snapshots/" not in node["json_pointer"]
    ]
    actual_relevant_nodes = [
        node
        for node in nodes
        if isinstance(node, dict)
        and node.get("kind") in {"root", "referenced_message"}
        and "/message_snapshots/" not in str(node.get("json_pointer"))
    ]
    if actual_relevant_nodes != expected_relevant_nodes:
        raise ValueError("Discord reference resolution evidence tamper detected")

    def relevant_diagnostics(items: object) -> list[dict[str, Any]]:
        if not isinstance(items, (list, tuple)):
            raise ValueError("Discord reference resolution diagnostic is invalid")
        if any(not isinstance(item, dict) for item in items):
            raise ValueError("Discord reference resolution diagnostic is invalid")
        return [
            item
            for item in items
            if "/message_snapshots/" not in str(item.get("json_pointer"))
        ]

    if (
        evidence.get("status") != rebuilt["status"]
        or relevant_diagnostics(diagnostics)
        != relevant_diagnostics(rebuilt["diagnostics"])
    ):
        raise ValueError("Discord reference resolution evidence tamper detected")
    candidates = _candidate_descriptors(raw_message)
    node_by_pointer = {
        node.get("json_pointer"): node for node in nodes if isinstance(node, dict)
    }
    for source_channel, source_message, _, _, relative in candidates:
        nested_pointer = _join_pointer(pointer, relative)
        node = node_by_pointer.get(nested_pointer)
        if (
            not isinstance(node, dict)
            or node.get("channel_id") != source_channel
            or node.get("message_id") != source_message
            or node.get("kind") != "referenced_message"
        ):
            raise ValueError("Discord reference resolution nested evidence mismatch")
    return pointer


def _candidate_descriptors(
    root: Mapping[str, Any],
) -> list[tuple[str, str, str, str, str]]:
    result: list[tuple[str, str, str, str, str]] = []

    def walk(current: Mapping[str, Any], pointer: str, *, nested: bool) -> None:
        reference = current.get("message_reference")
        if (
            nested
            and _is_default_reply(current, reference)
            and "referenced_message" not in current
        ):
            source_channel = _snowflake(current.get("channel_id"))
            source_message = _snowflake(current.get("id"))
            assert isinstance(reference, Mapping)
            target_channel = _snowflake(reference.get("channel_id")) or source_channel
            target_message = _snowflake(reference.get("message_id"))
            identities = (
                source_channel,
                source_message,
                target_channel,
                target_message,
            )
            if None in identities:
                raise ValueError(
                    "Discord reference resolution candidate identity is invalid"
                )
            assert source_channel is not None
            assert source_message is not None
            assert target_channel is not None
            assert target_message is not None
            result.append(
                (
                    source_channel,
                    source_message,
                    target_channel,
                    target_message,
                    pointer,
                )
            )
        referenced = current.get("referenced_message")
        if isinstance(referenced, Mapping):
            walk(
                referenced,
                _join_pointer(pointer, "referenced_message"),
                nested=True,
            )

    walk(root, "", nested=False)
    return result


def _nested_unknown_occurrences(root: _Root) -> list[_Occurrence]:
    result: list[_Occurrence] = []
    for (
        source_channel,
        source_message,
        target_channel,
        target_message,
        pointer,
    ) in _candidate_descriptors(root.message):
        summary = {
            "root_channel_id": root.channel_id,
            "root_message_id": root.message_id,
            "root_message_sha256": root.message_sha256,
            "evidence_sha256": root.evidence_sha256,
            "source_channel_id": source_channel,
            "source_message_id": source_message,
            "target_channel_id": target_channel,
            "target_message_id": target_message,
            "relative_pointer": pointer,
        }
        result.append(
            _Occurrence(
                root=root,
                source_channel_id=source_channel,
                source_message_id=source_message,
                target_channel_id=target_channel,
                target_message_id=target_message,
                relative_pointer=pointer,
                occurrence_sha256=_canonical_sha256(summary),
            )
        )
    return result


def _build_resolution_plan(
    key: tuple[str, str, str, str],
    index: Mapping[tuple[str, str], list[_Root]],
    edge_keys: frozenset[tuple[str, str, str, str]],
) -> _ResolutionPlan:
    candidates = index.get(key[:2], [])
    if not candidates:
        return _ResolutionPlan("blocked", "top_level_source_missing", (), ())
    if len({candidate.message_sha256 for candidate in candidates}) != 1:
        return _ResolutionPlan("blocked", "top_level_source_conflict", (), ())
    bindings: list[tuple[str, str]] = []
    states: set[str] = set()
    dependencies: set[tuple[str, str, str, str]] = set()
    for candidate in candidates:
        reference = candidate.message.get("message_reference")
        if not _is_default_reply(candidate.message, reference):
            return _ResolutionPlan("blocked", "top_level_identity_conflict", (), ())
        assert isinstance(reference, Mapping)
        target_channel = _snowflake(reference.get("channel_id")) or candidate.channel_id
        target_message = _snowflake(reference.get("message_id"))
        if (target_channel, target_message) != key[2:]:
            return _ResolutionPlan("blocked", "top_level_target_conflict", (), ())
        if "referenced_message" not in candidate.message:
            states.add("absent")
            continue
        referenced = candidate.message.get("referenced_message")
        if referenced is None:
            states.add("deleted")
            bindings.append(
                (candidate.message_sha256, candidate.evidence_sha256)
            )
            continue
        if not isinstance(referenced, Mapping):
            states.add("invalid")
            continue
        states.add("mapping")
        if (
            _snowflake(referenced.get("channel_id")),
            _snowflake(referenced.get("id")),
        ) != key[2:]:
            return _ResolutionPlan("blocked", "top_level_target_conflict", (), ())
        if not _has_target_evidence(candidate, key[2], key[3]):
            return _ResolutionPlan("blocked", "target_evidence_missing", (), ())
        bindings.append((candidate.message_sha256, candidate.evidence_sha256))
        for descriptor in _candidate_descriptors(candidate.message):
            dependency = descriptor[:4]
            if descriptor[4].startswith("/referenced_message"):
                dependencies.add(dependency)
    if len(states) != 1:
        return _ResolutionPlan("blocked", "top_level_source_conflict", (), ())
    state = next(iter(states))
    if state == "deleted":
        return _ResolutionPlan(
            "deleted", "verified_deleted", tuple(sorted(set(bindings))), ()
        )
    if state == "mapping":
        if not dependencies.issubset(edge_keys):
            return _ResolutionPlan("blocked", "dependency_evidence_missing", (), ())
        return _ResolutionPlan(
            "binding",
            "verified_local_binding",
            tuple(sorted(set(bindings))),
            tuple(sorted(dependencies, key=_edge_sort_key)),
        )
    if state == "absent":
        return _ResolutionPlan("blocked", "top_level_reference_absent", (), ())
    return _ResolutionPlan("blocked", "top_level_reference_invalid", (), ())


def _resolve_fixed_point(
    plans: Mapping[tuple[str, str, str, str], _ResolutionPlan],
    *,
    max_depth: int,
) -> dict[tuple[str, str, str, str], tuple[str, str, int | None]]:
    resolved: dict[tuple[str, str, str, str], tuple[str, str, int | None]] = {}
    for key, plan in plans.items():
        if plan.state == "deleted":
            resolved[key] = ("deleted", plan.reason_code, 0)
        elif plan.state == "blocked":
            resolved[key] = ("unresolved", plan.reason_code, None)

    pending = {key for key, plan in plans.items() if plan.state == "binding"}
    while pending:
        progressed = False
        for key in sorted(tuple(pending), key=_edge_sort_key):
            plan = plans[key]
            dependency_results = [resolved.get(item) for item in plan.dependencies]
            if any(
                result is not None and result[0] == "unresolved"
                for result in dependency_results
            ):
                resolved[key] = ("unresolved", "dependency_unresolved", None)
                pending.remove(key)
                progressed = True
                continue
            if not all(result is not None for result in dependency_results):
                continue
            depths = [
                result[2]
                for result in dependency_results
                if result is not None and result[2] is not None
            ]
            depth = 1 + max(depths, default=0)
            if depth > max_depth:
                resolved[key] = (
                    "unresolved",
                    "resolution_depth_exceeded",
                    None,
                )
            else:
                resolved[key] = ("local_resolved", plan.reason_code, depth)
            pending.remove(key)
            progressed = True
        if not progressed:
            break
    for key in pending:
        resolved[key] = ("unresolved", "reference_cycle", None)
    return resolved


def _edge_id(key: tuple[str, str, str, str]) -> str:
    return _canonical_sha256(
        {
            "source_channel_id": key[0],
            "source_message_id": key[1],
            "target_channel_id": key[2],
            "target_message_id": key[3],
        }
    )


def _has_target_evidence(root: _Root, channel_id: str, message_id: str) -> bool:
    pointer = _join_pointer(root.root_pointer, "referenced_message")
    nodes = root.evidence.get("nodes")
    assert isinstance(nodes, list)
    matches = [
        node
        for node in nodes
        if isinstance(node, dict)
        and node.get("kind") == "referenced_message"
        and node.get("json_pointer") == pointer
        and node.get("channel_id") == channel_id
        and node.get("message_id") == message_id
    ]
    return len(matches) == 1


def _is_default_reply(message: Mapping[str, Any], reference: object) -> bool:
    message_type = message.get("type")
    if (
        type(message_type) is not int
        or message_type != 19
        or not isinstance(reference, Mapping)
    ):
        return False
    reference_type = reference.get("type", 0)
    return type(reference_type) is int and reference_type == 0


def _strict_json_copy(value: object) -> Any:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return json.loads(encoded)
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise ValueError("Discord reference resolution input is not safe JSON") from exc


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _snowflake(value: object) -> str | None:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        return None
    if value == "0" or str(int(value)) != value:
        return None
    return value


def _join_pointer(base: str, *parts: str) -> str:
    pointer = base.rstrip("/")
    for part in parts:
        if not part:
            continue
        for component in part.strip("/").split("/"):
            pointer += "/" + component.replace("~", "~0").replace("/", "~1")
    return pointer


def _edge_sort_key(key: tuple[str, str, str, str]) -> tuple[int, int, int, int]:
    return tuple(int(value) for value in key)  # type: ignore[return-value]
