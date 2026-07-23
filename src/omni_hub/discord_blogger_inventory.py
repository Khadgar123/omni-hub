"""Redacted exact-target and parent-family projections for Discord bloggers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from typing import Iterable, Mapping, Sequence

from .discord_blogger_corpus import BloggerMessage
from .discord_reference_sidecar import _RootAnchor
from .discord_sharding import canonical_json_bytes, target_set_sha256


_THREAD_KIND = "THREAD"
_FORUM_KIND = "GUILD_FORUM"
_PRIVATE_FILE_MODE = 0o600
_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "content",
        "logical_key",
        "media_occurrence_refs",
        "message_ids",
        "snapshot_ref",
        "token",
        "url",
    }
)


@dataclass(slots=True)
class _Counts:
    message_count: int = 0
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    _first: datetime | None = None
    _last: datetime | None = None
    _authors: set[str] = field(default_factory=set)

    def add(self, message: BloggerMessage) -> None:
        when = _timestamp(message.timestamp)
        self.message_count += 1
        if message.author_id is not None:
            self._authors.add(message.author_id)
        if self._first is None or when < self._first:
            self._first = when
            self.first_timestamp = message.timestamp
        if self._last is None or when > self._last:
            self._last = when
            self.last_timestamp = message.timestamp

    def output(self) -> dict[str, object]:
        return {
            "message_count": self.message_count,
            "distinct_author_count": len(self._authors),
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
        }


def build_blogger_target_inventory(
    *,
    messages: Iterable[BloggerMessage],
    target_snapshot: Mapping[str, object],
    discovered_threads: Sequence[Mapping[str, object]],
    provenance: Mapping[str, object],
    private_archived_blocked_parent_ids: Sequence[str],
    family_parent_target_ids: Sequence[str],
) -> dict[str, object]:
    """Build overlapping exact/family views from one unique authorized corpus."""

    targets = _targets(target_snapshot)
    target_by_id = {str(target["id"]): target for target in targets}
    family_parents = _family_parents(family_parent_target_ids, target_by_id)
    family_owner = _family_owner(
        discovered_threads, target_by_id, family_parents
    )
    private_blockers = _private_blockers(
        private_archived_blocked_parent_ids, target_by_id, family_parents
    )
    exact: dict[str, _Counts] = {}
    family: dict[str, _Counts] = {}
    with tempfile.TemporaryDirectory(
        prefix="omni-discord-inventory-ledger-"
    ) as directory:
        ledger = sqlite3.connect(Path(directory) / "inventory.sqlite3")
        try:
            ledger.execute(
                """
                CREATE TABLE authorized_messages (
                    message_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    snapshot_sha256 TEXT NOT NULL
                )
                """
            )
            for message in messages:
                try:
                    ledger.execute(
                        """
                        INSERT INTO authorized_messages (
                            message_id, channel_id, snapshot_sha256
                        ) VALUES (?, ?, ?)
                        """,
                        (
                            message.message_id,
                            message.channel_id,
                            message.snapshot_sha256,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError(
                        "Discord authorized corpus contains a duplicate message"
                    ) from exc
                if (
                    message.channel_id not in target_by_id
                    and message.channel_id not in family_owner
                ):
                    raise ValueError(
                        "Discord authorized message is outside the target families"
                    )
                exact.setdefault(message.channel_id, _Counts()).add(message)
                owner = family_owner.get(
                    message.channel_id, message.channel_id
                )
                if owner not in target_by_id:
                    raise ValueError(
                        "Discord family owner is outside the explicit targets"
                    )
                family.setdefault(owner, _Counts()).add(message)
            ledger.commit()
            unique_message_count = int(
                ledger.execute(
                    "SELECT COUNT(*) FROM authorized_messages"
                ).fetchone()[0]
            )
            corpus_commitment = _authorized_corpus_commitment(ledger)
        finally:
            ledger.close()

    rows: list[dict[str, object]] = []
    for target in sorted(targets, key=lambda value: int(str(value["id"]))):
        target_id = str(target["id"])
        kind = str(target["kind"])
        if _THREAD_KIND in kind.upper():
            semantics = "exact_thread"
            counts = exact.get(target_id, _Counts())
        elif target_id in family_parents:
            semantics = "family_rollup"
            counts = family.get(target_id, _Counts())
        else:
            semantics = "exact_channel"
            counts = exact.get(target_id, _Counts())
        count_values = counts.output()
        is_exact_thread = _THREAD_KIND in kind.upper()
        private_scope_blocked = not is_exact_thread and target_id in private_blockers
        rows.append(
            {
                "target_id": target_id,
                "name": target["name"],
                "kind": kind,
                "parent_id": target.get("parent_id"),
                "source_labels": list(target.get("source_labels", [])),
                "count_semantics": semantics,
                "evidence_status": (
                    "verified_messages"
                    if count_values["message_count"]
                    else "verified_no_messages"
                ),
                "scope_completeness": (
                    "known_scope_only"
                    if private_scope_blocked
                    else "observed_scope"
                ),
                "private_archived_scope_status": (
                    "unjoined_private_archives_not_enumerable"
                    if private_scope_blocked
                    else (
                        "exact_thread_scope_complete"
                        if is_exact_thread
                        else "no_enumeration_blocker"
                    )
                ),
                **count_values,
            }
        )

    inventory = {
        "artifact_kind": "discord-blogger-target-inventory-v1",
        "schema_version": 1,
        "projection_semantics": {
            "authorized_corpus": "unique_message_id_union",
            "explicit_thread": "exact_channel_id",
            "parent_family": (
                "direct_parent_plus_explicit_and_discovered_threads"
            ),
            "per_target_sum_may_overlap": True,
        },
        "coverage_dimensions": {
            "text_messages": "verified_known_scope",
            "media": "not_asserted",
            "private_archived_threads": "per_target",
        },
        "provenance": dict(provenance),
        "target_count": len(rows),
        "unique_authorized_message_count": unique_message_count,
        "authorized_corpus_commitment_sha256": corpus_commitment,
        "per_target_message_sum": sum(int(row["message_count"]) for row in rows),
        "targets_with_verified_messages": sum(
            int(row["message_count"]) > 0 for row in rows
        ),
        "targets_without_verified_messages": sum(
            int(row["message_count"]) == 0 for row in rows
        ),
        "private_archived_blocked_parent_count": len(private_blockers),
        "targets": rows,
    }
    _reject_sensitive(inventory)
    return inventory


def _authorized_corpus_commitment(ledger: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    digest.update(b"[")
    first = True
    rows = ledger.execute(
        """
        SELECT message_id, channel_id, snapshot_sha256
        FROM authorized_messages
        ORDER BY LENGTH(message_id), message_id
        """
    )
    for message_id, channel_id, snapshot_sha256 in rows:
        if not first:
            digest.update(b",")
        digest.update(
            json.dumps(
                [message_id, channel_id, snapshot_sha256],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        first = False
    digest.update(b"]")
    return digest.hexdigest()


def publish_blogger_target_inventory(
    *,
    workspace: Path,
    output_path: Path,
    inventory: Mapping[str, object],
) -> dict[str, object]:
    """Atomically publish one immutable private JSON inventory."""

    root = Path(workspace).absolute().resolve(strict=True)
    relative = _relative(output_path, "inventory output")
    content = canonical_json_bytes(dict(inventory))
    _reject_sensitive(json.loads(content))
    anchor = _RootAnchor.open(root)
    try:
        anchor.write_exclusive_or_same(relative, content)
        if anchor.read_regular(relative, "blogger inventory") != content:
            raise ValueError("Discord blogger inventory publication changed")
        with anchor.directory(relative.parent, create=False) as parent:
            parent.verify()
            final = os.stat(
                relative.name, dir_fd=parent.fd, follow_symlinks=False
            )
            if (
                not stat.S_ISREG(final.st_mode)
                or stat.S_IMODE(final.st_mode) != _PRIVATE_FILE_MODE
                or final.st_size != len(content)
            ):
                raise ValueError("Discord blogger inventory publication changed")
            parent.verify()
    finally:
        anchor.close()
    return {
        "output_path": relative.as_posix(),
        "output_sha256": hashlib.sha256(content).hexdigest(),
        "target_count": inventory.get("target_count"),
        "unique_authorized_message_count": inventory.get(
            "unique_authorized_message_count"
        ),
    }


def _targets(snapshot: Mapping[str, object]) -> list[Mapping[str, object]]:
    guild_id = snapshot.get("guild_id")
    values = snapshot.get("targets")
    if not isinstance(guild_id, str) or not guild_id.isdigit():
        raise ValueError("Discord target snapshot guild_id is invalid")
    if not isinstance(values, list) or not values:
        raise ValueError("Discord target snapshot targets are invalid")
    target_ids: list[str] = []
    output: list[Mapping[str, object]] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError("Discord target snapshot target is invalid")
        target_id = value.get("id")
        name = value.get("name")
        kind = value.get("kind")
        parent_id = value.get("parent_id")
        labels = value.get("source_labels", [])
        if (
            not isinstance(target_id, str)
            or not target_id.isdigit()
            or not isinstance(name, str)
            or not name.strip()
            or not isinstance(kind, str)
            or not kind.strip()
            or (parent_id is not None and (not isinstance(parent_id, str) or not parent_id.isdigit()))
            or not isinstance(labels, list)
            or any(not isinstance(label, str) or not label.strip() for label in labels)
        ):
            raise ValueError("Discord target snapshot target is invalid")
        target_ids.append(target_id)
        output.append(value)
    if (
        snapshot.get("target_count") != len(output)
        or len(target_ids) != len(set(target_ids))
        or snapshot.get("target_set_sha256") != target_set_sha256(target_ids)
    ):
        raise ValueError("Discord target snapshot commitment is invalid")
    return output


def _family_owner(
    discovered_threads: Sequence[Mapping[str, object]],
    target_by_id: Mapping[str, Mapping[str, object]],
    family_parents: set[str],
) -> dict[str, str]:
    output = {
        target_id: str(target["parent_id"])
        for target_id, target in target_by_id.items()
        if _THREAD_KIND in str(target["kind"]).upper()
        and target.get("parent_id") in family_parents
    }
    seen_discovered: set[str] = set()
    for thread in discovered_threads:
        if not isinstance(thread, Mapping):
            raise ValueError("Discord discovered Thread is invalid")
        thread_id = thread.get("id")
        parent_id = thread.get("parent_id")
        owner_index = thread.get("owner_index")
        if (
            not isinstance(thread_id, str)
            or not thread_id.isdigit()
            or not isinstance(parent_id, str)
            or not parent_id.isdigit()
            or parent_id not in family_parents
            or isinstance(owner_index, bool)
            or not isinstance(owner_index, int)
            or owner_index <= 0
            or thread_id == parent_id
            or thread_id in seen_discovered
        ):
            raise ValueError("Discord discovered Thread mapping is invalid")
        seen_discovered.add(thread_id)
        if _THREAD_KIND in str(target_by_id[parent_id]["kind"]).upper():
            raise ValueError("Discord discovered Thread parent cannot be a Thread")
        explicit = target_by_id.get(thread_id)
        if explicit is not None:
            if _THREAD_KIND not in str(explicit["kind"]).upper():
                raise ValueError("Discord discovered explicit Thread kind is invalid")
            if explicit.get("parent_id") != parent_id:
                raise ValueError("Discord explicit Thread parent differs from discovery")
        known_parent = output.get(thread_id)
        if known_parent is not None and known_parent != parent_id:
            raise ValueError("Discord explicit Thread parent differs from discovery")
        output[thread_id] = parent_id
    return output


def _family_parents(
    values: Sequence[str],
    target_by_id: Mapping[str, Mapping[str, object]],
) -> set[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("Discord family parent targets are invalid")
    output = set(values)
    forum_ids = {
        target_id
        for target_id, target in target_by_id.items()
        if _FORUM_KIND in str(target["kind"]).upper()
    }
    if (
        len(output) != len(values)
        or forum_ids - output
        or any(
            not isinstance(value, str)
            or not value.isdigit()
            or value not in target_by_id
            or _THREAD_KIND in str(target_by_id[value]["kind"]).upper()
            for value in values
        )
    ):
        raise ValueError("Discord family parent targets are invalid")
    return output


def _private_blockers(
    values: Sequence[str],
    target_by_id: Mapping[str, Mapping[str, object]],
    family_parents: set[str],
) -> set[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("Discord private archived blocker parents are invalid")
    output = set(values)
    if (
        len(output) != len(values)
        or any(
            not isinstance(value, str)
            or not value.isdigit()
            or value not in target_by_id
            or value not in family_parents
            or _THREAD_KIND in str(target_by_id[value]["kind"]).upper()
            for value in values
        )
    ):
        raise ValueError("Discord private archived blocker parents are invalid")
    return output


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Discord message timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Discord message timestamp must be timezone-aware")
    return parsed


def _relative(value: Path, label: str) -> Path:
    relative = Path(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"Discord {label} path must be a contained relative path")
    return relative


def _reject_sensitive(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in _FORBIDDEN_OUTPUT_KEYS:
                raise ValueError("Discord blogger inventory contains a sensitive field")
            _reject_sensitive(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_sensitive(child)
    elif isinstance(value, str) and (
        value.lower().startswith("http://") or value.lower().startswith("https://")
    ):
        raise ValueError("Discord blogger inventory contains a raw URL")
