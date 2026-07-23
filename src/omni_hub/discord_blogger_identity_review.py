"""Private author census and immutable reviewed identity-pack freezing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Iterable, Mapping, Protocol

from .discord_blogger_contract import (
    canonical_json_bytes,
    canonical_json_sha256,
)
from .discord_blogger_evidence import VerifiedMessageEnvelope


_PRIVATE_PREFIX = (".omni", "private")


class _Hasher(Protocol):
    def update(self, value: bytes) -> None: ...

    def hexdigest(self) -> str: ...


@dataclass(slots=True)
class _Candidate:
    target_id: str
    parent_rollup_target_ids: tuple[str, ...]
    author_id: str
    webhook_id: str | None
    application_id: str | None
    observed_from: str
    observed_to: str
    _first: datetime
    _last: datetime
    message_count: int = 0
    delivery_kinds: set[str] = field(default_factory=set)
    evidence: _Hasher = field(default_factory=hashlib.sha256)

    def add(self, envelope: VerifiedMessageEnvelope, delivery_kind: str) -> None:
        instant = _timestamp(envelope.message.timestamp, "message timestamp")
        if instant < self._first:
            self._first = instant
            self.observed_from = envelope.message.timestamp
        if instant > self._last:
            self._last = instant
            self.observed_to = envelope.message.timestamp
        self.message_count += 1
        self.delivery_kinds.add(delivery_kind)
        self.evidence.update(
            canonical_json_bytes(
                {
                    "message_commitment": canonical_json_sha256(
                        {
                            "message_id": envelope.message.message_id,
                            "snapshot_sha256": envelope.message.snapshot_sha256,
                        }
                    ),
                    "snapshot_provenance": [
                        {
                            "source_kind": row.source_kind,
                            "snapshot_sha256": row.snapshot_sha256,
                            "evidence_sha256": row.evidence_sha256,
                            "current": row.current,
                        }
                        for row in envelope.snapshot_provenance
                    ],
                }
            )
        )

    def output(self) -> dict[str, object]:
        candidate_id = canonical_json_sha256(
            {
                "target_id": self.target_id,
                "author_id": self.author_id,
                "webhook_id": self.webhook_id,
                "application_id": self.application_id,
            }
        )
        return {
            "candidate_id": candidate_id,
            "target_id": self.target_id,
            "parent_rollup_target_ids": list(self.parent_rollup_target_ids),
            "author_id": self.author_id,
            "webhook_id": self.webhook_id,
            "application_id": self.application_id,
            "delivery_kinds": sorted(self.delivery_kinds),
            "observed_from": self.observed_from,
            "observed_to": self.observed_to,
            "valid_from": self.observed_from,
            "valid_to": None,
            "message_count": self.message_count,
            "evidence_sha256": self.evidence.hexdigest(),
            "review_required": True,
        }


def build_identity_review_pack(
    *,
    evidence: Iterable[VerifiedMessageEnvelope],
    inventory: Mapping[str, object],
) -> Mapping[str, object]:
    """Build a redacted census; it makes no identity decision by itself."""

    parent_rollups = _inventory_parent_rollups(inventory)
    groups: dict[tuple[str, str, str | None, str | None], _Candidate] = {}
    unknown_delivery_count = 0
    for envelope in evidence:
        if not isinstance(envelope, VerifiedMessageEnvelope):
            raise TypeError(
                "Discord identity census requires verified evidence envelopes"
            )
        message = envelope.message
        if message.channel_id not in parent_rollups:
            raise ValueError("Discord identity census target is outside inventory")
        if not envelope.evidence.nodes:
            raise ValueError("Discord identity census root evidence is missing")
        root = envelope.evidence.nodes[0]
        if root.kind != "root" or root.message_id != message.message_id:
            raise ValueError("Discord identity census root evidence is invalid")
        delivery = root.attribution
        if delivery.author_id is None:
            unknown_delivery_count += 1
            continue
        key = (
            message.channel_id,
            delivery.author_id,
            delivery.webhook_id,
            delivery.application_id,
        )
        instant = _timestamp(message.timestamp, "message timestamp")
        candidate = groups.get(key)
        if candidate is None:
            candidate = _Candidate(
                target_id=message.channel_id,
                parent_rollup_target_ids=parent_rollups[message.channel_id],
                author_id=delivery.author_id,
                webhook_id=delivery.webhook_id,
                application_id=delivery.application_id,
                observed_from=message.timestamp,
                observed_to=message.timestamp,
                _first=instant,
                _last=instant,
            )
            groups[key] = candidate
        candidate.add(envelope, delivery.kind)

    candidates = [
        candidate.output()
        for _, candidate in sorted(
            groups.items(),
            key=lambda item: tuple(
                "" if value is None else value for value in item[0]
            ),
        )
    ]
    return {
        "artifact_kind": "discord-blogger-identity-review-candidate-v1",
        "schema_version": 1,
        "verification_policy": "exact_ids_require_review",
        "inventory_sha256": canonical_json_sha256(dict(inventory)),
        "candidate_count": len(candidates),
        "unknown_delivery_count": unknown_delivery_count,
        "candidates": candidates,
    }


def freeze_identity_review_pack(
    *,
    candidate_pack: Path,
    reviewed_labels: Path,
    output_path: Path,
) -> Mapping[str, object]:
    """Freeze every review row into a canonical 0600 no-clobber JSON list."""

    candidate_path = _private_path(candidate_pack, "candidate pack")
    labels_path = _private_path(reviewed_labels, "reviewed labels")
    output = _private_path(output_path, "review output", must_exist=False)
    candidate_bytes = _read_canonical(candidate_path, "candidate pack")
    labels_bytes = _read_canonical(labels_path, "reviewed labels")
    candidate_sha = hashlib.sha256(candidate_bytes).hexdigest()
    labels_sha = hashlib.sha256(labels_bytes).hexdigest()
    candidate = json.loads(candidate_bytes)
    labels = json.loads(labels_bytes)
    candidates = _candidates(candidate)
    review_by_id = _reviews(labels, candidate_sha)
    candidate_ids = {str(row["candidate_id"]) for row in candidates}
    if set(review_by_id) != candidate_ids:
        raise ValueError(
            "Discord identity review must label every candidate exactly once"
        )

    frozen: list[dict[str, object]] = []
    for candidate_row in candidates:
        candidate_id = str(candidate_row["candidate_id"])
        label = review_by_id[candidate_id]
        if label["evidence_sha256"] != candidate_row["evidence_sha256"]:
            raise ValueError("Discord identity review evidence commitment changed")
        decision = str(label["decision"])
        identity_type = str(label["identity_type"])
        target_type = str(label["target_type"])
        aggregation_scope = str(label["aggregation_scope"])
        performance_owner_id = label["performance_owner_id"]
        aggregation_owner_id = label["aggregation_owner_id"]
        _validate_decision(
            decision,
            identity_type,
            target_type,
            aggregation_scope,
            performance_owner_id,
            aggregation_owner_id,
        )
        frozen.append(
            {
                "schema_version": 1,
                "record_type": "identity_review",
                "candidate_id": candidate_id,
                "target_id": candidate_row["target_id"],
                "author_id": candidate_row["author_id"],
                "webhook_id": candidate_row["webhook_id"],
                "application_id": candidate_row["application_id"],
                "valid_from": candidate_row["valid_from"],
                "valid_to": candidate_row["valid_to"],
                "decision": decision,
                "identity_type": identity_type,
                "target_type": target_type,
                "aggregation_scope": aggregation_scope,
                "performance_owner_id": performance_owner_id,
                "aggregation_owner_id": aggregation_owner_id,
                "reviewer": label["reviewer"],
                "reviewed_at": label["reviewed_at"],
                "evidence_sha256": candidate_row["evidence_sha256"],
                "candidate_pack_sha256": candidate_sha,
                "reviewed_labels_sha256": labels_sha,
            }
        )
    frozen.sort(key=lambda row: str(row["candidate_id"]))
    manifest = {
        "schema_version": 1,
        "record_type": "manifest",
        "reviewed_row_count": len(frozen),
        "reviewed_rows_sha256": canonical_json_sha256(frozen),
        "candidate_pack_sha256": candidate_sha,
        "reviewed_labels_sha256": labels_sha,
    }
    content = canonical_json_bytes([manifest, *frozen])
    _write_private_no_clobber(output, content)
    return {
        "output_path": str(output),
        "output_sha256": hashlib.sha256(content).hexdigest(),
        "candidate_pack_sha256": candidate_sha,
        "reviewed_labels_sha256": labels_sha,
        "reviewed_count": len(frozen),
    }


def _inventory_parent_rollups(
    inventory: Mapping[str, object],
) -> dict[str, tuple[str, ...]]:
    if (
        not isinstance(inventory, Mapping)
        or inventory.get("artifact_kind")
        != "discord-blogger-target-inventory-v1"
        or inventory.get("schema_version") != 1
    ):
        raise ValueError("Discord identity census inventory is invalid")
    rows = inventory.get("targets")
    if not isinstance(rows, list):
        raise ValueError("Discord identity census targets are invalid")
    target_by_id: dict[str, Mapping[str, object]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("Discord identity census target is invalid")
        target_id = _id(row.get("target_id"), "target")
        if target_id in target_by_id:
            raise ValueError("Discord identity census targets are duplicated")
        target_by_id[target_id] = row
    output: dict[str, tuple[str, ...]] = {}
    for target_id, row in target_by_id.items():
        parent = row.get("parent_id")
        if (
            isinstance(parent, str)
            and parent in target_by_id
            and target_by_id[parent].get("count_semantics") == "family_rollup"
        ):
            output[target_id] = (parent,)
        else:
            output[target_id] = ()
    return output


def _candidates(value: object) -> list[Mapping[str, object]]:
    if (
        not isinstance(value, Mapping)
        or value.get("artifact_kind")
        != "discord-blogger-identity-review-candidate-v1"
        or value.get("schema_version") != 1
    ):
        raise ValueError("Discord identity candidate pack is invalid")
    rows = value.get("candidates")
    if not isinstance(rows, list) or value.get("candidate_count") != len(rows):
        raise ValueError("Discord identity candidate rows are invalid")
    output: list[Mapping[str, object]] = []
    seen: set[str] = set()
    required = {
        "candidate_id",
        "target_id",
        "parent_rollup_target_ids",
        "author_id",
        "webhook_id",
        "application_id",
        "delivery_kinds",
        "observed_from",
        "observed_to",
        "valid_from",
        "valid_to",
        "message_count",
        "evidence_sha256",
        "review_required",
    }
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != required:
            raise ValueError("Discord identity candidate row is invalid")
        candidate_id = _sha(row["candidate_id"], "candidate ID")
        if candidate_id in seen:
            raise ValueError("Discord identity candidate rows are duplicated")
        seen.add(candidate_id)
        _id(row["target_id"], "target")
        _id(row["author_id"], "author")
        for field in ("webhook_id", "application_id"):
            if row[field] is not None:
                _id(row[field], field)
        _sha(row["evidence_sha256"], "evidence")
        start = _timestamp(row["valid_from"], "valid_from")
        if row["valid_to"] is not None:
            end = _timestamp(row["valid_to"], "valid_to")
            if start >= end:
                raise ValueError("Discord identity candidate validity is invalid")
        if row["review_required"] is not True:
            raise ValueError("Discord identity candidate review gate is invalid")
        output.append(row)
    return output


def _reviews(
    value: object, candidate_sha: str
) -> dict[str, Mapping[str, object]]:
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != 1
        or value.get("candidate_pack_sha256") != candidate_sha
    ):
        raise ValueError("Discord identity candidate pack SHA-256 changed")
    rows = value.get("labels")
    if not isinstance(rows, list):
        raise ValueError("Discord identity reviewed labels are invalid")
    output: dict[str, Mapping[str, object]] = {}
    required = {
        "candidate_id",
        "decision",
        "identity_type",
        "target_type",
        "aggregation_scope",
        "performance_owner_id",
        "aggregation_owner_id",
        "reviewer",
        "reviewed_at",
        "evidence_sha256",
    }
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != required:
            raise ValueError("Discord identity reviewed label is invalid")
        candidate_id = _sha(row["candidate_id"], "candidate ID")
        if candidate_id in output:
            raise ValueError("Discord identity reviewed labels are duplicated")
        _sha(row["evidence_sha256"], "evidence")
        if (
            not isinstance(row["reviewer"], str)
            or not str(row["reviewer"]).strip()
        ):
            raise ValueError("Discord identity reviewer is invalid")
        _timestamp(row["reviewed_at"], "reviewed_at")
        output[candidate_id] = row
    return output


def _validate_decision(
    decision: str,
    identity_type: str,
    target_type: str,
    aggregation_scope: str,
    performance_owner_id: object,
    aggregation_owner_id: object,
) -> None:
    if decision == "accepted":
        allowed = {
            ("single_author_analyst", "owner", "message_owner"),
            ("multi_author_team", "team", "target_rollup_only"),
            ("signal_delivery_channel", "proxy", "target_rollup_only"),
            ("community_chat", "community", "no_performance"),
            ("news_or_aggregation", "community", "no_performance"),
        }
        if (target_type, identity_type, aggregation_scope) not in allowed:
            raise ValueError("accepted target/identity aggregation is invalid")
        if aggregation_scope == "message_owner":
            _id(performance_owner_id, "performance owner")
            if aggregation_owner_id is not None:
                raise ValueError("message owner aggregation is invalid")
        elif aggregation_scope == "target_rollup_only":
            if performance_owner_id is not None:
                raise ValueError("target rollup cannot name a message owner")
            _id(aggregation_owner_id, "aggregation owner")
        elif (
            performance_owner_id is not None
            or aggregation_owner_id is not None
        ):
            raise ValueError("no-performance review cannot name an owner")
    elif decision == "rejected":
        if (
            identity_type != "unknown"
            or target_type != "unknown"
            or aggregation_scope != "no_performance"
            or performance_owner_id is not None
            or aggregation_owner_id is not None
        ):
            raise ValueError("rejected identity must remain unknown")
    elif decision == "conflicting":
        if (
            identity_type != "conflict"
            or target_type != "unknown"
            or aggregation_scope != "no_performance"
            or performance_owner_id is not None
            or aggregation_owner_id is not None
        ):
            raise ValueError("conflicting identity cannot name an owner")
    else:
        raise ValueError("Discord identity review decision is invalid")


def _private_path(
    value: Path, label: str, *, must_exist: bool = True
) -> Path:
    path = Path(value).absolute()
    parts = path.parts
    private_indexes = [
        index
        for index in range(len(parts) - 1)
        if parts[index : index + 2] == _PRIVATE_PREFIX
    ]
    if not private_indexes:
        raise ValueError(f"Discord identity {label} must be under .omni/private")
    private_index = private_indexes[-1]
    current = Path(*parts[:private_index]).resolve(strict=True)
    for part in parts[private_index:-1]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if must_exist:
                raise ValueError(
                    f"Discord identity {label} parent is missing"
                ) from None
            break
        if stat.S_ISLNK(mode):
            raise ValueError(
                f"Discord identity {label} path contains a symbolic link"
            )
        if not stat.S_ISDIR(mode):
            raise ValueError(
                f"Discord identity {label} parent is not a directory"
            )
    if must_exist:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            raise ValueError(f"Discord identity {label} is missing") from None
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise ValueError(f"Discord identity {label} is not a regular file")
        if stat.S_IMODE(mode) != 0o600:
            raise ValueError(f"Discord identity {label} must use mode 0600")
    return path


def _read_canonical(path: Path, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        content = b""
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            content += chunk
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or (before.st_dev, before.st_ino, before.st_size)
        != (after.st_dev, after.st_ino, after.st_size)
    ):
        raise ValueError(f"Discord identity {label} changed during read")
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Discord identity {label} is unreadable") from exc
    if content != canonical_json_bytes(value):
        raise ValueError(f"Discord identity {label} must be canonical JSON")
    return content


def _write_private_no_clobber(path: Path, content: bytes) -> None:
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("Discord identity review output parent is unsafe")
    if path.exists():
        existing = _read_canonical(path, "review output")
        if existing == content:
            if stat.S_IMODE(path.stat().st_mode) != 0o600:
                raise ValueError("Discord identity review output mode changed")
            return
        raise FileExistsError("Discord identity review output already exists")
    descriptor, stage_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=parent,
    )
    stage = Path(stage_name)
    try:
        os.fchmod(descriptor, 0o600)
        written = 0
        while written < len(content):
            written += os.write(descriptor, content[written:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.link(stage, path, follow_symlinks=False)
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        stage.unlink(missing_ok=True)
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode) or stat.S_IMODE(mode) != 0o600:
        raise ValueError("Discord identity review output mode changed")


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"Discord identity {label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Discord identity {label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Discord identity {label} must be timezone-aware")
    return parsed.astimezone(UTC)


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.isdecimal() or int(value) <= 0:
        raise ValueError(f"Discord identity {label} ID is invalid")
    return value


def _sha(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"Discord identity {label} SHA-256 is invalid")
    return value
