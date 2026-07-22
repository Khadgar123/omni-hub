"""Immutable, resumable Discord evidence collection orchestration."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path, PurePath
import re
import sqlite3
import stat
import tempfile
from typing import Any, Protocol
from urllib.parse import urlsplit

from .connectors.discord import (
    DiscordAPIError,
    DiscordJSONTransport,
    DiscordMediaResolutionError,
    DiscordMediaResolutionInvalidAnswer,
    DiscordMediaSecurityError,
    DiscordPage,
    iter_joined_private_archived_thread_pages,
    iter_message_pages,
    iter_pin_pages,
    iter_private_archived_thread_pages,
    iter_public_archived_thread_pages,
    rfc2544_fake_ip_media_policy_descriptor,
)
from .discord_media_recovery import (
    FRESH_SECURITY_REJECTION_PROVENANCE,
    MAX_RESOLUTION_RETRY_SEQUENCES,
    RESOLUTION_RETRY_TRIGGER,
    TRANSIENT_RESOLUTION_DETAILS,
    discord_declared_size_mismatch,
    discord_media_candidate_observation_metadata,
    discord_media_field_descriptor,
    discord_media_identity_metadata,
    discord_media_metadata_without_urls,
    discord_media_mime_outcome,
    discord_media_reference_candidate_ledger_is_exact,
    discord_media_reference_source_observation,
    has_resolution_attempt_history,
    is_legacy_zero_complete_candidate,
    is_discord_external_proxy_url as _recovery_is_discord_external_proxy_url,
    media_resolution_context,
    migrate_legacy_media_record,
    next_resolution_retry_metadata,
    normalized_discord_media_mime,
    reusable_resolution_attempt_number,
    validate_resolution_attempt_history,
)
from .discord_media_audit import (
    MEDIA_RECOVERY_AUDIT_FILENAME,
    MEDIA_RECOVERY_AUDIT_VERSION,
    build_media_recovery_audit,
    canonical_media_recovery_audit_bytes,
)
from .discord_message_evidence import (
    MediaOccurrence,
    MessageEvidence,
    extract_message_evidence,
)
from .discord_reference_sidecar import (
    build_message_reference_resolution_audit,
    publish_message_reference_resolution_audit,
)


_SNOWFLAKE = re.compile(r"^[0-9]+$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_THREAD_TYPES = frozenset({10, 11, 12})
_THREAD_PARENT_TYPES = frozenset({0, 5, 15, 16})
_FORUM_OR_MEDIA_TYPES = frozenset({15, 16})
_MESSAGE_BEARING_TYPES = frozenset({0, 2, 5, 10, 11, 12, 13})
_KNOWN_GUILD_CHANNEL_TYPES = frozenset({0, 2, 4, 5, 6, 10, 11, 12, 13, 14, 15, 16})
_COMPLETE_STREAM_STATUS = "complete"
_NON_API_EXPOSED = ["discord_go_live", "personal_favorites"]
_ASSET_RECORD_SCHEMA_VERSION = 4
_LEGACY_ASSET_RECORD_SCHEMA_VERSION = 3
_ASSET_LEDGER_SCHEMA_VERSION = 1
_ASSET_LEDGER_MARKER = {"backend": "sqlite", "version": 1}
_ASSET_LEDGER_FILENAME = "asset-ledger.sqlite3"
_MESSAGE_EVIDENCE_SCHEMA_VERSION = 2
_REQUEST_SCHEMA_VERSION = 2
_REQUEST_AMENDMENT_FILENAME = "request-v2-amendment.json"
_LEGACY_MAX_ASSET_BYTES = 512 * 1024 * 1024
_YOUTUBE_EMBED_PLAYER_REFERENCE_REASON = "youtube_embed_player_reference"
_YOUTUBE_EMBED_PLAYER_REFERENCE_RULE = (
    "youtube_embed_player_url_rejected_by_media_policy_v1"
)
_YOUTUBE_EMBED_PLAYER_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }
)
_YOUTUBE_EMBED_PLAYER_PATH = re.compile(r"^/embed/[A-Za-z0-9_-]+$")
_RETRYABLE_ASSET_REASONS = frozenset(
    {
        "byte_transport_unavailable",
        "content_length_mismatch",
        "download_failed",
        "download_failed_transient",
        "interrupted",
    }
)
_HARD_ASSET_FAILURE_REASONS = frozenset(
    {"logical_identity_conflict", "size_limit_exceeded"}
)
_COVERED_ASSET_STATUSES = frozenset(
    {"complete", "captured_with_warning", "reference_only"}
)
_E754_ASSET_KEYS = frozenset(
    {
        "logical_key",
        "kind",
        "field",
        "url",
        "declared_metadata",
        "declared_content_type",
        "sources",
        "observed_urls",
        "attempt_history",
        "status",
        "terminal_reason",
        "http_content_type",
        "http_content_length",
        "actual_bytes",
        "sha256",
        "blob_path",
    }
)


class DiscordByteTransport(Protocol):
    """The credential-free seam the collector needs for CDN evidence."""

    allow_rfc2544_fake_ip: bool
    rfc2544_fake_ip_policy: Mapping[str, object] | None

    def open_byte_stream(
        self,
        path_or_url: str,
        params: Mapping[str, object] | None = None,
        *,
        chunk_size: int = 64 * 1024,
    ) -> AbstractContextManager[Iterable[bytes]]: ...


@dataclass(frozen=True, slots=True)
class CollectionResult:
    run_root: Path
    manifest: dict[str, Any]


class _AssetLedger:
    """Small crash-recovery ledger for immutable per-asset records."""

    def __init__(self, run_root: Path, *, create_if_missing: bool) -> None:
        self.path = run_root / _ASSET_LEDGER_FILENAME
        self._sidecars = tuple(
            Path(str(self.path) + suffix) for suffix in ("-wal", "-shm")
        )
        self._connection: sqlite3.Connection | None = None
        self._path_guard: int | None = None
        self._validate_paths()
        guard_flags = os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            guard_flags |= os.O_NOFOLLOW
        created = False
        try:
            guard = os.open(self.path, guard_flags)
        except FileNotFoundError:
            if not create_if_missing:
                raise ValueError("Discord asset ledger database is missing") from None
            guard = os.open(
                self.path,
                guard_flags | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            created = True
        guard_status = os.fstat(guard)
        if not stat.S_ISREG(guard_status.st_mode):
            os.close(guard)
            raise ValueError("Discord asset ledger path must be a regular file")
        self._path_guard = guard
        connection: sqlite3.Connection | None = None
        try:
            if created:
                os.fsync(guard)
                _fsync_directory(self.path.parent)
            connection = sqlite3.connect(self.path, timeout=30)
            self._assert_guard_identity("changed during SQLite open")
            connection.row_factory = sqlite3.Row
            self._connection = connection
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            if mode is None or str(mode[0]).lower() != "wal":
                raise ValueError("Discord asset ledger could not enable WAL mode")
            connection.execute("PRAGMA synchronous = FULL")
            self._initialize_schema()
            self._validate_paths(require_database=True)
        except BaseException:
            if connection is not None:
                connection.close()
            self._connection = None
            os.close(guard)
            self._path_guard = None
            raise

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Discord asset ledger is closed")
        return self._connection

    def _validate_paths(self, *, require_database: bool = False) -> None:
        for path in (self.path, *self._sidecars):
            try:
                status = os.lstat(path)
            except FileNotFoundError:
                if require_database and path == self.path:
                    raise ValueError("Discord asset ledger database is missing") from None
                continue
            if stat.S_ISLNK(status.st_mode):
                raise ValueError("Discord asset ledger path must not be a symbolic link")
            if not stat.S_ISREG(status.st_mode):
                raise ValueError("Discord asset ledger path must be a regular file")

    def _assert_guard_identity(self, reason: str) -> None:
        guard = self._path_guard
        if guard is None:
            raise RuntimeError("Discord asset ledger path guard is closed")
        guarded = os.fstat(guard)
        try:
            current = os.lstat(self.path)
        except FileNotFoundError:
            raise ValueError(f"Discord asset ledger path {reason}") from None
        if (
            not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != (guarded.st_dev, guarded.st_ino)
        ):
            raise ValueError(f"Discord asset ledger path {reason}")

    def _initialize_schema(self) -> None:
        connection = self.connection
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in {0, _ASSET_LEDGER_SCHEMA_VERSION}:
            raise ValueError("Discord asset ledger schema version is unsupported")
        if version == 0:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    CREATE TABLE asset_records (
                        logical_key TEXT PRIMARY KEY NOT NULL,
                        record_name TEXT NOT NULL UNIQUE,
                        committed_sha256 TEXT,
                        pending_sha256 TEXT,
                        CHECK (
                            committed_sha256 IS NULL OR (
                                length(committed_sha256) = 64
                                AND committed_sha256 NOT GLOB '*[^0-9a-f]*'
                            )
                        ),
                        CHECK (
                            pending_sha256 IS NULL OR (
                                length(pending_sha256) = 64
                                AND pending_sha256 NOT GLOB '*[^0-9a-f]*'
                            )
                        )
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE asset_metadata (
                        key TEXT PRIMARY KEY NOT NULL,
                        value TEXT NOT NULL
                    )
                    """
                )
                connection.executemany(
                    "INSERT INTO asset_metadata(key, value) VALUES (?, ?)",
                    (
                        ("records_generation", "0"),
                        ("index_generation", "-1"),
                        ("asset_index_sha256", ""),
                    ),
                )
                connection.execute(
                    f"PRAGMA user_version = {_ASSET_LEDGER_SCHEMA_VERSION}"
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        self._validate_schema()

    def _validate_schema(self) -> None:
        expected = {
            "asset_records": (
                ("logical_key", "TEXT", 1, 1),
                ("record_name", "TEXT", 1, 0),
                ("committed_sha256", "TEXT", 0, 0),
                ("pending_sha256", "TEXT", 0, 0),
            ),
            "asset_metadata": (
                ("key", "TEXT", 1, 1),
                ("value", "TEXT", 1, 0),
            ),
        }
        for table, wanted in expected.items():
            rows = self.connection.execute(f"PRAGMA table_info({table})").fetchall()
            actual = tuple(
                (str(row[1]), str(row[2]).upper(), int(row[3]), int(row[5]))
                for row in rows
            )
            if actual != wanted:
                raise ValueError("Discord asset ledger schema is invalid")
        records_generation = self._metadata_int("records_generation")
        index_generation = self._metadata_int("index_generation")
        if (
            records_generation < 0
            or index_generation < -1
            or index_generation > records_generation
        ):
            raise ValueError("Discord asset ledger generation is invalid")
        index_digest = self._metadata("asset_index_sha256")
        if index_digest and not _valid_sha256(index_digest):
            raise ValueError("Discord asset index hash is invalid")

    def _metadata(self, key: str) -> str:
        row = self.connection.execute(
            "SELECT value FROM asset_metadata WHERE key = ?", (key,)
        ).fetchone()
        if row is None or not isinstance(row[0], str):
            raise ValueError("Discord asset ledger metadata is invalid")
        return row[0]

    def _metadata_int(self, key: str) -> int:
        try:
            return int(self._metadata(key))
        except ValueError:
            raise ValueError("Discord asset ledger generation is invalid") from None

    def _increment_generation(self, amount: int = 1) -> None:
        self.connection.execute(
            "UPDATE asset_metadata SET value = CAST(value AS INTEGER) + ? "
            "WHERE key = 'records_generation'",
            (amount,),
        )

    def import_legacy(self, legacy: Mapping[str, Any]) -> None:
        source_digest = _sha256_bytes(_canonical_json_bytes(legacy, newline=False))
        marker_row = self.connection.execute(
            "SELECT value FROM asset_metadata WHERE key = 'legacy_migration_sha256'"
        ).fetchone()
        if marker_row is not None:
            if marker_row[0] != source_digest:
                raise ValueError("Discord legacy asset ledger changed during migration")
            return

        rows: list[tuple[str, str, str | None, str | None]] = []
        for logical_key, entry in legacy.items():
            if not isinstance(logical_key, str) or not isinstance(entry, dict):
                raise ValueError("Discord checkpoint asset identity is invalid")
            expected_name = (
                hashlib.sha256(logical_key.encode("utf-8")).hexdigest() + ".json"
            )
            if entry.get("record_name") != expected_name:
                raise ValueError(
                    f"Discord checkpoint asset filename mismatch: {logical_key}"
                )
            committed = entry.get("committed_sha256")
            pending = entry.get("pending_sha256")
            if committed is not None and not _valid_sha256(committed):
                raise ValueError(
                    f"Discord checkpoint asset hash is invalid: {logical_key}"
                )
            if pending is not None and not _valid_sha256(pending):
                raise ValueError(
                    f"Discord checkpoint asset hash is invalid: {logical_key}"
                )
            if committed is None and pending is None:
                raise ValueError(
                    f"Discord checkpoint asset has no durable state: {logical_key}"
                )
            rows.append((logical_key, expected_name, committed, pending))

        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            inserted = 0
            for logical_key, record_name, committed, pending in rows:
                existing = connection.execute(
                    "SELECT record_name, committed_sha256, pending_sha256 "
                    "FROM asset_records WHERE logical_key = ?",
                    (logical_key,),
                ).fetchone()
                values = (record_name, committed, pending)
                if existing is None:
                    connection.execute(
                        "INSERT INTO asset_records "
                        "(logical_key, record_name, committed_sha256, pending_sha256) "
                        "VALUES (?, ?, ?, ?)",
                        (logical_key, *values),
                    )
                    inserted += committed is not None
                elif tuple(existing) != values:
                    raise ValueError(
                        f"Discord legacy asset ledger conflicts with SQLite: {logical_key}"
                    )
            if inserted:
                self._increment_generation(inserted)
            connection.execute(
                "INSERT INTO asset_metadata(key, value) VALUES "
                "('legacy_migration_sha256', ?)",
                (source_digest,),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def entries(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT logical_key, record_name, committed_sha256, pending_sha256 "
            "FROM asset_records ORDER BY logical_key"
        ).fetchall()

    def has_pending(self) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM asset_records WHERE pending_sha256 IS NOT NULL LIMIT 1"
            ).fetchone()
            is not None
        )

    def reconcile(self, entry: sqlite3.Row, path: Path) -> bool:
        logical_key = str(entry["logical_key"])
        committed = entry["committed_sha256"]
        pending = entry["pending_sha256"]
        if path.is_symlink() or not path.is_file():
            if pending is not None and committed is None and not path.exists():
                with self.connection:
                    self.connection.execute(
                        "DELETE FROM asset_records WHERE logical_key = ?", (logical_key,)
                    )
                return False
            raise ValueError(f"Discord asset record is missing: {path.name}")
        actual = _sha256_file(path)
        if pending is not None and actual == pending:
            with self.connection:
                self.connection.execute(
                    "UPDATE asset_records SET committed_sha256 = ?, pending_sha256 = NULL "
                    "WHERE logical_key = ?",
                    (pending, logical_key),
                )
                if committed != pending:
                    self._increment_generation()
            return True
        if pending is not None and actual == committed:
            with self.connection:
                self.connection.execute(
                    "UPDATE asset_records SET pending_sha256 = NULL WHERE logical_key = ?",
                    (logical_key,),
                )
            return True
        if actual != committed:
            raise ValueError(f"Discord asset record hash mismatch: {path.name}")
        return True

    def register_existing(self, logical_key: str, record_name: str, digest: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO asset_records "
                "(logical_key, record_name, committed_sha256, pending_sha256) "
                "VALUES (?, ?, ?, NULL)",
                (logical_key, record_name, digest),
            )
            self._increment_generation()

    def prepare_commit(self, logical_key: str, record_name: str, digest: str) -> bool:
        connection = self.connection
        with connection:
            row = connection.execute(
                "SELECT record_name, committed_sha256, pending_sha256 "
                "FROM asset_records WHERE logical_key = ?",
                (logical_key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO asset_records "
                    "(logical_key, record_name, committed_sha256, pending_sha256) "
                    "VALUES (?, ?, NULL, ?)",
                    (logical_key, record_name, digest),
                )
                return True
            if row["record_name"] != record_name:
                raise ValueError(f"Discord asset record filename changed: {logical_key}")
            if row["pending_sha256"] is not None:
                raise ValueError(
                    f"Discord asset record has unreconciled pending state: {logical_key}"
                )
            if row["committed_sha256"] == digest:
                return False
            connection.execute(
                "UPDATE asset_records SET pending_sha256 = ? WHERE logical_key = ?",
                (digest, logical_key),
            )
            return True

    def finish_commit(self, logical_key: str, digest: str) -> None:
        connection = self.connection
        with connection:
            row = connection.execute(
                "SELECT committed_sha256, pending_sha256 FROM asset_records "
                "WHERE logical_key = ?",
                (logical_key,),
            ).fetchone()
            if row is None or row["pending_sha256"] != digest:
                raise ValueError(
                    f"Discord asset record pending state changed: {logical_key}"
                )
            connection.execute(
                "UPDATE asset_records SET committed_sha256 = ?, pending_sha256 = NULL "
                "WHERE logical_key = ?",
                (digest, logical_key),
            )
            if row["committed_sha256"] != digest:
                self._increment_generation()

    def index_needs_write(self, _path: Path) -> bool:
        return self._metadata_int("index_generation") != self._metadata_int(
            "records_generation"
        )

    def bound_index_sha256(self, path: Path) -> str:
        expected = self._metadata("asset_index_sha256")
        if not _valid_sha256(expected):
            raise ValueError("Discord asset index hash is missing or invalid")
        content = _read_regular_bytes(path, label="Discord asset index")
        if _sha256_bytes(content) != expected:
            raise ValueError("Discord asset index hash mismatch")
        return expected

    def index_snapshot(self) -> tuple[int, list[sqlite3.Row]]:
        connection = self.connection
        connection.execute("BEGIN")
        try:
            generation = self._metadata_int("records_generation")
            rows = connection.execute(
                "SELECT logical_key, record_name, committed_sha256, pending_sha256 "
                "FROM asset_records ORDER BY logical_key"
            ).fetchall()
        finally:
            connection.rollback()
        return generation, rows

    def mark_index(
        self,
        digest: str,
        *,
        expected_generation: int,
    ) -> bool:
        if not _valid_sha256(digest):
            raise ValueError("Discord asset index hash is invalid")
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            generation = self._metadata_int("records_generation")
            if generation != expected_generation:
                connection.rollback()
                return False
            self.connection.execute(
                "UPDATE asset_metadata SET value = ? WHERE key = 'index_generation'",
                (str(generation),),
            )
            self.connection.execute(
                "UPDATE asset_metadata SET value = ? WHERE key = 'asset_index_sha256'",
                (digest,),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return True

    def checkpoint(self) -> None:
        self._validate_paths(require_database=True)
        self._assert_guard_identity("changed while open")
        self.connection.commit()
        result = self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if result is None or int(result[0]) != 0:
            raise RuntimeError("Discord asset ledger WAL checkpoint did not complete")
        self._assert_guard_identity("changed while open")

    def close(self) -> None:
        connection = self._connection
        if connection is None:
            return
        try:
            self.checkpoint()
        finally:
            connection.close()
            self._connection = None
            guard = self._path_guard
            if guard is not None:
                os.close(guard)
                self._path_guard = None


def validate_target_snapshot(payload: object) -> dict[str, Any]:
    """Validate target identity fields while retaining all audit metadata."""

    if not isinstance(payload, dict):
        raise ValueError("Discord target snapshot must be a JSON object")
    guild_id = payload.get("guild_id")
    if not _valid_snowflake(guild_id):
        raise ValueError("Discord target snapshot guild_id must be a snowflake string")
    targets = payload.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("Discord target snapshot targets must be a non-empty list")

    seen_ids: set[str] = set()
    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            raise ValueError(f"Discord target at index {index} must be an object")
        target_id = target.get("id")
        if not _valid_snowflake(target_id):
            raise ValueError(f"Discord target at index {index} has an invalid id")
        if target_id in seen_ids:
            raise ValueError(f"Discord target id is duplicated: {target_id}")
        seen_ids.add(target_id)
        for field in ("kind", "name"):
            value = target.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Discord target {target_id} field {field} must be a non-empty string"
                )
        parent_id = target.get("parent_id")
        if parent_id is not None and not _valid_snowflake(parent_id):
            raise ValueError(f"Discord target {target_id} has an invalid parent_id")
        source_labels = target.get("source_labels")
        if source_labels is not None and (
            not isinstance(source_labels, list)
            or any(not isinstance(label, str) or not label.strip() for label in source_labels)
        ):
            raise ValueError(
                f"Discord target {target_id} source_labels must contain non-empty strings"
            )
    return deepcopy(payload)


def resolve_output_root(
    workspace: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
) -> Path:
    """Resolve a relative output directory without permitting symlink escape."""

    workspace_path = Path(workspace)
    if not workspace_path.is_absolute():
        workspace_path = workspace_path.absolute()
    workspace_resolved = workspace_path.resolve(strict=True)
    relative = Path(output_dir)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise ValueError("Discord output directory must be a contained relative path")
    candidate = workspace_resolved.joinpath(relative)
    resolved = candidate.resolve(strict=False)
    if not _is_relative_to(resolved, workspace_resolved):
        raise ValueError("Discord output directory escapes the workspace")
    return candidate


class DiscordEvidenceCollector:
    """Collect API evidence through injected JSON and byte-stream transports."""

    def __init__(
        self,
        json_transport: DiscordJSONTransport,
        *,
        byte_transport: DiscordByteTransport | None = None,
        max_asset_bytes: int = 512 * 1024 * 1024,
        chunk_size: int = 64 * 1024,
        allow_rfc2544_fake_ip: bool = False,
    ) -> None:
        if (
            isinstance(max_asset_bytes, bool)
            or not isinstance(max_asset_bytes, int)
            or max_asset_bytes <= 0
        ):
            raise ValueError("max_asset_bytes must be positive")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if not isinstance(allow_rfc2544_fake_ip, bool):
            raise ValueError("allow_rfc2544_fake_ip must be a boolean")
        if byte_transport is not None:
            expected_policy = (
                rfc2544_fake_ip_media_policy_descriptor()
                if allow_rfc2544_fake_ip
                else None
            )
            missing_descriptor = not all(
                hasattr(byte_transport, name)
                for name in (
                    "allow_rfc2544_fake_ip",
                    "rfc2544_fake_ip_policy",
                )
            )
            transport_allow = getattr(
                byte_transport, "allow_rfc2544_fake_ip", None
            )
            transport_policy = getattr(
                byte_transport, "rfc2544_fake_ip_policy", None
            )
            if (
                missing_descriptor
                or transport_allow is not allow_rfc2544_fake_ip
                or transport_policy != expected_policy
            ):
                raise ValueError(
                    "Discord byte transport policy does not match request policy"
                )
        self._json = json_transport
        self._bytes = byte_transport
        self._max_asset_bytes = max_asset_bytes
        self._chunk_size = chunk_size
        self._allow_rfc2544_fake_ip = allow_rfc2544_fake_ip

    def collect(
        self,
        *,
        workspace: str | os.PathLike[str],
        output_dir: str | os.PathLike[str],
        targets: Mapping[str, Any] | str | os.PathLike[str],
        run_id: str,
        max_pages: int | None = None,
        download_assets: bool = True,
    ) -> CollectionResult:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id) or run_id in {".", ".."}:
            raise ValueError("Discord run_id contains unsafe path characters")
        if max_pages is not None and max_pages <= 0:
            raise ValueError("max_pages must be positive")
        workspace_path = Path(workspace).absolute().resolve(strict=True)
        snapshot = self._load_targets(targets, workspace_path)
        output_root = resolve_output_root(workspace_path, output_dir)
        run_root = output_root / "runs" / run_id
        self._prepare_layout(workspace_path, output_root, run_root)

        self._run_root = run_root
        self._download_assets = download_assets
        self._max_pages = max_pages
        self._checkpoint_path = run_root / "checkpoint.json"
        self._checkpoint = self._load_checkpoint(run_id)
        self._authenticated_bot_payload = self._authenticate_bot_principal()
        bot_principal_id = str(self._authenticated_bot_payload["id"])
        api_origin = _api_origin(self._json)
        request_options: dict[str, Any] = {
            "max_pages": max_pages,
            "download_assets": download_assets,
            "max_asset_bytes": self._max_asset_bytes,
        }
        if self._allow_rfc2544_fake_ip:
            request_options.update(
                {
                    "allow_rfc2544_fake_ip": True,
                    "rfc2544_fake_ip_policy": (
                        rfc2544_fake_ip_media_policy_descriptor()
                    ),
                }
            )
        request_identity = {
            "version": _REQUEST_SCHEMA_VERSION,
            "run_id": run_id,
            "target_snapshot": snapshot,
            "target_sha256": _sha256_bytes(_canonical_json_bytes(snapshot)),
            "identity": {
                "bot_principal_id": bot_principal_id,
                "api_origin": api_origin,
            },
            "options": request_options,
            "schema": {
                "message_evidence_version": _MESSAGE_EVIDENCE_SCHEMA_VERSION,
            },
        }
        self._bind_request_identity(request_identity)
        self._resolution_context = media_resolution_context(
            request_identity,
            self._checkpoint["request_sha256"],
        )
        self._record_request_telemetry()
        self._asset_ledger = _AssetLedger(
            run_root,
            create_if_missing=self._checkpoint.get("asset_ledger") is None,
        )
        self._blob_validation_cache: dict[
            str,
            tuple[Path, int, int, int, int, int],
        ] = {}
        primary_error: BaseException | None = None
        try:
            self._asset_records = self._load_asset_records()
            self._attempted_asset_urls: set[tuple[str, str]] = set()
            self._verify_recorded_pages()

            try:
                self._retry_pending_assets_only()
                self._retry_covered_asset_fallbacks()
                self._collect_all(snapshot)
                self._retry_pending_assets()
            except BaseException as exc:
                try:
                    self._write_derived_outputs(interrupted=True)
                except BaseException as finalization_error:
                    _add_secondary_exception_note(
                        exc,
                        "interrupted finalization failed",
                        finalization_error,
                    )
                raise
            manifest = self._write_derived_outputs(interrupted=False)
            return CollectionResult(run_root=run_root, manifest=manifest)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                self._asset_ledger.close()
            except BaseException as close_error:
                if primary_error is None:
                    raise
                _add_secondary_exception_note(
                    primary_error,
                    "asset ledger close failed",
                    close_error,
                )

    def _authenticate_bot_principal(self) -> dict[str, Any]:
        payload = self._json.get_json("/users/@me", {})
        if not isinstance(payload, dict) or not _valid_snowflake(payload.get("id")):
            raise ValueError("Discord authenticated bot principal is invalid")
        return deepcopy(payload)

    def _bind_request_identity(self, request_identity: dict[str, Any]) -> None:
        request_path = self._run_root / "request.json"
        amendment_path = self._run_root / _REQUEST_AMENDMENT_FILENAME
        migration_marker_path = self._run_root / "request-v2-migration-marker.json"
        try:
            os.lstat(request_path)
        except FileNotFoundError:
            if (
                _path_exists_without_following(amendment_path)
                or _path_exists_without_following(migration_marker_path)
                or self._checkpoint_has_collection_state()
            ):
                raise ValueError("Discord request identity is missing")
            request = {
                **deepcopy(request_identity),
                "telemetry": {"initial_asset_chunk_size": self._chunk_size},
            }
            digest = _write_exclusive_or_same(request_path, request)
            self._checkpoint["request_sha256"] = digest
            self._checkpoint.pop("request_amendment_sha256", None)
            self._save_checkpoint()
            return

        stored, request_content = _read_regular_json_bytes(
            request_path,
            label="Discord request identity",
        )
        if not isinstance(stored, dict):
            raise ValueError("Discord request identity must be a JSON object")
        request_digest = _sha256_bytes(request_content)
        checkpoint_digest = self._checkpoint.get("request_sha256")
        if checkpoint_digest is not None and checkpoint_digest != request_digest:
            raise ValueError("Discord request identity hash mismatch")

        version = stored.get("version")
        if version == _REQUEST_SCHEMA_VERSION:
            if checkpoint_digest is None and self._checkpoint_has_collection_state():
                raise ValueError("Discord request identity hash binding is missing")
            stored_identity = deepcopy(stored)
            telemetry = stored_identity.pop("telemetry", None)
            stored_options = stored_identity.get("options")
            stored_max_asset_bytes = (
                stored_options.get("max_asset_bytes")
                if isinstance(stored_options, dict)
                else None
            )
            if (
                isinstance(stored_max_asset_bytes, bool)
                or not isinstance(stored_max_asset_bytes, int)
                or stored_max_asset_bytes <= 0
            ):
                raise ValueError("Discord request max_asset_bytes is invalid")
            recorded_identity = stored_identity.get("identity")
            if not isinstance(recorded_identity, dict):
                recorded_identity = {}
            recorded_principal = recorded_identity.get("bot_principal_id")
            recorded_origin = recorded_identity.get("api_origin")
            if recorded_principal != request_identity["identity"][
                "bot_principal_id"
            ]:
                raise ValueError("Discord request bot principal changed")
            if recorded_origin != request_identity["identity"]["api_origin"]:
                raise ValueError("Discord request API origin changed")
            if stored_identity != request_identity:
                raise ValueError("Discord request identity content mismatch")
            if (
                not isinstance(telemetry, dict)
                or set(telemetry) != {"initial_asset_chunk_size"}
                or not isinstance(telemetry["initial_asset_chunk_size"], int)
                or isinstance(telemetry["initial_asset_chunk_size"], bool)
                or telemetry["initial_asset_chunk_size"] <= 0
            ):
                raise ValueError("Discord request telemetry is invalid")
            if (
                self._checkpoint.get("request_amendment_sha256") is not None
                or _path_exists_without_following(amendment_path)
                or self._checkpoint.get("request_migration_marker_sha256")
                is not None
                or _path_exists_without_following(migration_marker_path)
            ):
                raise ValueError("Discord v2 request has an unexpected request amendment")
            if checkpoint_digest is None:
                self._checkpoint["request_sha256"] = request_digest
                self._save_checkpoint()
            return

        if version != 1:
            raise ValueError("Discord request identity version is unsupported")
        legacy_chunk_size = self._validate_legacy_request(stored, request_identity)
        self._validate_legacy_bot_principal(request_identity)
        if request_identity["identity"]["api_origin"] != "https://discord.com":
            raise ValueError(
                "Discord legacy API origin is unproven; canonical Discord origin required"
            )
        amendment = {
            "version": _REQUEST_SCHEMA_VERSION,
            "kind": "discord_request_v1_amendment",
            "base_request_sha256": request_digest,
            "identity": deepcopy(request_identity["identity"]),
            "schema": deepcopy(request_identity["schema"]),
            "legacy_origin_status": "legacy_origin_unproven",
            "effective_identity_sha256": _sha256_bytes(
                _canonical_json_bytes(request_identity, newline=False)
            ),
            "telemetry": {"legacy_asset_chunk_size": legacy_chunk_size},
        }
        expected_amendment_digest = _sha256_bytes(
            _canonical_json_bytes(amendment)
        )
        checkpoint_amendment_digest = self._checkpoint.get(
            "request_amendment_sha256"
        )
        amendment_exists = _path_exists_without_following(amendment_path)
        migration_marker_exists = _path_exists_without_following(
            migration_marker_path
        )
        checkpoint_marker_digest = self._checkpoint.get(
            "request_migration_marker_sha256"
        )
        if checkpoint_amendment_digest is not None:
            if not amendment_exists:
                raise ValueError("Discord request amendment is missing")
            stored_amendment, amendment_content = _read_regular_json_bytes(
                amendment_path,
                label="Discord request amendment",
            )
            actual_amendment_digest = _sha256_bytes(amendment_content)
            recorded_identity = (
                stored_amendment.get("identity")
                if isinstance(stored_amendment, dict)
                else None
            )
            if isinstance(recorded_identity, dict):
                if recorded_identity.get("bot_principal_id") != request_identity[
                    "identity"
                ]["bot_principal_id"]:
                    raise ValueError("Discord request bot principal changed")
                if recorded_identity.get("api_origin") != request_identity[
                    "identity"
                ]["api_origin"]:
                    raise ValueError("Discord request API origin changed")
            if (
                checkpoint_amendment_digest != actual_amendment_digest
                or actual_amendment_digest != expected_amendment_digest
                or stored_amendment != amendment
            ):
                raise ValueError("Discord request amendment hash or content mismatch")
        else:
            migration_trace_exists = bool(
                checkpoint_digest is not None
                or checkpoint_marker_digest is not None
                or self._checkpoint.get("request_telemetry")
            )
            if not amendment_exists and (
                migration_trace_exists or migration_marker_exists
            ):
                raise ValueError("Discord request amendment is missing")
            if amendment_exists and migration_trace_exists:
                raise ValueError(
                    "Discord request amendment checkpoint binding is missing"
                )
            try:
                actual_amendment_digest = _write_exclusive_or_same(
                    amendment_path,
                    amendment,
                )
            except ValueError as exc:
                raise ValueError("Discord request amendment content mismatch") from exc
            if actual_amendment_digest != expected_amendment_digest:
                raise ValueError("Discord request amendment hash mismatch")

        migration_marker = {
            "version": 1,
            "kind": "discord_request_v1_migration_marker",
            "base_request_sha256": request_digest,
            "request_amendment_sha256": actual_amendment_digest,
        }
        expected_marker_digest = _sha256_bytes(
            _canonical_json_bytes(migration_marker)
        )
        if checkpoint_marker_digest is not None:
            if not migration_marker_exists:
                raise ValueError("Discord request migration marker is missing")
            stored_marker, marker_content = _read_regular_json_bytes(
                migration_marker_path,
                label="Discord request migration marker",
            )
            actual_marker_digest = _sha256_bytes(marker_content)
            if (
                checkpoint_marker_digest != actual_marker_digest
                or actual_marker_digest != expected_marker_digest
                or stored_marker != migration_marker
            ):
                raise ValueError(
                    "Discord request migration marker hash or content mismatch"
                )
        elif migration_marker_exists:
            stored_marker, marker_content = _read_regular_json_bytes(
                migration_marker_path,
                label="Discord request migration marker",
            )
            actual_marker_digest = _sha256_bytes(marker_content)
            if (
                checkpoint_amendment_digest is not None
                or actual_marker_digest != expected_marker_digest
                or stored_marker != migration_marker
            ):
                raise ValueError(
                    "Discord request migration marker checkpoint binding is missing"
                )
        else:
            if checkpoint_amendment_digest is not None:
                raise ValueError("Discord request migration marker is missing")
            actual_marker_digest = _write_exclusive_or_same(
                migration_marker_path,
                migration_marker,
            )
            if actual_marker_digest != expected_marker_digest:
                raise ValueError("Discord request migration marker hash mismatch")

        self._checkpoint["request_amendment_sha256"] = actual_amendment_digest
        self._checkpoint["request_migration_marker_sha256"] = actual_marker_digest
        if checkpoint_digest is None:
            self._checkpoint["request_sha256"] = request_digest
        self._save_checkpoint()

    def _validate_legacy_bot_principal(
        self,
        request_identity: Mapping[str, Any],
    ) -> None:
        streams = self._checkpoint.get("streams")
        if not isinstance(streams, dict):
            raise ValueError("Discord legacy checkpoint streams are invalid")
        state = streams.get("inventory_bot")
        if state is None:
            if (
                streams
                or self._checkpoint.get("asset_ledger")
                or self._checkpoint.get("assets")
                or self._checkpoint.get("errors")
            ):
                raise ValueError(
                    "Discord legacy bot principal evidence is missing"
                )
            return
        if (
            not isinstance(state, dict)
            or state.get("status") != "complete"
            or state.get("evidence_path") != "inventory/bot.json"
            or not _valid_sha256(state.get("evidence_sha256"))
        ):
            raise ValueError("Discord legacy bot principal evidence is invalid")
        bot_path = self._run_root / "inventory" / "bot.json"
        payload, content = _read_regular_json_bytes(
            bot_path,
            label="Discord legacy bot principal evidence",
        )
        if _sha256_bytes(content) != state["evidence_sha256"]:
            raise ValueError("Discord legacy bot principal evidence hash mismatch")
        recorded_principal = payload.get("id") if isinstance(payload, dict) else None
        if not _valid_snowflake(recorded_principal):
            raise ValueError("Discord legacy bot principal evidence is invalid")
        if recorded_principal != request_identity["identity"]["bot_principal_id"]:
            raise ValueError("Discord legacy bot principal changed")

    def _record_request_telemetry(self) -> None:
        telemetry = self._checkpoint.setdefault("request_telemetry", {})
        if not isinstance(telemetry, dict):
            raise ValueError("Discord request telemetry checkpoint is invalid")
        observed = telemetry.setdefault("asset_chunk_sizes_observed", [])
        if (
            not isinstance(observed, list)
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                for value in observed
            )
        ):
            raise ValueError("Discord request telemetry checkpoint is invalid")
        if self._chunk_size not in observed:
            observed.append(self._chunk_size)
            observed.sort()
            self._save_checkpoint()

    def _checkpoint_has_collection_state(self) -> bool:
        return bool(
            self._checkpoint.get("request_sha256")
            or self._checkpoint.get("request_amendment_sha256")
            or self._checkpoint.get("request_migration_marker_sha256")
            or self._checkpoint.get("request_telemetry")
            or self._checkpoint.get("asset_ledger")
            or self._checkpoint.get("streams")
            or self._checkpoint.get("assets")
            or self._checkpoint.get("errors")
        )

    def _validate_legacy_request(
        self,
        stored: dict[str, Any],
        request_identity: dict[str, Any],
    ) -> int | None:
        if set(stored) != {
            "version",
            "run_id",
            "target_snapshot",
            "target_sha256",
            "options",
        }:
            raise ValueError("Discord legacy request identity fields are invalid")
        for key in ("run_id", "target_snapshot", "target_sha256"):
            if stored.get(key) != request_identity[key]:
                raise ValueError("Discord legacy request identity content mismatch")
        options = stored.get("options")
        if not isinstance(options, dict) or not set(options).issubset(
            {
                "max_pages",
                "download_assets",
                "max_asset_bytes",
                "asset_chunk_size",
                "message_evidence_schema_version",
            }
        ):
            raise ValueError("Discord legacy request options are invalid")
        if set(options) < {"max_pages", "download_assets"}:
            raise ValueError("Discord legacy request options are incomplete")
        expected_options = request_identity["options"]
        if (
            options.get("max_pages") != expected_options["max_pages"]
            or options.get("download_assets")
            != expected_options["download_assets"]
        ):
            raise ValueError("Discord legacy request identity content mismatch")
        legacy_max_asset_bytes = options.get(
            "max_asset_bytes",
            _LEGACY_MAX_ASSET_BYTES,
        )
        if (
            isinstance(legacy_max_asset_bytes, bool)
            or not isinstance(legacy_max_asset_bytes, int)
            or legacy_max_asset_bytes <= 0
        ):
            raise ValueError("Discord legacy request max_asset_bytes is invalid")
        if legacy_max_asset_bytes != expected_options["max_asset_bytes"]:
            raise ValueError("Discord legacy request identity content mismatch")
        if expected_options.get("allow_rfc2544_fake_ip") is True:
            raise ValueError("Discord legacy request identity content mismatch")
        legacy_evidence_version = options.get(
            "message_evidence_schema_version",
            _MESSAGE_EVIDENCE_SCHEMA_VERSION,
        )
        if legacy_evidence_version != request_identity["schema"][
            "message_evidence_version"
        ]:
            raise ValueError("Discord legacy request schema is incompatible")
        chunk_size = options.get("asset_chunk_size")
        if chunk_size is not None and (
            not isinstance(chunk_size, int)
            or isinstance(chunk_size, bool)
            or chunk_size <= 0
        ):
            raise ValueError("Discord legacy request chunk telemetry is invalid")
        return chunk_size

    def _load_targets(
        self,
        targets: Mapping[str, Any] | str | os.PathLike[str],
        workspace: Path,
    ) -> dict[str, Any]:
        if isinstance(targets, Mapping):
            return validate_target_snapshot(dict(targets))
        path = Path(targets)
        if not path.is_absolute():
            path = workspace / path
        if path.is_symlink():
            raise ValueError("Discord target snapshot path must not be a symlink")
        resolved = path.resolve(strict=True)
        if not _is_relative_to(resolved, workspace):
            raise ValueError("Discord target snapshot path escapes the workspace")
        if not resolved.is_file():
            raise ValueError("Discord target snapshot path must be a regular file")
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Discord target snapshot is not readable JSON") from exc
        return validate_target_snapshot(payload)

    def _prepare_layout(self, workspace: Path, output_root: Path, run_root: Path) -> None:
        for path in (
            output_root,
            output_root / "runs",
            run_root,
            run_root / "inventory",
            run_root / "pages",
            run_root / "message-evidence",
            run_root / "assets",
            run_root / "assets" / "sha256",
            run_root / "asset-records",
        ):
            _safe_mkdir(path, workspace)

    def _load_checkpoint(self, run_id: str) -> dict[str, Any]:
        if self._checkpoint_path.exists():
            payload = _read_json(self._checkpoint_path)
            if not isinstance(payload, dict) or payload.get("run_id") != run_id:
                raise ValueError("Discord checkpoint does not match the requested run")
            if not isinstance(payload.get("streams"), dict):
                raise ValueError("Discord checkpoint streams are invalid")
            if not isinstance(payload.get("errors"), list):
                raise ValueError("Discord checkpoint errors are invalid")
            if not isinstance(payload.setdefault("assets", {}), dict):
                raise ValueError("Discord checkpoint asset ledger is invalid")
            marker = payload.get("asset_ledger")
            if marker is not None and marker != _ASSET_LEDGER_MARKER:
                raise ValueError("Discord checkpoint asset ledger backend is invalid")
            return payload
        checkpoint = {
            "version": 1,
            "run_id": run_id,
            "streams": {},
            "assets": {},
            "errors": [],
        }
        _atomic_write_json(self._checkpoint_path, checkpoint)
        return checkpoint

    def _load_asset_records(self) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        legacy = self._checkpoint.setdefault("assets", {})
        if legacy:
            self._asset_ledger.import_legacy(legacy)
        referenced_names: set[str] = set()
        for entry in self._asset_ledger.entries():
            logical_key = entry["logical_key"]
            if not isinstance(logical_key, str):
                raise ValueError("Discord asset ledger identity is invalid")
            expected_name = hashlib.sha256(logical_key.encode("utf-8")).hexdigest() + ".json"
            if entry["record_name"] != expected_name:
                raise ValueError(f"Discord asset ledger filename mismatch: {logical_key}")
            committed = entry["committed_sha256"]
            pending = entry["pending_sha256"]
            for digest in (committed, pending):
                if digest is not None and not _valid_sha256(digest):
                    raise ValueError(f"Discord asset ledger hash is invalid: {logical_key}")
            if committed is None and pending is None:
                raise ValueError(f"Discord asset ledger has no durable state: {logical_key}")
            path = self._run_root / "asset-records" / expected_name
            referenced_names.add(expected_name)
            if not self._asset_ledger.reconcile(entry, path):
                continue
            payload = _read_json(path)
            if not isinstance(payload, dict) or payload.get("logical_key") != logical_key:
                raise ValueError(f"Discord asset record identity mismatch: {expected_name}")
            source_record_sha256 = _sha256_file(path)
            needs_migration = payload.get("schema_version") in {None, 2}
            payload = self._migrate_e754_asset_record(payload, path)
            payload, compatibility_migrated = (
                self._migrate_legacy_media_compatibility(
                    payload,
                    path,
                    source_record_sha256=source_record_sha256,
                )
            )
            stale_reference_cleared = (
                self._clear_stale_completed_youtube_embed_player_reference(payload)
            )
            self._validate_asset_record(payload, path)
            reconciled = self._reconcile_youtube_embed_player_reference(payload)
            if reconciled:
                self._validate_asset_record(payload, path)
            if (
                needs_migration
                or compatibility_migrated
                or stale_reference_cleared
                or reconciled
            ):
                self._commit_asset_record(payload)
            records[logical_key] = payload

        for path in sorted((self._run_root / "asset-records").glob("*.json")):
            if path.name in referenced_names:
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Invalid Discord asset record: {path.name}")
            payload = _read_json(path)
            if not isinstance(payload, dict) or not isinstance(payload.get("logical_key"), str):
                raise ValueError(f"Invalid Discord asset record: {path.name}")
            logical_key = payload["logical_key"]
            expected_name = hashlib.sha256(logical_key.encode("utf-8")).hexdigest() + ".json"
            if path.name != expected_name:
                raise ValueError(f"Discord asset record identity mismatch: {path.name}")
            if logical_key in records:
                raise ValueError(f"Duplicate Discord asset logical key: {logical_key}")
            source_record_sha256 = _sha256_file(path)
            needs_migration = payload.get("schema_version") in {None, 2}
            payload = self._migrate_e754_asset_record(payload, path)
            payload, compatibility_migrated = (
                self._migrate_legacy_media_compatibility(
                    payload,
                    path,
                    source_record_sha256=source_record_sha256,
                )
            )
            stale_reference_cleared = (
                self._clear_stale_completed_youtube_embed_player_reference(payload)
            )
            self._validate_asset_record(payload, path)
            reconciled = self._reconcile_youtube_embed_player_reference(payload)
            if reconciled:
                self._validate_asset_record(payload, path)
            records[logical_key] = payload
            if (
                needs_migration
                or compatibility_migrated
                or stale_reference_cleared
                or reconciled
            ):
                self._commit_asset_record(payload)
            else:
                self._asset_ledger.register_existing(
                    logical_key,
                    path.name,
                    _sha256_file(path),
                )

        checkpoint_changed = False
        if legacy:
            self._checkpoint["assets"] = {}
            checkpoint_changed = True
        if self._checkpoint.get("asset_ledger") != _ASSET_LEDGER_MARKER:
            self._checkpoint["asset_ledger"] = dict(_ASSET_LEDGER_MARKER)
            checkpoint_changed = True
        if checkpoint_changed:
            self._save_checkpoint()
        return records

    def _migrate_legacy_media_compatibility(
        self,
        record: dict[str, Any],
        path: Path,
        *,
        source_record_sha256: str,
    ) -> tuple[dict[str, Any], bool]:
        verified_empty_blob = False
        if is_legacy_zero_complete_candidate(record):
            attempts = record.get("attempt_history")
            source_attempt = (
                attempts[-1]
                if isinstance(attempts, list) and attempts
                else None
            )
            if not isinstance(source_attempt, Mapping):
                raise ValueError(
                    f"Discord zero-byte migration source is invalid: {path.name}"
                )
            self._validate_blob_reference(
                source_attempt.get("sha256"),
                source_attempt.get("blob_path"),
                0,
                label=f"legacy zero-byte source in {path.name}",
            )
            verified_empty_blob = True
        return migrate_legacy_media_record(
            record,
            source_record_sha256=source_record_sha256,
            verified_empty_blob=verified_empty_blob,
        )

    def _migrate_e754_asset_record(
        self,
        record: dict[str, Any],
        path: Path,
    ) -> dict[str, Any]:
        if record.get("schema_version") in {
            _LEGACY_ASSET_RECORD_SCHEMA_VERSION,
            _ASSET_RECORD_SCHEMA_VERSION,
        }:
            return record
        if record.get("schema_version") == 2:
            migrated = deepcopy(record)
            migrated["schema_version"] = _ASSET_RECORD_SCHEMA_VERSION
            if "candidate_urls" not in migrated:
                current_url = migrated.get("url")
                if not isinstance(current_url, str) or not current_url:
                    raise ValueError(
                        f"Malformed v2 Discord asset record: {path.name}"
                    )
                migrated["candidate_urls"] = [current_url]
            if migrated.get("kind") == "attachment":
                metadata = migrated.get("declared_metadata")
                if not isinstance(metadata, dict):
                    raise ValueError(
                        f"Malformed v2 Discord asset record: {path.name}"
                    )
                migrated["identity_metadata"] = _attachment_identity_metadata(
                    metadata
                )
            return migrated
        if "schema_version" in record:
            return record
        current_only = {"identity_metadata", "observations", "identity_conflicts"}
        if current_only.intersection(record) or not _E754_ASSET_KEYS.issubset(record):
            raise ValueError(f"Unrecognized versionless Discord asset record: {path.name}")
        metadata = record.get("declared_metadata")
        sources = record.get("sources")
        url = record.get("url")
        if (
            not isinstance(metadata, dict)
            or not isinstance(sources, list)
            or not isinstance(url, str)
            or not isinstance(record.get("observed_urls"), list)
            or not isinstance(record.get("attempt_history"), list)
        ):
            raise ValueError(f"Malformed e754 Discord asset record: {path.name}")
        logical_key = record.get("logical_key")
        kind = record.get("kind")
        field = record.get("field")
        if kind == "attachment":
            attachment_id = metadata.get("id")
            if (
                field != "attachment"
                or not isinstance(attachment_id, str)
                or not isinstance(logical_key, str)
                or re.fullmatch(
                    rf"[0-9]+:attachment:{re.escape(attachment_id)}",
                    logical_key,
                )
                is None
            ):
                raise ValueError(f"Invalid e754 attachment identity: {path.name}")
            identity_metadata = _attachment_identity_metadata(metadata)
        elif kind == "embed":
            if (
                field not in {"image", "thumbnail", "video"}
                or not isinstance(logical_key, str)
                or re.fullmatch(rf"[0-9]+:embed:[0-9]+:{field}", logical_key) is None
            ):
                raise ValueError(f"Invalid e754 embed identity: {path.name}")
            identity_metadata = _without_url_metadata(metadata)
        else:
            raise ValueError(f"Invalid e754 asset kind: {path.name}")
        observations: list[dict[str, Any]] = []
        for source in sources:
            if not isinstance(source, dict):
                raise ValueError(f"Invalid e754 asset source: {path.name}")
            observation = {
                "source": deepcopy(source),
                "metadata": deepcopy(metadata),
                "url": url,
                "proxy_url": metadata.get("proxy_url"),
            }
            if observation not in observations:
                observations.append(observation)
        if not observations:
            raise ValueError(f"e754 Discord asset has no sources: {path.name}")
        migrated = deepcopy(record)
        migrated.update(
            {
                "schema_version": _ASSET_RECORD_SCHEMA_VERSION,
                "identity_metadata": identity_metadata,
                "observations": observations,
                "identity_conflicts": [],
            }
        )
        migrated.setdefault("candidate_urls", [url])
        return migrated

    def _validate_asset_record(self, record: dict[str, Any], path: Path) -> None:
        if record.get("schema_version") not in {
            _LEGACY_ASSET_RECORD_SCHEMA_VERSION,
            _ASSET_RECORD_SCHEMA_VERSION,
        }:
            raise ValueError(f"Discord asset record schema is invalid: {path.name}")
        if (
            record.get("schema_version") == _LEGACY_ASSET_RECORD_SCHEMA_VERSION
            and (record.get("kind"), record.get("field"))
            in {
                ("embed", "author_icon"),
                ("embed", "footer_icon"),
            }
        ):
            raise ValueError(
                f"Discord legacy icon record was not migrated: {path.name}"
            )
        if record.get("kind") not in {
            "attachment",
            "embed",
            "component",
            "sticker",
            "emoji",
        }:
            raise ValueError(f"Discord asset record has invalid kind: {path.name}")
        if not isinstance(record.get("field"), str) or not record["field"]:
            raise ValueError(f"Discord asset record has invalid field: {path.name}")
        if not isinstance(record.get("url"), str) or not record["url"]:
            raise ValueError(f"Discord asset record has invalid URL: {path.name}")
        candidate_urls = record.get("candidate_urls")
        if candidate_urls is not None and (
            not isinstance(candidate_urls, list)
            or not candidate_urls
            or any(not isinstance(value, str) or not value for value in candidate_urls)
            or len(set(candidate_urls)) != len(candidate_urls)
            or record["url"] not in candidate_urls
        ):
            raise ValueError(
                f"Discord asset record has invalid candidate URLs: {path.name}"
            )
        observed_urls = record.get("observed_urls")
        if (
            not isinstance(observed_urls, list)
            or any(not isinstance(value, str) or not value for value in observed_urls)
            or len(set(observed_urls)) != len(observed_urls)
            or (
                isinstance(candidate_urls, list)
                and any(value not in observed_urls for value in candidate_urls)
            )
        ):
            raise ValueError(
                f"Discord asset record has invalid observed URLs: {path.name}"
            )
        if not isinstance(record.get("declared_metadata"), dict):
            raise ValueError(f"Discord asset record has invalid metadata: {path.name}")
        if not isinstance(record.get("identity_metadata"), dict):
            raise ValueError(f"Discord asset record has invalid identity metadata: {path.name}")
        observations = record.get("observations")
        if not isinstance(observations, list) or not observations:
            raise ValueError(f"Discord asset record has no observations: {path.name}")
        for observation in observations:
            if (
                not isinstance(observation, dict)
                or not isinstance(observation.get("source"), dict)
                or not isinstance(observation.get("metadata"), dict)
                or not isinstance(observation.get("url"), str)
            ):
                raise ValueError(f"Discord asset observation is invalid: {path.name}")
        status = record.get("status")
        if status not in {
            "complete",
            "captured_with_warning",
            "reference_only",
            "failed",
            "in_progress",
            "not_requested",
        }:
            raise ValueError(f"Discord asset record has invalid status: {path.name}")
        if not isinstance(record.get("sources"), list):
            raise ValueError(f"Discord asset record has invalid sources: {path.name}")
        attempt_history = record.get("attempt_history", [])
        if not isinstance(attempt_history, list):
            raise ValueError(f"Discord asset record has invalid attempt history: {path.name}")
        actual_bytes = record.get("actual_bytes")
        if isinstance(actual_bytes, bool) or not isinstance(actual_bytes, int) or actual_bytes < 0:
            raise ValueError(f"Discord asset record has invalid byte count: {path.name}")
        if status in _COVERED_ASSET_STATUSES and actual_bytes > self._max_asset_bytes:
            raise ValueError(
                f"Discord covered asset exceeds maximum asset size: {path.name}"
            )
        digest = record.get("sha256")
        blob_value = record.get("blob_path")
        is_youtube_player_reference = (
            status == "reference_only"
            and record.get("terminal_reason")
            == _YOUTUBE_EMBED_PLAYER_REFERENCE_REASON
        )
        if digest is None and blob_value is None:
            if status in _COVERED_ASSET_STATUSES and not is_youtube_player_reference:
                raise ValueError(f"Complete Discord asset has no blob: {path.name}")
        else:
            self._validate_blob_reference(
                digest,
                blob_value,
                actual_bytes,
                label=f"asset record {path.name}",
            )
        for attempt_number, attempt in enumerate(attempt_history, start=1):
            if not isinstance(attempt, dict):
                raise ValueError(f"Discord asset attempt is invalid: {path.name}")
            attempt_status = attempt.get("status")
            if attempt_status not in {
                "complete",
                "captured_with_warning",
                "reference_only",
                "failed",
                "in_progress",
                "interrupted",
            }:
                raise ValueError(f"Discord asset attempt status is invalid: {path.name}")
            attempt_bytes = attempt.get("actual_bytes")
            if (
                isinstance(attempt_bytes, bool)
                or not isinstance(attempt_bytes, int)
                or attempt_bytes < 0
            ):
                raise ValueError(f"Discord asset attempt byte count is invalid: {path.name}")
            if (
                attempt_status in _COVERED_ASSET_STATUSES
                and attempt_bytes > self._max_asset_bytes
            ):
                raise ValueError(
                    "Discord covered asset attempt exceeds maximum asset size: "
                    f"{path.name}"
                )
            attempt_digest = attempt.get("sha256")
            attempt_blob = attempt.get("blob_path")
            if attempt_digest is None and attempt_blob is None:
                if attempt_status in _COVERED_ASSET_STATUSES:
                    raise ValueError(
                        "Covered Discord asset attempt has no blob: "
                        f"{path.name}/{attempt_number}"
                    )
                continue
            self._validate_blob_reference(
                attempt_digest,
                attempt_blob,
                attempt_bytes,
                label=f"asset attempt {attempt_number} in {path.name}",
            )
        provenance_present = "reference_provenance" in record
        if is_youtube_player_reference:
            expected_provenance = _youtube_embed_player_reference_provenance(record)
            if (
                expected_provenance is None
                or record.get("reference_provenance") != expected_provenance
            ):
                raise ValueError(
                    f"Discord asset reference provenance is invalid: {path.name}"
                )
        elif (
            provenance_present
            or record.get("terminal_reason")
            == _YOUTUBE_EMBED_PLAYER_REFERENCE_REASON
        ):
            raise ValueError(
                f"Discord asset reference provenance is invalid: {path.name}"
            )
        validate_resolution_attempt_history(
            record,
            context=self._resolution_context,
        )

    def _validate_blob_reference(
        self,
        digest: object,
        blob_value: object,
        actual_bytes: int,
        *,
        label: str,
    ) -> None:
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(blob_value, str)
        ):
            raise ValueError(f"Discord {label} has invalid blob identity")
        relative = Path(blob_value)
        if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
            raise ValueError(f"Discord {label} blob path is not contained")
        blob = self._run_root / relative
        assets_root = (self._run_root / "assets" / "sha256").resolve(strict=True)
        resolved = blob.resolve(strict=False)
        if not _is_relative_to(resolved, assets_root):
            raise ValueError(f"Discord {label} blob path escapes its root")
        if blob.is_symlink() or not blob.is_file():
            raise ValueError(f"Discord {label} blob is missing or unsafe")
        parts = relative.parts
        if (
            len(parts) != 4
            or parts[:2] != ("assets", "sha256")
            or parts[2] != digest[:2]
            or not parts[3].startswith(digest + ".")
        ):
            raise ValueError(f"Discord {label} blob path does not match its hash")
        blob_size = blob.stat().st_size
        if blob_size != actual_bytes:
            raise ValueError(f"Discord {label} blob content mismatch")
        fingerprint = _file_fingerprint(blob)
        cached = self._blob_validation_cache.get(digest)
        if cached is not None:
            if cached[0] != resolved:
                raise ValueError(f"Discord {label} blob identity is inconsistent")
            if cached == fingerprint:
                return
        if _sha256_file(blob) != digest:
            raise ValueError(f"Discord {label} blob content mismatch")
        verified = _file_fingerprint(blob)
        if verified != fingerprint:
            raise ValueError(f"Discord {label} blob changed during validation")
        self._blob_validation_cache[digest] = verified

    def _verify_recorded_pages(self) -> None:
        for stream_key, state in self._checkpoint["streams"].items():
            hashes = state.get("page_hashes", [])
            if not isinstance(hashes, list):
                raise ValueError(f"Invalid page hash ledger for stream {stream_key}")
            page_states = state.get("page_states", [])
            if page_states and (
                not isinstance(page_states, list) or len(page_states) != len(hashes)
            ):
                raise ValueError(f"Invalid page processing ledger for stream {stream_key}")
            for index, expected_hash in enumerate(hashes, start=1):
                if not _valid_sha256(expected_hash):
                    raise ValueError(
                        f"Invalid raw page hash for stream {stream_key}/{index:06d}"
                    )
                page_path = self._run_root / "pages" / stream_key / f"{index:06d}.json"
                page_document, page_content = _read_regular_json_bytes(
                    page_path,
                    label=f"Discord raw page {stream_key}/{index:06d}",
                )
                if _sha256_bytes(page_content) != expected_hash:
                    raise ValueError(
                        f"Discord raw page hash mismatch: {stream_key}/{index:06d}"
                    )
                fetched_at = _page_fetched_at(
                    page_document,
                    label=f"Discord raw page {stream_key}/{index:06d}",
                )
                if not page_states:
                    continue
                page_state = page_states[index - 1]
                if not isinstance(page_state, dict):
                    raise ValueError(
                        f"Invalid page processing state: {stream_key}/{index:06d}"
                    )
                evidence = page_state.get("message_evidence")
                if evidence is None:
                    continue
                if not isinstance(evidence, dict):
                    raise ValueError(
                        f"Discord message evidence ledger is invalid: "
                        f"{stream_key}/{index:06d}"
                    )
                self._verify_message_evidence_descriptor(
                    stream_key=stream_key,
                    page_number=index,
                    raw_page_sha256=expected_hash,
                    fetched_at=fetched_at,
                    descriptor=evidence,
                )

    def _verify_message_evidence_descriptor(
        self,
        *,
        stream_key: str,
        page_number: int,
        raw_page_sha256: str,
        fetched_at: str,
        descriptor: Mapping[str, Any],
    ) -> None:
        expected_relative = f"message-evidence/{stream_key}/{page_number:06d}.jsonl"
        expected_raw_relative = f"pages/{stream_key}/{page_number:06d}.json"
        channel_id = _message_stream_channel_id(stream_key)
        if (
            descriptor.get("schema_version") != _MESSAGE_EVIDENCE_SCHEMA_VERSION
            or descriptor.get("stream") != stream_key
            or descriptor.get("channel_id") != channel_id
            or descriptor.get("page_number") != page_number
            or descriptor.get("path") != expected_relative
            or not _valid_sha256(descriptor.get("sha256"))
            or descriptor.get("raw_page_path") != expected_raw_relative
            or descriptor.get("raw_page_sha256") != raw_page_sha256
            or descriptor.get("fetched_at") != fetched_at
        ):
            raise ValueError(
                f"Discord message evidence identity mismatch: "
                f"{stream_key}/{page_number:06d}"
            )

        evidence_path = self._run_root / expected_relative
        evidence_content = _read_regular_bytes(
            evidence_path,
            label=(
                f"Discord message evidence {stream_key}/{page_number:06d}"
            ),
        )
        if _sha256_bytes(evidence_content) != descriptor["sha256"]:
            raise ValueError(
                f"Discord message evidence hash mismatch: "
                f"{stream_key}/{page_number:06d}"
            )
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(evidence_content.splitlines(), start=1):
            try:
                row = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"Discord message evidence row is invalid: "
                    f"{stream_key}/{page_number:06d}/{line_number}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(
                    f"Discord message evidence row is invalid: "
                    f"{stream_key}/{page_number:06d}/{line_number}"
                )
            rows.append(row)

        calculated = _message_evidence_counts(
            rows,
            stream_key=stream_key,
            channel_id=channel_id,
            page_number=page_number,
            raw_page_sha256=raw_page_sha256,
        )
        for field, value in calculated.items():
            if descriptor.get(field) != value:
                raise ValueError(
                    f"Discord message evidence descriptor mismatch: "
                    f"{stream_key}/{page_number:06d}/{field}"
                )

    def _collect_all(self, snapshot: dict[str, Any]) -> None:
        guild_id = snapshot["guild_id"]
        self._guild_id = guild_id
        requested = {target["id"]: target for target in snapshot["targets"]}
        bot = self._inventory_object("inventory_bot", "/users/@me", "bot.json")
        guild = self._inventory_object(
            "inventory_guild", f"/guilds/{guild_id}", "guild.json"
        )
        channels = self._inventory_object(
            "inventory_channels", f"/guilds/{guild_id}/channels", "channels.json"
        )
        if not isinstance(bot, dict) or not isinstance(guild, dict) or not isinstance(channels, list):
            raise ValueError("Discord inventory payload shape is invalid")
        bot_valid = _valid_snowflake(bot.get("id"))
        self._set_simple_stream(
            "inventory_bot_validation",
            "complete" if bot_valid else "failed",
            "identity_valid" if bot_valid else "identity_id_invalid",
        )
        guild_valid = guild.get("id") == guild_id
        self._set_simple_stream(
            "inventory_guild_validation",
            "complete" if guild_valid else "failed",
            "guild_valid" if guild_valid else "guild_id_mismatch",
        )
        if not guild_valid:
            self._write_target_inventory(requested, {}, {})
            return

        active_payload = self._active_threads(guild_id)
        active_threads = active_payload.get("threads", []) if isinstance(active_payload, dict) else []
        if not isinstance(active_threads, list):
            raise ValueError("Discord active threads payload has no threads list")

        channel_by_id: dict[str, dict[str, Any]] = {}
        inventory_ids: set[str] = set()
        channel_errors: list[str] = []
        for item in channels:
            if isinstance(item, dict) and _valid_snowflake(item.get("id")):
                inventory_ids.add(item["id"])
            error = self._channel_metadata_error(item, guild_id, require_guild=False)
            if error is not None:
                channel_errors.append(error)
                continue
            channel_id = item["id"]
            if channel_id in channel_by_id:
                channel_errors.append(f"duplicate_channel_id:{channel_id}")
                continue
            channel_by_id[channel_id] = item
        self._set_simple_stream(
            "inventory_channels_validation",
            "failed" if channel_errors else "complete",
            ";".join(channel_errors) if channel_errors else "channel_graph_valid",
        )

        valid_active_threads: list[dict[str, Any]] = []
        active_errors: list[str] = []
        for thread in active_threads:
            error = self._thread_metadata_error(thread, guild_id, require_guild=False)
            if error is not None:
                active_errors.append(error)
            else:
                valid_active_threads.append(thread)
        self._set_simple_stream(
            "inventory_active_threads_validation",
            "failed" if active_errors else "complete",
            ";".join(active_errors) if active_errors else "active_threads_valid",
        )

        target_live: dict[str, dict[str, Any] | None] = {}
        for target_id, target_request in requested.items():
            metadata = channel_by_id.get(target_id)
            if metadata is None and target_id not in inventory_ids:
                metadata = self._supplement_target_metadata(
                    target_id,
                    target_request,
                    guild_id,
                )
            target_error = self._target_metadata_error(
                metadata,
                target_request,
                guild_id,
                require_guild=False,
            )
            if target_error is not None:
                self._set_simple_stream(
                    f"target_validation_{target_id}", "failed", target_error
                )
                metadata = None
            else:
                self._set_simple_stream(
                    f"target_validation_{target_id}", "complete", "target_metadata_valid"
                )
            target_live[target_id] = metadata

        threads: dict[str, dict[str, Any]] = {}
        target_ids = set(requested)
        target_parent_ids = {
            target_id
            for target_id, metadata in target_live.items()
            if metadata is not None and metadata.get("type") not in _THREAD_TYPES
        }
        for thread in valid_active_threads:
            if thread.get("parent_id") in target_parent_ids or thread["id"] in target_ids:
                self._merge_thread(
                    threads,
                    thread,
                    "active",
                    expected_parent_id=thread.get("parent_id"),
                )
        for target_id, metadata in target_live.items():
            if metadata is not None and metadata.get("type") in _THREAD_TYPES:
                self._merge_thread(
                    threads,
                    metadata,
                    "explicit_target",
                    expected_parent_id=requested[target_id].get("parent_id"),
                )

        for parent_id in sorted(target_parent_ids, key=int):
            metadata = target_live[parent_id]
            if metadata is None or metadata.get("type") not in _THREAD_PARENT_TYPES:
                continue
            self._discover_archived_threads(parent_id, threads)

        for target_id in sorted(target_parent_ids, key=int):
            metadata = target_live[target_id]
            if (
                metadata is None
                or metadata.get("type") in _FORUM_OR_MEDIA_TYPES
                or metadata.get("type") not in _MESSAGE_BEARING_TYPES
            ):
                if metadata is not None and metadata.get("type") not in (
                    _FORUM_OR_MEDIA_TYPES | _MESSAGE_BEARING_TYPES
                ):
                    self._set_simple_stream(
                        f"target_collection_{target_id}",
                        "failed",
                        "unsupported_target_type",
                    )
                continue
            self._collect_message_bearing_target(target_id, threads)

        processed_threads: set[str] = set()
        while True:
            pending = sorted(set(threads) - processed_threads, key=int)
            if not pending:
                break
            for thread_id in pending:
                processed_threads.add(thread_id)
                self._collect_message_bearing_target(thread_id, threads)

        self._write_target_inventory(requested, target_live, threads)

    def _write_target_inventory(
        self,
        requested: dict[str, dict[str, Any]],
        target_live: dict[str, dict[str, Any] | None],
        threads: dict[str, dict[str, Any]],
    ) -> None:
        target_inventory = {
            "targets": [
                {
                    "requested": requested[target_id],
                    "metadata": target_live.get(target_id),
                }
                for target_id in requested
            ],
            "threads": [
                {
                    **deepcopy(threads[thread_id]["metadata"]),
                    "id": thread_id,
                    "sources": sorted(threads[thread_id]["sources"]),
                }
                for thread_id in sorted(threads, key=int)
            ],
        }
        _atomic_write_json(self._run_root / "inventory" / "targets.json", target_inventory)
        self._set_simple_stream("inventory_targets", "complete", "inventory_saved")

    def _inventory_object(self, key: str, path: str, filename: str) -> Any:
        destination = self._run_root / "inventory" / filename
        state = self._checkpoint["streams"].get(key)
        if state and state.get("status") == "complete":
            self._verify_single_response_file(key, state, destination)
            return _read_json(destination)
        if state and state.get("status") in {"failed", "blocked", "not_found"}:
            raise ValueError(f"Discord terminal inventory endpoint: {key}")
        if key == "inventory_bot":
            payload = deepcopy(self._authenticated_bot_payload)
        else:
            try:
                payload = self._json.get_json(path, {})
            except DiscordAPIError as exc:
                if exc.status_code == 401:
                    raise
                self._record_endpoint_error(key, exc)
                raise
        digest = _write_exclusive_or_same(destination, payload)
        self._set_simple_stream(
            key,
            "complete",
            "single_response",
            evidence_sha256=digest,
            evidence_path=f"inventory/{filename}",
        )
        return payload

    def _active_threads(self, guild_id: str) -> Any:
        key = "inventory_active_threads"
        destination = self._run_root / "inventory" / "active-threads.json"
        state = self._checkpoint["streams"].get(key)
        if state and state.get("status") == "complete":
            self._verify_single_response_file(key, state, destination)
            return _read_json(destination)
        if state and state.get("status") in {"failed", "blocked", "not_found"}:
            return {"threads": [], "members": []}
        path = f"/guilds/{guild_id}/threads/active"
        try:
            payload = self._json.get_json(path, {})
        except DiscordAPIError as exc:
            if exc.status_code == 401:
                raise
            self._record_endpoint_error(key, exc)
            return {"threads": [], "members": []}
        digest = _write_exclusive_or_same(destination, payload)
        self._set_simple_stream(
            key,
            "complete",
            "single_response",
            evidence_sha256=digest,
            evidence_path="inventory/active-threads.json",
        )
        return payload

    def _supplement_target_metadata(
        self,
        target_id: str,
        requested: dict[str, Any],
        guild_id: str,
    ) -> dict[str, Any] | None:
        key = f"target_metadata_{target_id}"
        state = self._checkpoint["streams"].get(key)
        if state and state.get("status") in {"complete", "failed", "blocked", "not_found"}:
            payload = next(self._stored_payloads(key), None)
            return payload if state.get("status") == "complete" and isinstance(payload, dict) else None
        try:
            payload = self._json.get_json(f"/channels/{target_id}", {})
        except DiscordAPIError as exc:
            if exc.status_code == 401:
                raise
            self._record_endpoint_error(key, exc)
            return None
        if not isinstance(payload, dict):
            self._set_simple_stream(key, "failed", "metadata_not_object")
            return None
        page = DiscordPage(payload, f"/channels/{target_id}", {}, 1, None, "complete")
        page_number = self._land_page(key, page, terminal_reason="single_response")
        self._mark_page_processed(key, page_number, payload)
        error = self._target_metadata_error(
            payload,
            requested,
            guild_id,
            require_guild=True,
        )
        if error is not None:
            state = self._checkpoint["streams"][key]
            state["status"] = "failed"
            state["terminal_reason"] = error
            self._save_checkpoint()
            return None
        return payload

    def _verify_single_response_file(
        self,
        stream_key: str,
        state: dict[str, Any],
        destination: Path,
    ) -> None:
        expected_hash = state.get("evidence_sha256")
        if not isinstance(expected_hash, str) or not destination.is_file():
            raise ValueError(f"Discord inventory evidence is missing: {stream_key}")
        if destination.is_symlink() or _sha256_file(destination) != expected_hash:
            raise ValueError(f"Discord inventory evidence hash mismatch: {stream_key}")

    def _channel_metadata_error(
        self,
        metadata: object,
        guild_id: str,
        *,
        require_guild: bool,
    ) -> str | None:
        if not isinstance(metadata, dict):
            return "channel_metadata_not_object"
        channel_id = metadata.get("id")
        if not _valid_snowflake(channel_id):
            return "channel_id_invalid"
        channel_type = metadata.get("type")
        if (
            isinstance(channel_type, bool)
            or not isinstance(channel_type, int)
            or channel_type not in _KNOWN_GUILD_CHANNEL_TYPES
        ):
            return f"channel_type_invalid:{channel_id}"
        metadata_guild = metadata.get("guild_id")
        if require_guild and metadata_guild is None:
            return f"channel_guild_id_missing:{channel_id}"
        if metadata_guild is not None and metadata_guild != guild_id:
            return f"channel_guild_id_mismatch:{channel_id}"
        parent_id = metadata.get("parent_id")
        if parent_id is not None and not _valid_snowflake(parent_id):
            return f"channel_parent_id_invalid:{channel_id}"
        return None

    def _thread_metadata_error(
        self,
        metadata: object,
        guild_id: str,
        *,
        require_guild: bool,
        expected_parent_id: str | None = None,
    ) -> str | None:
        error = self._channel_metadata_error(
            metadata,
            guild_id,
            require_guild=require_guild,
        )
        if error is not None:
            return error
        assert isinstance(metadata, dict)
        if metadata.get("type") not in _THREAD_TYPES:
            return f"thread_type_invalid:{metadata['id']}"
        parent_id = metadata.get("parent_id")
        if not _valid_snowflake(parent_id):
            return f"thread_parent_id_missing:{metadata['id']}"
        if expected_parent_id is not None and parent_id != expected_parent_id:
            return f"thread_parent_id_mismatch:{metadata['id']}"
        return None

    def _target_metadata_error(
        self,
        metadata: object,
        requested: dict[str, Any],
        guild_id: str,
        *,
        require_guild: bool,
    ) -> str | None:
        if metadata is None:
            return "target_metadata_unavailable"
        error = self._channel_metadata_error(
            metadata,
            guild_id,
            require_guild=require_guild,
        )
        if error is not None:
            return error
        assert isinstance(metadata, dict)
        if metadata.get("id") != requested["id"]:
            return "target_id_mismatch"
        channel_type = metadata["type"]
        declared_parent = requested.get("parent_id")
        if channel_type in _THREAD_TYPES:
            if declared_parent is None:
                return "thread_declared_parent_missing"
            return self._thread_metadata_error(
                metadata,
                guild_id,
                require_guild=require_guild,
                expected_parent_id=declared_parent,
            )
        return None

    def _discover_archived_threads(
        self,
        parent_id: str,
        threads: dict[str, dict[str, Any]],
    ) -> None:
        sources: tuple[
            tuple[str, str, Callable[..., Iterator[DiscordPage]]], ...
        ] = (
            ("public_archived", "public_archived", iter_public_archived_thread_pages),
            ("private_archived", "private_archived", iter_private_archived_thread_pages),
            (
                "joined_private_archived",
                "joined_private_archived",
                iter_joined_private_archived_thread_pages,
            ),
        )
        for suffix, source, paginator in sources:
            key = f"threads_{parent_id}_{suffix}"
            payloads = self._collect_paginated(
                key,
                lambda before, remaining, paginator=paginator: paginator(
                    self._json,
                    parent_id,
                    before=before,
                    max_pages=remaining,
                ),
                complete_reason="has_more_false",
            )
            for payload in payloads:
                if not isinstance(payload, dict):
                    continue
                for thread in payload.get("threads", []):
                    if isinstance(thread, dict):
                        self._merge_thread(
                            threads,
                            thread,
                            source,
                            expected_parent_id=parent_id,
                        )

    def _collect_message_bearing_target(
        self,
        channel_id: str,
        threads: dict[str, dict[str, Any]],
    ) -> None:
        message_key = f"messages_{channel_id}"

        def process_messages(
            payload: Any,
            page_number: int,
            page_sha256: str,
        ) -> dict[str, Any] | None:
            if not isinstance(payload, list):
                return None
            evidence_messages: list[
                tuple[dict[str, Any], str, dict[str, Any] | None]
            ] = []
            for index, message in enumerate(payload):
                if self._message_item_error(message, channel_id) is not None:
                    continue
                assert isinstance(message, dict)
                embedded = message.get("thread")
                if isinstance(embedded, dict):
                    self._merge_thread(
                        threads,
                        embedded,
                        "message_embedded",
                        expected_parent_id=channel_id,
                    )
                evidence_messages.append((message, f"/payload/{index}", None))
            return self._write_message_evidence_page(
                stream_key=message_key,
                channel_id=channel_id,
                page_number=page_number,
                raw_page_sha256=page_sha256,
                messages=evidence_messages,
            )

        message_payloads = self._collect_paginated(
            message_key,
            lambda before, remaining: iter_message_pages(
                self._json,
                channel_id,
                before=before,
                max_pages=remaining,
            ),
            complete_reason="empty_page",
            process_payload=process_messages,
        )
        self._write_item_validation(
            f"{message_key}_item_validation",
            message_payloads,
            envelope_key=None,
            validator=lambda item: self._message_item_error(item, channel_id),
        )
        self._audit_message_page_order(
            message_key,
            channel_id=channel_id,
            pin_stream=False,
        )
        state = self._checkpoint["streams"].get(message_key, {})
        if state.get("status") in {"blocked", "not_found", "failed", "in_progress"}:
            return

        pin_key = f"pins_{channel_id}"

        def process_pins(
            payload: Any,
            page_number: int,
            page_sha256: str,
        ) -> dict[str, Any] | None:
            if not isinstance(payload, dict):
                return None
            evidence_messages: list[
                tuple[dict[str, Any], str, dict[str, Any] | None]
            ] = []
            for index, item in enumerate(payload.get("items", [])):
                if self._pin_item_error(item, channel_id) is not None:
                    continue
                assert isinstance(item, dict)
                message = item.get("message")
                assert isinstance(message, dict)
                pinned_at = item["pinned_at"]
                assert isinstance(pinned_at, str)
                pinned_at_utc = _parse_aware_datetime(pinned_at).astimezone(
                    UTC
                ).isoformat()
                embedded = message.get("thread")
                if isinstance(embedded, dict):
                    self._merge_thread(
                        threads,
                        embedded,
                        "message_embedded",
                        expected_parent_id=channel_id,
                    )
                evidence_messages.append(
                    (
                        message,
                        f"/payload/items/{index}/message",
                        {
                            "event_key": (
                                f"pin_event:{channel_id}:{message['id']}:"
                                f"{pinned_at_utc}"
                            ),
                            "channel_id": channel_id,
                            "message_id": message["id"],
                            "pinned_at": pinned_at,
                            "pinned_at_utc": pinned_at_utc,
                            "json_pointer": f"/payload/items/{index}",
                        },
                    )
                )
            return self._write_message_evidence_page(
                stream_key=pin_key,
                channel_id=channel_id,
                page_number=page_number,
                raw_page_sha256=page_sha256,
                messages=evidence_messages,
            )

        pin_payloads = self._collect_paginated(
            pin_key,
            lambda before, remaining: iter_pin_pages(
                self._json,
                channel_id,
                before=before,
                max_pages=remaining,
            ),
            complete_reason="has_more_false",
            process_payload=process_pins,
        )
        self._write_item_validation(
            f"{pin_key}_item_validation",
            pin_payloads,
            envelope_key="items",
            validator=lambda item: self._pin_item_error(item, channel_id),
        )
        self._audit_message_page_order(
            pin_key,
            channel_id=channel_id,
            pin_stream=True,
        )

    @staticmethod
    def _message_item_error(
        item: object,
        expected_channel_id: str | None = None,
    ) -> str | None:
        if not isinstance(item, dict):
            return "message_not_object"
        if not _valid_snowflake(item.get("id")):
            return "message_id_invalid"
        channel_id = item.get("channel_id")
        if not _valid_snowflake(channel_id):
            return "channel_id_invalid"
        if expected_channel_id is not None and channel_id != expected_channel_id:
            return "channel_id_mismatch"
        return None

    @classmethod
    def _pin_item_error(
        cls,
        item: object,
        expected_channel_id: str | None = None,
    ) -> str | None:
        if not isinstance(item, dict):
            return "pin_item_not_object"
        pinned_at = item.get("pinned_at")
        if not isinstance(pinned_at, str) or not pinned_at:
            return "pinned_at_invalid"
        try:
            _parse_aware_datetime(pinned_at)
        except ValueError:
            return "pinned_at_invalid"
        message = item.get("message")
        if not isinstance(message, dict):
            return "pin_message_not_object"
        message_error = cls._message_item_error(message, expected_channel_id)
        if message_error is not None:
            return f"pin_{message_error}"
        return None

    def _write_item_validation(
        self,
        stream_key: str,
        payloads: Iterable[Any],
        *,
        envelope_key: str | None,
        validator: Callable[[object], str | None],
    ) -> None:
        valid_items = 0
        invalid_items = 0
        diagnostics: list[dict[str, Any]] = []
        for page_number, payload in enumerate(payloads, start=1):
            if envelope_key is None:
                items = payload if isinstance(payload, list) else None
            else:
                items = payload.get(envelope_key) if isinstance(payload, dict) else None
            if not isinstance(items, list):
                invalid_items += 1
                diagnostics.append(
                    {"page": page_number, "item": None, "reason": "items_not_list"}
                )
                continue
            for item_number, item in enumerate(items, start=1):
                error = validator(item)
                if error is None:
                    valid_items += 1
                else:
                    invalid_items += 1
                    diagnostics.append(
                        {"page": page_number, "item": item_number, "reason": error}
                    )
        self._set_simple_stream(
            stream_key,
            "failed" if invalid_items else "complete",
            "invalid_items" if invalid_items else "items_valid",
            valid_items=valid_items,
            invalid_items=invalid_items,
            diagnostics=diagnostics,
        )

    def _audit_message_page_order(
        self,
        stream_key: str,
        *,
        channel_id: str,
        pin_stream: bool,
    ) -> None:
        diagnostics: list[dict[str, Any]] = []
        seen: set[object] = set()
        previous_last_cursor: str | None = None
        checked_items = 0
        first_page_values: list[tuple[str, str | None]] = []
        last_nonempty_values: list[tuple[str, str | None]] = []
        first_fetched_at: str | None = None
        last_fetched_at: str | None = None
        expected_path = (
            f"/channels/{channel_id}/messages/pins"
            if pin_stream
            else f"/channels/{channel_id}/messages"
        )
        pages = 0
        for page_number, document in enumerate(
            self._stored_page_documents(stream_key),
            start=1,
        ):
            pages = page_number
            fetched_at = _page_fetched_at(
                document,
                label=f"Discord raw page {stream_key}/{page_number:06d}",
            )
            if first_fetched_at is None:
                first_fetched_at = fetched_at
            last_fetched_at = fetched_at
            request = document.get("request")
            params = request.get("params") if isinstance(request, dict) else None
            request_before = params.get("before") if isinstance(params, dict) else None
            if (
                not isinstance(request, dict)
                or request.get("path") != expected_path
                or not isinstance(params, dict)
            ):
                diagnostics.append(
                    {"page": page_number, "item": None, "reason": "request_identity_invalid"}
                )
            if page_number == 1:
                if request_before is not None:
                    diagnostics.append(
                        {
                            "page": page_number,
                            "item": None,
                            "reason": "unexpected_initial_before_cursor",
                            "actual": request_before,
                        }
                    )
            elif request_before != previous_last_cursor:
                diagnostics.append(
                    {
                        "page": page_number,
                        "item": None,
                        "reason": "before_cursor_discontinuity",
                        "expected": previous_last_cursor,
                        "actual": request_before,
                    }
                )

            payload = document.get("payload")
            if pin_stream:
                items = payload.get("items") if isinstance(payload, dict) else None
            else:
                items = payload
            if not isinstance(items, list):
                diagnostics.append(
                    {"page": page_number, "item": None, "reason": "items_not_list"}
                )
                previous_last_cursor = None
                continue

            page_values: list[tuple[str, str | None]] = []
            previous_order_value: int | datetime | None = None
            for item_number, item in enumerate(items, start=1):
                checked_items += 1
                if pin_stream:
                    if not isinstance(item, dict):
                        continue
                    message = item.get("message")
                    pinned_at = item.get("pinned_at")
                    if (
                        not isinstance(message, dict)
                        or not _valid_snowflake(message.get("id"))
                        or not isinstance(pinned_at, str)
                    ):
                        continue
                    message_id = message["id"]
                    try:
                        order_value = _parse_aware_datetime(pinned_at).astimezone(UTC)
                    except ValueError:
                        continue
                    identity: object = (message_id, order_value.isoformat())
                    cursor_value = pinned_at
                    timestamp_value = order_value.isoformat()
                    if (
                        isinstance(request_before, str)
                        and order_value
                        > _parse_aware_datetime(request_before).astimezone(UTC)
                    ):
                        diagnostics.append(
                            {
                                "page": page_number,
                                "item": item_number,
                                "reason": "pin_newer_than_before_cursor",
                            }
                        )
                else:
                    if not isinstance(item, dict) or not _valid_snowflake(
                        item.get("id")
                    ):
                        continue
                    message_id = item["id"]
                    assert isinstance(message_id, str)
                    order_value = int(message_id)
                    identity = message_id
                    cursor_value = message_id
                    timestamp_value = _normalized_aware_timestamp(item.get("timestamp"))
                    if isinstance(request_before, str) and order_value >= int(
                        request_before
                    ):
                        diagnostics.append(
                            {
                                "page": page_number,
                                "item": item_number,
                                "reason": "message_not_older_than_before_cursor",
                            }
                        )

                if identity in seen:
                    diagnostics.append(
                        {
                            "page": page_number,
                            "item": item_number,
                            "reason": (
                                "pin_event_duplicate"
                                if pin_stream
                                else "message_id_duplicate"
                            ),
                        }
                    )
                seen.add(identity)
                if previous_order_value is not None:
                    invalid_order = (
                        order_value > previous_order_value
                        if pin_stream
                        else order_value >= previous_order_value
                    )
                    if invalid_order:
                        diagnostics.append(
                            {
                                "page": page_number,
                                "item": item_number,
                                "reason": (
                                    "pinned_at_not_non_increasing"
                                    if pin_stream
                                    else "message_ids_not_strictly_descending"
                                ),
                            }
                        )
                previous_order_value = order_value
                page_values.append((message_id, timestamp_value))

            pagination = document.get("pagination")
            recorded_cursor = (
                pagination.get("next_cursor")
                if isinstance(pagination, dict)
                else None
            )
            if page_values:
                expected_cursor = cursor_value
                if recorded_cursor is not None and recorded_cursor != expected_cursor:
                    diagnostics.append(
                        {
                            "page": page_number,
                            "item": None,
                            "reason": "response_cursor_mismatch",
                            "expected": expected_cursor,
                            "actual": recorded_cursor,
                        }
                    )
                previous_last_cursor = expected_cursor
                last_nonempty_values = page_values
                if page_number == 1:
                    first_page_values = page_values
            else:
                previous_last_cursor = None

        bounds = _message_stream_bounds(
            first_page_values,
            last_nonempty_values,
            first_fetched_at=first_fetched_at,
            last_fetched_at=last_fetched_at,
        )
        main_state = self._checkpoint["streams"].get(stream_key)
        if isinstance(main_state, dict):
            main_state["bounds"] = bounds
            if diagnostics and main_state.get("status") not in {"blocked", "not_found"}:
                main_state["status"] = "failed"
                main_state["terminal_reason"] = "ordering_validation_failed"
        self._set_simple_stream(
            f"{stream_key}_order_validation",
            "failed" if diagnostics else "complete",
            "ordering_invalid" if diagnostics else "ordering_valid",
            checked_pages=pages,
            checked_items=checked_items,
            diagnostics=diagnostics,
        )

    def _write_message_evidence_page(
        self,
        *,
        stream_key: str,
        channel_id: str,
        page_number: int,
        raw_page_sha256: str,
        messages: list[
            tuple[dict[str, Any], str, dict[str, Any] | None]
        ],
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        totals = {
            "root_messages": 0,
            "partial_messages": 0,
            "nodes": 0,
            "media_occurrences": 0,
            "references": 0,
            "diagnostics": 0,
            "pin_events": 0,
        }
        diagnostics_by_severity = {"error": 0, "warning": 0, "info": 0}
        relative_path = (
            f"message-evidence/{stream_key}/{page_number:06d}.jsonl"
        )
        raw_relative_path = f"pages/{stream_key}/{page_number:06d}.json"
        raw_document, raw_content = _read_regular_json_bytes(
            self._run_root / raw_relative_path,
            label=f"Discord raw page {stream_key}/{page_number:06d}",
        )
        if _sha256_bytes(raw_content) != raw_page_sha256:
            raise ValueError(
                f"Discord raw page hash mismatch: {stream_key}/{page_number:06d}"
            )
        fetched_at = _page_fetched_at(
            raw_document,
            label=f"Discord raw page {stream_key}/{page_number:06d}",
        )
        for message, pointer, pin_event in messages:
            evidence = extract_message_evidence(
                message,
                stream=stream_key,
                evidence_path=raw_relative_path,
                evidence_sha256=raw_page_sha256,
                json_pointer=pointer,
            )
            extracted_keys = self._collect_extracted_media(evidence)
            self._collect_message_assets(
                message,
                channel_id,
                stream_key,
                excluded_logical_keys=extracted_keys,
            )
            row = {
                "schema_version": _MESSAGE_EVIDENCE_SCHEMA_VERSION,
                "stream": stream_key,
                "channel_id": channel_id,
                "page_number": page_number,
                "message_json_pointer": pointer,
                **asdict(evidence),
            }
            if pin_event is not None:
                row["pin_event"] = deepcopy(pin_event)
                totals["pin_events"] += 1
            rows.append(row)
            totals["root_messages"] += 1
            totals["partial_messages"] += evidence.status == "partial"
            totals["nodes"] += len(evidence.nodes)
            totals["media_occurrences"] += len(evidence.media)
            totals["references"] += len(evidence.references)
            totals["diagnostics"] += len(evidence.diagnostics)
            for diagnostic in evidence.diagnostics:
                diagnostics_by_severity.setdefault(diagnostic.severity, 0)
                diagnostics_by_severity[diagnostic.severity] += 1

        content = b"".join(
            _canonical_json_bytes(row, newline=True) for row in rows
        )
        destination = self._run_root / relative_path
        _safe_mkdir(destination.parent, self._run_root)
        digest = _write_exclusive_bytes_or_same(destination, content)
        return {
            "schema_version": _MESSAGE_EVIDENCE_SCHEMA_VERSION,
            "stream": stream_key,
            "channel_id": channel_id,
            "page_number": page_number,
            "path": relative_path,
            "sha256": digest,
            "raw_page_path": raw_relative_path,
            "raw_page_sha256": raw_page_sha256,
            "fetched_at": fetched_at,
            "diagnostics_by_severity": diagnostics_by_severity,
            **totals,
        }

    def _collect_extracted_media(self, evidence: MessageEvidence) -> set[str]:
        extracted_keys = {occurrence.logical_key for occurrence in evidence.media}
        for occurrence in evidence.media:
            if not occurrence.downloadable or not isinstance(occurrence.url, str):
                continue
            if occurrence.resolution in {"attachment_id", "attachment_filename"}:
                existing = self._asset_records.get(occurrence.logical_key)
                if existing is None:
                    raise ValueError(
                        "Discord resolved attachment alias has no attachment record"
                    )
                self._record_asset_alias_observation(existing, occurrence)
                continue
            metadata = deepcopy(dict(occurrence.metadata))
            if occurrence.kind == "attachment":
                identity_metadata = _attachment_identity_metadata(metadata)
                declared_content_type = _normalized_mime(
                    metadata.get("content_type")
                )
                field = occurrence.field
            elif occurrence.kind == "sticker":
                identity_metadata = _sticker_identity_metadata(metadata)
                declared_content_type = None
                field = "sticker"
            else:
                identity_metadata = discord_media_identity_metadata(
                    occurrence.kind,
                    metadata,
                    field=occurrence.field,
                    schema_version=_ASSET_RECORD_SCHEMA_VERSION,
                )
                declared_content_type = _normalized_mime(
                    metadata.get("content_type")
                )
                field = occurrence.field
            candidate = {
                "logical_key": occurrence.logical_key,
                "kind": occurrence.kind,
                "field": field,
                "url": occurrence.url,
                "candidate_urls": _media_candidate_urls(occurrence),
                "declared_metadata": metadata,
                "declared_content_type": declared_content_type,
                "identity_metadata": identity_metadata,
                "_observation_url": (
                    occurrence.observed_url
                    if isinstance(occurrence.observed_url, str)
                    else occurrence.url
                ),
                "_observation_proxy_url": occurrence.proxy_url,
            }
            self._collect_asset(candidate, asdict(occurrence.source))
        return extracted_keys

    def _record_asset_alias_observation(
        self,
        record: dict[str, Any],
        occurrence: MediaOccurrence,
    ) -> None:
        source = asdict(occurrence.source)
        observation = {
            "source": deepcopy(source),
            "metadata": deepcopy(dict(occurrence.metadata)),
            "url": (
                occurrence.observed_url
                if isinstance(occurrence.observed_url, str)
                else occurrence.url
            ),
            "proxy_url": occurrence.proxy_url,
        }
        observations = record.setdefault("observations", [])
        if observation not in observations:
            observations.append(observation)
        if source not in record.get("sources", []):
            record.setdefault("sources", []).append(source)
            record["sources"] = sorted(
                record["sources"],
                key=_asset_source_sort_key,
            )
        self._write_asset_record(record)

    def _collect_paginated(
        self,
        stream_key: str,
        factory: Callable[[str | None, int | None], Iterator[DiscordPage]],
        *,
        complete_reason: str,
        process_payload: Callable[
            [Any, int, str], dict[str, Any] | None
        ]
        | None = None,
    ) -> Iterator[Any]:
        existing = self._checkpoint["streams"].get(stream_key)
        state = existing or self._new_page_stream_state()
        self._checkpoint["streams"][stream_key] = state
        self._normalize_page_state(state)
        self._save_checkpoint()
        page_states = state["page_states"]
        for index, document in enumerate(self._stored_page_documents(stream_key)):
            processing_status = page_states[index]["processing_status"]
            if processing_status == "processed":
                if process_payload is not None:
                    processing_output = process_payload(
                        document["payload"],
                        index + 1,
                        state["page_hashes"][index],
                    )
                    self._record_page_processing_output(
                        stream_key,
                        index + 1,
                        processing_output,
                    )
            else:
                if processing_status != "landed":
                    raise ValueError(
                        f"Invalid Discord page processing status: {stream_key}"
                    )
                if process_payload is not None:
                    processing_output = process_payload(
                        document["payload"],
                        index + 1,
                        state["page_hashes"][index],
                    )
                    self._record_page_processing_output(
                        stream_key,
                        index + 1,
                        processing_output,
                    )
                self._mark_page_processed(
                    stream_key,
                    index + 1,
                    document["payload"],
                )
            yield document["payload"]

        if state.get("status") in {
            "complete",
            "truncated_by_limit",
            "blocked",
            "not_found",
            "failed",
        }:
            return

        before = state.get("next_cursor")
        remaining = (
            None
            if self._max_pages is None
            else max(self._max_pages - int(state.get("pages", 0)), 0)
        )
        if remaining == 0:
            state["status"] = "truncated_by_limit"
            state["terminal_reason"] = "truncated_by_limit"
            self._save_checkpoint()
            return

        try:
            for page in factory(before, remaining):
                terminal_reason = (
                    complete_reason
                    if page.terminal_status == "complete"
                    else page.terminal_status
                )
                page_number = self._land_page(
                    stream_key,
                    page,
                    terminal_reason=terminal_reason,
                )
                if process_payload is not None:
                    processing_output = process_payload(
                        page.raw_payload,
                        page_number,
                        self._checkpoint["streams"][stream_key]["page_hashes"][
                            page_number - 1
                        ],
                    )
                    self._record_page_processing_output(
                        stream_key,
                        page_number,
                        processing_output,
                    )
                self._mark_page_processed(stream_key, page_number, page.raw_payload)
                yield page.raw_payload
        except DiscordAPIError as exc:
            if exc.status_code == 401:
                raise
            self._record_endpoint_error(stream_key, exc)
        return

    def _record_page_processing_output(
        self,
        stream_key: str,
        page_number: int,
        output: dict[str, Any] | None,
    ) -> None:
        if output is None:
            return
        state = self._checkpoint["streams"][stream_key]
        page_state = state["page_states"][page_number - 1]
        existing = page_state.get("message_evidence")
        if existing is not None and existing != output:
            raise ValueError(
                f"Discord message evidence ledger mismatch: "
                f"{stream_key}/{page_number:06d}"
            )
        if existing is None:
            page_state["message_evidence"] = deepcopy(output)
            self._save_checkpoint()

    def _new_page_stream_state(self) -> dict[str, Any]:
        return {
            "status": "in_progress",
            "pages": 0,
            "processed_pages": 0,
            "items": 0,
            "next_cursor": None,
            "page_hashes": [],
            "page_states": [],
            "terminal_reason": None,
            "first_id": None,
            "last_id": None,
            "first_timestamp": None,
            "last_timestamp": None,
        }

    def _normalize_page_state(self, state: dict[str, Any]) -> None:
        hashes = state.setdefault("page_hashes", [])
        page_states = state.setdefault("page_states", [])
        if not isinstance(hashes, list) or not isinstance(page_states, list):
            raise ValueError("Discord page ledger is invalid")
        if not page_states and hashes:
            page_states.extend(
                {
                    "processing_status": "processed",
                    "next_cursor": state.get("next_cursor") if index == len(hashes) else None,
                    "terminal_status": state.get("status") if index == len(hashes) else None,
                    "terminal_reason": state.get("terminal_reason") if index == len(hashes) else None,
                }
                for index in range(1, len(hashes) + 1)
            )
        if len(page_states) != len(hashes):
            raise ValueError("Discord page processing ledger length mismatch")
        state.setdefault(
            "processed_pages",
            sum(item.get("processing_status") == "processed" for item in page_states),
        )

    def _land_page(
        self,
        stream_key: str,
        page: DiscordPage,
        *,
        terminal_reason: str | None,
    ) -> int:
        state = self._checkpoint["streams"].setdefault(
            stream_key,
            self._new_page_stream_state(),
        )
        self._normalize_page_state(state)
        page_number = int(state.get("pages", 0)) + 1
        page_document_base = {
            "request": {"path": page.path, "params": dict(page.params)},
            "payload": page.raw_payload,
            "pagination": {
                "item_count": page.item_count,
                "next_cursor": page.next_cursor,
                "terminal_status": page.terminal_status,
                "diagnostic": page.diagnostic,
            },
        }
        directory = self._run_root / "pages" / stream_key
        _safe_mkdir(directory, self._run_root)
        path = directory / f"{page_number:06d}.json"
        digest, page_document = _land_or_adopt_page(
            path,
            page_document_base,
            label=f"Discord raw page {stream_key}/{page_number:06d}",
        )
        hashes = state.setdefault("page_hashes", [])
        if page_number <= len(hashes):
            if hashes[page_number - 1] != digest:
                raise ValueError(f"Discord raw page ledger mismatch: {stream_key}/{page_number}")
        else:
            hashes.append(digest)
            state["pages"] = page_number
            state["items"] = int(state.get("items", 0)) + page.item_count
            state["page_states"].append(
                {
                    "processing_status": "landed",
                    "next_cursor": page.next_cursor,
                    "terminal_status": page.terminal_status,
                    "terminal_reason": terminal_reason,
                }
            )
        self._save_checkpoint()
        return page_number

    def _mark_page_processed(
        self,
        stream_key: str,
        page_number: int,
        payload: Any,
    ) -> None:
        state = self._checkpoint["streams"][stream_key]
        page_state = state["page_states"][page_number - 1]
        if page_state.get("processing_status") == "processed":
            return
        if page_state.get("processing_status") != "landed":
            raise ValueError(f"Discord page is not landed: {stream_key}/{page_number}")
        page_state["processing_status"] = "processed"
        state["processed_pages"] = int(state.get("processed_pages", 0)) + 1
        state["next_cursor"] = page_state.get("next_cursor")
        state["status"] = page_state.get("terminal_status") or "in_progress"
        state["terminal_reason"] = page_state.get("terminal_reason")
        self._update_stream_bounds(state, payload)
        self._save_checkpoint()

    def _update_stream_bounds(self, state: dict[str, Any], payload: Any) -> None:
        items: list[Any]
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict) and isinstance(payload.get("threads"), list):
            items = payload["threads"]
        elif isinstance(payload, dict) and isinstance(payload.get("items"), list):
            items = payload["items"]
        else:
            return
        objects = [item for item in items if isinstance(item, dict)]
        if not objects:
            return
        first_id = _object_id(objects[0])
        last_id = _object_id(objects[-1])
        first_time = _object_time(objects[0])
        last_time = _object_time(objects[-1])
        if state.get("first_id") is None and isinstance(first_id, str):
            state["first_id"] = first_id
        if isinstance(last_id, str):
            state["last_id"] = last_id
        if state.get("first_timestamp") is None and first_time is not None:
            state["first_timestamp"] = first_time
        if last_time is not None:
            state["last_timestamp"] = last_time

    def _stored_page_documents(
        self,
        stream_key: str,
    ) -> Iterator[dict[str, Any]]:
        state = self._checkpoint["streams"].get(stream_key, {})
        for index in range(1, int(state.get("pages", 0)) + 1):
            document, _ = _read_regular_json_bytes(
                self._run_root / "pages" / stream_key / f"{index:06d}.json",
                label=f"Discord raw page {stream_key}/{index:06d}",
            )
            if not isinstance(document, dict) or "payload" not in document:
                raise ValueError(f"Invalid Discord raw page: {stream_key}/{index:06d}")
            _page_fetched_at(
                document,
                label=f"Discord raw page {stream_key}/{index:06d}",
            )
            yield document

    def _stored_payloads(self, stream_key: str) -> Iterator[Any]:
        for document in self._stored_page_documents(stream_key):
            yield document["payload"]

    def _merge_thread(
        self,
        threads: dict[str, dict[str, Any]],
        metadata: dict[str, Any],
        source: str,
        *,
        expected_parent_id: str | None,
    ) -> None:
        thread_id = metadata.get("id")
        error = self._thread_metadata_error(
            metadata,
            self._guild_id,
            require_guild=False,
            expected_parent_id=expected_parent_id,
        )
        if error is not None:
            suffix = thread_id if _valid_snowflake(thread_id) else hashlib.sha256(
                _canonical_json_bytes(metadata, newline=False)
            ).hexdigest()[:16]
            self._set_simple_stream(f"thread_validation_{suffix}", "failed", error)
            return
        existing = threads.setdefault(
            thread_id,
            {"metadata": deepcopy(metadata), "sources": set()},
        )
        existing["sources"].add(source)
        if len(metadata) > len(existing["metadata"]):
            existing["metadata"] = deepcopy(metadata)

    def _collect_message_assets(
        self,
        message: dict[str, Any],
        channel_id: str,
        stream_key: str,
        *,
        excluded_logical_keys: set[str] | None = None,
    ) -> None:
        message_id = message.get("id")
        if not _valid_snowflake(message_id):
            return
        candidates: list[dict[str, Any]] = []
        attachments = message.get("attachments")
        if isinstance(attachments, list):
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                attachment_id = attachment.get("id")
                url = attachment.get("url")
                if not isinstance(attachment_id, str) or not attachment_id:
                    continue
                if (
                    not isinstance(url, str)
                    or urlsplit(url).scheme.lower() not in {"http", "https"}
                ):
                    continue
                candidates.append(
                    {
                        "logical_key": f"{message_id}:attachment:{attachment_id}",
                        "kind": "attachment",
                        "field": "attachment",
                        "url": url,
                        "declared_metadata": deepcopy(attachment),
                        "declared_content_type": _normalized_mime(
                            attachment.get("content_type")
                        ),
                        "identity_metadata": _attachment_identity_metadata(
                            attachment
                        ),
                    }
                )
        source = {
            "message_id": message_id,
            "channel_id": channel_id,
            "stream": stream_key,
        }
        for candidate in candidates:
            if (
                excluded_logical_keys is not None
                and candidate["logical_key"] in excluded_logical_keys
            ):
                continue
            self._collect_asset(candidate, source)

    def _collect_asset(self, candidate: dict[str, Any], source: dict[str, Any]) -> None:
        logical_key = candidate["logical_key"]
        observation_url = candidate.pop(
            "_observation_url",
            candidate.get("url"),
        )
        _, proxy_field, _ = discord_media_field_descriptor(
            candidate.get("kind"),
            candidate.get("field"),
        )
        observation_proxy_url = candidate.pop(
            "_observation_proxy_url",
            candidate["declared_metadata"].get(proxy_field),
        )
        candidate_urls = candidate.setdefault("candidate_urls", [candidate["url"]])
        if not isinstance(candidate_urls, list) or not candidate_urls:
            raise ValueError(f"Discord asset has no candidate URL: {logical_key}")
        candidate["url"] = candidate_urls[0]
        observation = {
            "source": deepcopy(source),
            "metadata": deepcopy(candidate["declared_metadata"]),
            "url": observation_url,
            "proxy_url": observation_proxy_url,
        }
        existing = self._asset_records.get(logical_key)
        if existing is not None:
            validate_resolution_attempt_history(
                existing,
                context=self._resolution_context,
            )
            if (
                existing.get("kind") != candidate.get("kind")
                or existing.get("field") != candidate.get("field")
            ):
                raise ValueError(f"Discord asset logical identity changed: {logical_key}")
            observations = existing.setdefault("observations", [])
            if observation not in observations:
                observations.append(observation)
            if source not in existing.get("sources", []):
                existing.setdefault("sources", []).append(source)
                existing["sources"] = sorted(
                    existing["sources"],
                    key=_asset_source_sort_key,
                )
            if existing.get("identity_metadata") != candidate["identity_metadata"]:
                self._clear_youtube_embed_player_reference(existing)
                attempt_history = existing.get("attempt_history", [])
                if not isinstance(attempt_history, list):
                    raise ValueError("Discord asset attempt history is invalid")
                tail = attempt_history[-1] if attempt_history else None
                if isinstance(tail, dict) and tail.get("status") in {
                    "in_progress",
                    "interrupted",
                }:
                    tail["status"] = "failed"
                    tail["terminal_reason"] = "logical_identity_conflict"
                    tail["failure_detail"] = None
                existing["status"] = "failed"
                existing["terminal_reason"] = "logical_identity_conflict"
                existing["failure_detail"] = None
                conflicts = existing.setdefault("identity_conflicts", [])
                conflict = {
                    "observation_index": observations.index(observation),
                    "observed_identity": candidate["identity_metadata"],
                }
                if conflict not in conflicts:
                    conflicts.append(conflict)
                validate_resolution_attempt_history(
                    existing,
                    context=self._resolution_context,
                )
                self._write_asset_record(existing)
                return
            if existing.get("terminal_reason") == "logical_identity_conflict":
                self._write_asset_record(existing)
                return
            observed_urls = existing.setdefault("observed_urls", [existing["url"]])
            previous_candidates = existing.setdefault(
                "candidate_urls",
                [existing["url"]],
            )
            candidates_changed = previous_candidates != candidate_urls
            for candidate_url in candidate_urls:
                if candidate_url not in observed_urls:
                    observed_urls.append(candidate_url)
            retryable = self._asset_is_retryable(
                existing,
                url_changed=candidates_changed,
            )
            if candidates_changed and self._asset_has_pending_tail(existing):
                if self._bytes is not None:
                    self._download_asset_candidates(existing)
                    if existing.get("status") == "complete":
                        return
                else:
                    self._finalize_pending_tail(
                        existing,
                        terminal_reason="byte_transport_unavailable",
                    )
                    retryable = True
                if self._bytes is not None:
                    retryable = self._asset_is_retryable(
                        existing,
                        url_changed=True,
                    )
            if existing.get("status") == "complete" or (
                existing.get("status") in _COVERED_ASSET_STATUSES
                and not retryable
            ):
                self._write_asset_record(existing)
                return
            if (
                candidates_changed
                and retryable
                and not self._asset_candidates_have_executable_action(
                    existing,
                    candidate_urls,
                )
            ):
                self._write_asset_record(existing)
                return
            if candidates_changed and retryable:
                existing["candidate_urls"] = list(candidate_urls)
                existing["url"] = candidate_urls[0]
                existing["declared_metadata"] = candidate["declared_metadata"]
                existing["declared_content_type"] = candidate["declared_content_type"]
                self._clear_youtube_embed_player_reference(existing)
                self._reset_resolution_outcome(existing)
                existing.pop("failure_detail", None)
                existing["status"] = "in_progress"
                existing["terminal_reason"] = "candidate_urls_changed"
            if retryable and candidates_changed:
                self._download_asset_candidates(existing)
            else:
                self._write_asset_record(existing)
                if retryable:
                    self._download_asset_candidates(existing)
            return

        record = {
            "schema_version": _ASSET_RECORD_SCHEMA_VERSION,
            **candidate,
            "sources": [source],
            "observations": [observation],
            "identity_conflicts": [],
            "observed_urls": list(candidate_urls),
            "attempt_history": [],
            "status": "not_requested" if not self._download_assets else "in_progress",
            "terminal_reason": "asset_download_disabled" if not self._download_assets else None,
            "http_content_type": None,
            "http_content_length": None,
            "actual_bytes": 0,
            "sha256": None,
            "blob_path": None,
        }
        self._asset_records[logical_key] = record
        self._write_asset_record(record)
        if not self._download_assets:
            return
        if self._bytes is None:
            record["status"] = "failed"
            record["terminal_reason"] = "byte_transport_unavailable"
            self._write_asset_record(record)
            return

        self._download_asset_candidates(record)

    def _retry_pending_assets(self) -> None:
        if not self._download_assets:
            return
        for logical_key in sorted(self._asset_records):
            record = self._asset_records[logical_key]
            if self._asset_is_retryable(record, url_changed=False):
                self._download_asset_candidates(record)

    def _retry_pending_assets_only(self) -> None:
        if not self._download_assets:
            return
        for logical_key in sorted(self._asset_records):
            record = self._asset_records[logical_key]
            if self._asset_has_pending_tail(record):
                self._download_asset_candidates(record)

    def _retry_covered_asset_fallbacks(self) -> None:
        if not self._download_assets:
            return
        for logical_key in sorted(self._asset_records):
            record = self._asset_records[logical_key]
            if record.get("status") == "complete" or record.get(
                "terminal_reason"
            ) in _HARD_ASSET_FAILURE_REASONS:
                continue
            baseline = self._best_covered_asset_baseline(
                record,
                allow_pending_tail=False,
            )
            if baseline is None:
                continue
            candidate_urls = record.get("candidate_urls", [record.get("url")])
            if not isinstance(candidate_urls, list) or any(
                not isinstance(candidate_url, str) or not candidate_url
                for candidate_url in candidate_urls
            ):
                raise ValueError("Discord asset candidate URL ledger is invalid")
            if self._asset_candidates_have_executable_action(
                record,
                candidate_urls,
            ):
                self._download_asset_candidates(record)
            elif record.get("status") not in _COVERED_ASSET_STATUSES:
                self._apply_covered_asset_baseline(record, baseline)
                self._write_asset_record(record)

    def _asset_has_pending_tail(self, record: Mapping[str, Any]) -> bool:
        if record.get("status") == "in_progress" and record.get(
            "terminal_reason"
        ) in {None, "interrupted"}:
            return True
        candidate_urls = record.get("candidate_urls", [record.get("url")])
        if not isinstance(candidate_urls, list):
            raise ValueError("Discord asset candidate URL ledger is invalid")
        return any(
            isinstance(candidate_url, str)
            and reusable_resolution_attempt_number(record, candidate_url) is not None
            for candidate_url in candidate_urls
        )

    def _finalize_pending_tail(
        self,
        record: dict[str, Any],
        *,
        terminal_reason: str,
    ) -> None:
        validate_resolution_attempt_history(
            record,
            context=self._resolution_context,
        )
        attempt_history = record.get("attempt_history", [])
        tail = (
            attempt_history[-1]
            if isinstance(attempt_history, list) and attempt_history
            else None
        )
        if tail is None:
            if record.get("status") != "in_progress":
                raise ValueError("Discord asset pending tail is invalid")
        elif not isinstance(tail, dict) or tail.get("status") not in {
            "in_progress",
            "interrupted",
        }:
            raise ValueError("Discord asset pending tail is invalid")
        else:
            tail["status"] = "failed"
            tail["terminal_reason"] = terminal_reason
            tail["failure_detail"] = None
        record["status"] = "failed"
        record["terminal_reason"] = terminal_reason
        record["failure_detail"] = None
        validate_resolution_attempt_history(
            record,
            context=self._resolution_context,
        )

    def _download_asset_candidates(self, record: dict[str, Any]) -> None:
        candidate_urls = record.get("candidate_urls", [record.get("url")])
        if not isinstance(candidate_urls, list) or any(
            not isinstance(candidate_url, str) or not candidate_url
            for candidate_url in candidate_urls
        ):
            raise ValueError("Discord asset candidate URL ledger is invalid")
        validate_resolution_attempt_history(
            record,
            context=self._resolution_context,
        )
        initial_record_bytes = _canonical_json_bytes(record)
        outcome_fields = (
            "url",
            "status",
            "terminal_reason",
            "http_content_type",
            "http_content_length",
            "actual_bytes",
            "sha256",
            "blob_path",
        )
        best_reference: dict[str, Any] | None = None
        covered_baseline = self._best_covered_asset_baseline(
            record,
            allow_pending_tail=True,
        )

        pending_selection: tuple[str, int, str] | None = None
        current_url = record.get("url")
        attempt_history = record.get("attempt_history", [])
        if (
            record.get("status") == "in_progress"
            and record.get("terminal_reason") in {None, "interrupted"}
            and isinstance(current_url, str)
            and current_url in candidate_urls
            and isinstance(attempt_history, list)
            and attempt_history
        ):
            resume_attempt_number = reusable_resolution_attempt_number(
                record,
                current_url,
            )
            if resume_attempt_number is not None:
                pending_selection = (
                    current_url,
                    resume_attempt_number,
                    "typed",
                )
            else:
                tail = attempt_history[-1]
                if (
                    isinstance(tail, Mapping)
                    and tail.get("url") == current_url
                    and tail.get("status") in {"in_progress", "interrupted"}
                ):
                    reusable_in_place = _is_reusable_generic_pending_attempt(
                        tail
                    )
                    pending_selection = (
                        current_url,
                        len(attempt_history),
                        "generic_reuse" if reusable_in_place else "generic_append",
                    )
        if pending_selection is None:
            for candidate_url in candidate_urls:
                resume_attempt_number = reusable_resolution_attempt_number(
                    record,
                    candidate_url,
                )
                if resume_attempt_number is not None:
                    pending_selection = (
                        candidate_url,
                        resume_attempt_number,
                        "typed",
                    )
                    break
        if pending_selection is not None:
            pending_url, pending_attempt_number, pending_mode = pending_selection
            pending_key = (record["logical_key"], pending_url)
            if pending_key in self._attempted_asset_urls:
                return
            if self._bytes is None:
                if pending_mode.startswith("generic_"):
                    self._finalize_pending_tail(
                        record,
                        terminal_reason="byte_transport_unavailable",
                    )
                    self._write_asset_record(record)
                return
            record["url"] = pending_url
            if pending_mode == "generic_append":
                self._finalize_pending_tail(
                    record,
                    terminal_reason="interrupted",
                )
                self._write_asset_record(record)
                self._download_asset(record, defer_terminal_commit=True)
            else:
                self._download_asset(
                    record,
                    resume_attempt_number=(
                        pending_attempt_number
                        if pending_mode == "typed"
                        else None
                    ),
                    resume_untyped_attempt_number=(
                        pending_attempt_number
                        if pending_mode == "generic_reuse"
                        else None
                    ),
                    defer_terminal_commit=True,
                )
            if record.get("status") in {"complete", "captured_with_warning"}:
                self._write_asset_record(record)
                return
            if record.get("status") == "reference_only":
                best_reference = {
                    field: deepcopy(record.get(field)) for field in outcome_fields
                }
            elif not _supports_candidate_fallback(record.get("terminal_reason")):
                self._write_asset_record(record)
                return

        typed_retry_urls: list[str] = []
        legacy_retry_urls: list[str] = []
        retry_metadata_by_url: dict[str, Mapping[str, object]] = {}
        for candidate_url in candidate_urls:
            retry_metadata = next_resolution_retry_metadata(
                record,
                candidate_url,
                context=self._resolution_context,
            )
            if retry_metadata is None:
                continue
            retry_metadata_by_url[candidate_url] = retry_metadata
            if retry_metadata.get("retry_trigger") == RESOLUTION_RETRY_TRIGGER:
                typed_retry_urls.append(candidate_url)
            else:
                legacy_retry_urls.append(candidate_url)
        prioritized_urls = list(typed_retry_urls)
        if not typed_retry_urls:
            prioritized_urls.extend(legacy_retry_urls)
        else:
            for candidate_url in legacy_retry_urls:
                retry_metadata_by_url.pop(candidate_url, None)
        prioritized_urls.extend(
            candidate_url
            for candidate_url in candidate_urls
            if candidate_url not in prioritized_urls
        )
        if covered_baseline is not None:
            baseline_url = covered_baseline["url"]
            prioritized_urls = [
                candidate_url
                for candidate_url in prioritized_urls
                if candidate_url != baseline_url
            ]

        for candidate_url in prioritized_urls:
            if (
                covered_baseline is not None
                and candidate_url == covered_baseline["url"]
            ):
                continue
            resume_attempt_number = reusable_resolution_attempt_number(
                record,
                candidate_url,
            )
            retry_metadata = retry_metadata_by_url.get(candidate_url)
            latest = next(
                (
                    attempt
                    for attempt in reversed(record.get("attempt_history", []))
                    if attempt.get("url") == candidate_url
                ),
                None,
            )
            if (
                candidate_url == record.get("url")
                and record.get("status") == "in_progress"
                and record.get("terminal_reason") == "interrupted"
            ):
                latest = None
            if resume_attempt_number is not None or retry_metadata is not None:
                if self._bytes is None:
                    return
                if (record["logical_key"], candidate_url) in self._attempted_asset_urls:
                    continue
                record["url"] = candidate_url
                self._download_asset(
                    record,
                    resume_attempt_number=resume_attempt_number,
                    retry_metadata=retry_metadata,
                    defer_terminal_commit=True,
                )
            elif latest is not None:
                latest_reason = latest.get("terminal_reason")
                latest_status = latest.get("status")
                if latest_status in {"complete", "captured_with_warning"}:
                    record.update(
                        {
                            field: deepcopy(latest.get(field))
                            for field in outcome_fields
                        }
                    )
                    self._write_asset_record(record)
                    return
                if latest_status == "reference_only":
                    best_reference = {
                        field: deepcopy(latest.get(field))
                        for field in outcome_fields
                    }
                    continue
                if has_resolution_attempt_history(record, candidate_url):
                    if _supports_candidate_fallback(latest_reason):
                        continue
                    break
                if not (
                    latest_status in {"in_progress", "interrupted"}
                    or latest_reason in _RETRYABLE_ASSET_REASONS
                ):
                    if _supports_candidate_fallback(latest_reason):
                        continue
                    break
                if (record["logical_key"], candidate_url) in self._attempted_asset_urls:
                    continue
                record["url"] = candidate_url
                self._download_asset(record, defer_terminal_commit=True)
            else:
                if has_resolution_attempt_history(record, candidate_url):
                    break
                if (record["logical_key"], candidate_url) in self._attempted_asset_urls:
                    continue
                record["url"] = candidate_url
                self._download_asset(record, defer_terminal_commit=True)
            if record.get("status") in {"complete", "captured_with_warning"}:
                self._write_asset_record(record)
                return
            if record.get("status") == "reference_only":
                best_reference = {
                    field: deepcopy(record.get(field)) for field in outcome_fields
                }
                continue
            if not _supports_candidate_fallback(record.get("terminal_reason")):
                break
        restored_baseline = self._best_covered_asset_baseline(
            record,
            allow_pending_tail=False,
        )
        if restored_baseline is not None:
            self._apply_covered_asset_baseline(record, restored_baseline)
        elif best_reference is not None:
            record.update(best_reference)
        if _canonical_json_bytes(record) != initial_record_bytes:
            self._write_asset_record(record)

    def _asset_is_retryable(
        self,
        record: Mapping[str, Any],
        *,
        url_changed: bool = False,
    ) -> bool:
        status = record.get("status")
        reason = record.get("terminal_reason")
        if status == "complete":
            return False
        if status == "captured_with_warning" and not url_changed:
            return False
        if reason in _HARD_ASSET_FAILURE_REASONS:
            return False
        if url_changed and status in {
            "failed",
            "captured_with_warning",
            "reference_only",
            "in_progress",
        }:
            return True
        candidate_urls = record.get("candidate_urls", [record.get("url")])
        if not isinstance(candidate_urls, list):
            raise ValueError("Discord asset candidate URL ledger is invalid")
        typed_candidates = {
            candidate_url
            for candidate_url in candidate_urls
            if isinstance(candidate_url, str)
            and has_resolution_attempt_history(record, candidate_url)
        }
        if typed_candidates:
            for candidate_url in candidate_urls:
                if not isinstance(candidate_url, str):
                    continue
                if candidate_url in typed_candidates:
                    if (
                        reusable_resolution_attempt_number(record, candidate_url)
                        is not None
                        or next_resolution_retry_metadata(
                            record,
                            candidate_url,
                            context=self._resolution_context,
                        )
                        is not None
                    ):
                        return True
                    continue
                latest = next(
                    (
                        attempt
                        for attempt in reversed(record.get("attempt_history", []))
                        if isinstance(attempt, Mapping)
                        and attempt.get("url") == candidate_url
                    ),
                    None,
                )
                if latest is None:
                    if url_changed or _supports_candidate_fallback(reason):
                        return True
                elif latest.get("status") in {"in_progress", "interrupted"} or (
                    latest.get("terminal_reason") in _RETRYABLE_ASSET_REASONS
                ):
                    return True
            return False
        if status == "in_progress" or (
            status == "failed" and reason in _RETRYABLE_ASSET_REASONS
        ):
            return True
        if status != "failed":
            return False
        return any(
            isinstance(candidate_url, str)
            and (
                reusable_resolution_attempt_number(record, candidate_url)
                is not None
                or next_resolution_retry_metadata(
                    record,
                    candidate_url,
                    context=self._resolution_context,
                )
                is not None
            )
            for candidate_url in candidate_urls
        )

    def _asset_candidates_have_executable_action(
        self,
        record: Mapping[str, Any],
        candidate_urls: Sequence[str],
    ) -> bool:
        attempts = record.get("attempt_history", [])
        if not isinstance(attempts, list):
            raise ValueError("Discord asset attempt history is invalid")
        for candidate_url in candidate_urls:
            if (record["logical_key"], candidate_url) in self._attempted_asset_urls:
                continue
            if reusable_resolution_attempt_number(record, candidate_url) is not None:
                if self._bytes is not None:
                    return True
                continue
            if (
                next_resolution_retry_metadata(
                    record,
                    candidate_url,
                    context=self._resolution_context,
                )
                is not None
            ):
                if self._bytes is not None:
                    return True
                continue
            latest = next(
                (
                    attempt
                    for attempt in reversed(attempts)
                    if isinstance(attempt, Mapping)
                    and attempt.get("url") == candidate_url
                ),
                None,
            )
            if latest is None:
                if not has_resolution_attempt_history(record, candidate_url):
                    return True
                continue
            if has_resolution_attempt_history(record, candidate_url):
                continue
            if latest.get("status") in {"in_progress", "interrupted"} or (
                latest.get("terminal_reason") in _RETRYABLE_ASSET_REASONS
            ):
                return True
        return False

    def _best_covered_asset_baseline(
        self,
        record: Mapping[str, Any],
        *,
        allow_pending_tail: bool,
    ) -> dict[str, Any] | None:
        if record.get("terminal_reason") in _HARD_ASSET_FAILURE_REASONS:
            return None
        attempts = record.get("attempt_history", [])
        observations = record.get("observations", [])
        sources = record.get("sources", [])
        baseline_identity = record.get("identity_metadata")
        if (
            not isinstance(attempts, list)
            or not isinstance(observations, list)
            or not isinstance(sources, list)
            or not isinstance(baseline_identity, Mapping)
        ):
            return None

        candidates: list[tuple[int, int, dict[str, Any]]] = []
        for attempt_number, attempt in enumerate(attempts, start=1):
            if not isinstance(attempt, Mapping):
                continue
            url = attempt.get("url")
            status = attempt.get("status")
            if not isinstance(url, str) or status not in {
                "captured_with_warning",
                "reference_only",
                "failed",
            }:
                continue
            if (
                status == "failed"
                and attempt.get("terminal_reason") == "unsafe_media_url"
            ):
                observation = discord_media_reference_source_observation(
                    record,
                    url,
                )
            else:
                observation = next(
                    (
                        item
                        for item in reversed(observations)
                        if isinstance(item, Mapping)
                        and item.get("source") in sources
                        and (
                            item.get("url") == url
                            or item.get("proxy_url") == url
                        )
                        and isinstance(item.get("metadata"), Mapping)
                        and discord_media_identity_metadata(
                            record.get("kind"),
                            item["metadata"],
                            field=record.get("field"),
                            schema_version=record.get("schema_version"),
                        )
                        == baseline_identity
                    ),
                    None,
                )
            if observation is None:
                continue
            metadata = deepcopy(dict(observation["metadata"]))
            baseline = {
                "url": url,
                "candidate_urls": [url],
                "declared_metadata": metadata,
                "declared_content_type": (
                    None
                    if record.get("kind") == "sticker"
                    else normalized_discord_media_mime(
                        metadata.get("content_type")
                    )
                ),
                "identity_metadata": deepcopy(dict(baseline_identity)),
                "status": status,
                "terminal_reason": attempt.get("terminal_reason"),
                "http_content_type": attempt.get("http_content_type"),
                "http_content_length": attempt.get("http_content_length"),
                "actual_bytes": attempt.get("actual_bytes"),
                "sha256": attempt.get("sha256"),
                "blob_path": attempt.get("blob_path"),
            }
            if status == "captured_with_warning":
                candidates.append((2, attempt_number, baseline))
                continue
            if status == "reference_only":
                candidates.append((1, attempt_number, baseline))
                continue
            if attempt.get("terminal_reason") != "unsafe_media_url":
                continue
            provisional = {**deepcopy(dict(record)), **baseline}
            provenance = _youtube_embed_player_attempt_provenance(
                provisional,
                source_url=url,
                failed_attempt_number=attempt_number,
                allow_pending_tail=allow_pending_tail,
            )
            if provenance is None:
                continue
            baseline.update(
                {
                    "status": "reference_only",
                    "terminal_reason": _YOUTUBE_EMBED_PLAYER_REFERENCE_REASON,
                    "http_content_type": None,
                    "http_content_length": None,
                    "actual_bytes": 0,
                    "sha256": None,
                    "blob_path": None,
                    "reference_provenance": provenance,
                }
            )
            candidates.append((1, attempt_number, baseline))
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item[0], item[1]))[2]

    def _apply_covered_asset_baseline(
        self,
        record: dict[str, Any],
        baseline: Mapping[str, Any],
    ) -> None:
        active_candidates = record.get("candidate_urls", [])
        if not isinstance(active_candidates, list) or any(
            not isinstance(candidate_url, str) or not candidate_url
            for candidate_url in active_candidates
        ):
            raise ValueError("Discord asset candidate URL ledger is invalid")
        baseline_url = baseline.get("url")
        if not isinstance(baseline_url, str) or not baseline_url:
            raise ValueError("Discord covered asset baseline URL is invalid")
        merged_candidates: list[str] = []
        for candidate_url in (baseline_url, *active_candidates):
            if candidate_url not in merged_candidates:
                merged_candidates.append(candidate_url)
        for field in (
            "url",
            "declared_metadata",
            "declared_content_type",
            "identity_metadata",
            "status",
            "terminal_reason",
            "http_content_type",
            "http_content_length",
            "actual_bytes",
            "sha256",
            "blob_path",
        ):
            record[field] = deepcopy(baseline[field])
        record["candidate_urls"] = merged_candidates
        record.pop("failure_detail", None)
        if "reference_provenance" in baseline:
            record["reference_provenance"] = deepcopy(
                baseline["reference_provenance"]
            )
        else:
            record.pop("reference_provenance", None)
        validate_resolution_attempt_history(
            record,
            context=self._resolution_context,
        )

    def _bind_asset_candidate_observation(
        self,
        record: dict[str, Any],
    ) -> None:
        candidate_url = record.get("url")
        if not isinstance(candidate_url, str) or not candidate_url:
            raise ValueError("Discord asset candidate observation is invalid")
        metadata = discord_media_candidate_observation_metadata(
            record,
            candidate_url,
        )
        if metadata is None:
            raise ValueError("Discord asset candidate observation is missing")
        record["declared_metadata"] = metadata
        record["declared_content_type"] = (
            None
            if record.get("kind") == "sticker"
            else normalized_discord_media_mime(metadata.get("content_type"))
        )

    def _download_asset(
        self,
        record: dict[str, Any],
        *,
        resume_attempt_number: int | None = None,
        resume_untyped_attempt_number: int | None = None,
        retry_metadata: Mapping[str, object] | None = None,
        defer_terminal_commit: bool = False,
    ) -> None:
        if not isinstance(defer_terminal_commit, bool):
            raise ValueError("Discord media terminal commit option is invalid")
        if record.get("terminal_reason") == "logical_identity_conflict":
            return
        typed_selection = (
            resume_attempt_number is not None or retry_metadata is not None
        )
        untyped_replay = resume_untyped_attempt_number is not None
        if untyped_replay and typed_selection:
            raise ValueError("Discord media replay selection is ambiguous")
        if (
            not typed_selection
            and not untyped_replay
            and has_resolution_attempt_history(record, record["url"])
        ):
            raise ValueError(
                "Discord typed media recovery requires a committed sequence"
            )
        attempt_key = (record["logical_key"], record["url"])
        if attempt_key in self._attempted_asset_urls:
            return
        if self._bytes is None:
            if typed_selection or untyped_replay:
                return
            self._bind_asset_candidate_observation(record)
            record["status"] = "failed"
            record["terminal_reason"] = "byte_transport_unavailable"
            self._write_asset_record(record)
            return
        attempt_history = record.setdefault("attempt_history", [])
        if not isinstance(attempt_history, list):
            raise ValueError("Discord asset attempt history is invalid")
        append_attempt = False
        if resume_attempt_number is not None:
            if retry_metadata is not None or (
                isinstance(resume_attempt_number, bool)
                or not isinstance(resume_attempt_number, int)
                or reusable_resolution_attempt_number(record, record["url"])
                != resume_attempt_number
            ):
                raise ValueError("Discord media resolution replay is invalid")
            attempt = attempt_history[resume_attempt_number - 1]
            if not isinstance(attempt, dict):
                raise ValueError("Discord media resolution replay is invalid")
        elif resume_untyped_attempt_number is not None:
            if (
                isinstance(resume_untyped_attempt_number, bool)
                or not isinstance(resume_untyped_attempt_number, int)
                or resume_untyped_attempt_number != len(attempt_history)
                or resume_untyped_attempt_number < 1
            ):
                raise ValueError("Discord generic media replay is invalid")
            attempt = attempt_history[resume_untyped_attempt_number - 1]
            if (
                not isinstance(attempt, dict)
                or attempt.get("url") != record["url"]
                or not _is_reusable_generic_pending_attempt(attempt)
            ):
                raise ValueError("Discord generic media replay is invalid")
        else:
            attempt = {
                "url": record["url"],
                "status": "in_progress",
                "terminal_reason": None,
                "http_content_type": None,
                "http_content_length": None,
                "actual_bytes": 0,
                "sha256": None,
                "blob_path": None,
            }
            if retry_metadata is not None:
                attempt.update(deepcopy(dict(retry_metadata)))
            append_attempt = True
        self._bind_asset_candidate_observation(record)
        self._attempted_asset_urls.add(attempt_key)
        if append_attempt:
            attempt_history.append(attempt)
        attempt.update(
            {
                "status": "in_progress",
                "terminal_reason": None,
                "http_content_type": None,
                "http_content_length": None,
                "actual_bytes": 0,
                "sha256": None,
                "blob_path": None,
            }
        )
        if "failure_detail" in attempt:
            attempt["failure_detail"] = None
        self._clear_youtube_embed_player_reference(record)
        record["status"] = "in_progress"
        record["terminal_reason"] = None
        record["http_content_type"] = None
        record["http_content_length"] = None
        record["actual_bytes"] = 0
        record["sha256"] = None
        record["blob_path"] = None
        self._commit_asset_record(record)

        temporary_path: Path | None = None
        actual_bytes = 0
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=".asset-",
                dir=self._run_root / "assets",
            )
            temporary_path = Path(name)
            digest = hashlib.sha256()
            with os.fdopen(descriptor, "wb") as output:
                with self._bytes.open_byte_stream(
                    record["url"],
                    chunk_size=self._chunk_size,
                ) as stream:
                    record["http_content_type"] = _normalized_mime(
                        getattr(stream, "content_type", None)
                    )
                    record["http_content_length"] = getattr(
                        stream, "content_length", None
                    )
                    for chunk in stream:
                        if not isinstance(chunk, bytes):
                            raise ValueError("Discord byte stream yielded a non-bytes chunk")
                        actual_bytes += len(chunk)
                        if actual_bytes > self._max_asset_bytes:
                            raise _AssetTooLarge
                        digest.update(chunk)
                        output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            record["actual_bytes"] = actual_bytes
            if (
                record.get("http_content_length") is not None
                and record["http_content_length"] != actual_bytes
            ):
                record["status"] = "failed"
                record["terminal_reason"] = "content_length_mismatch"
            elif actual_bytes == 0:
                record["status"] = "failed"
                record["terminal_reason"] = "download_failed_transient"
            else:
                record["sha256"] = digest.hexdigest()
                blob = self._promote_blob(
                    temporary_path,
                    record["sha256"],
                    record["http_content_type"],
                )
                temporary_path = None
                record["blob_path"] = blob.relative_to(self._run_root).as_posix()
                declared_mime = record.get("declared_content_type")
                actual_mime = record.get("http_content_type")
                mime_outcome = discord_media_mime_outcome(
                    record,
                    declared_mime,
                    actual_mime,
                )
                if mime_outcome is not None:
                    record["status"], record["terminal_reason"] = mime_outcome
                elif discord_declared_size_mismatch(record, actual_bytes):
                    record["status"] = "captured_with_warning"
                    record["terminal_reason"] = "declared_size_mismatch"
                else:
                    record["status"] = "complete"
                    record["terminal_reason"] = "downloaded"
        except _AssetTooLarge:
            record["status"] = "failed"
            record["terminal_reason"] = "size_limit_exceeded"
            record["actual_bytes"] = actual_bytes
        except DiscordMediaResolutionInvalidAnswer as exc:
            self._ensure_resolution_attempt_metadata(record, attempt)
            self._reset_resolution_outcome(record)
            record["status"] = "failed"
            record["terminal_reason"] = "media_resolution_invalid_answer"
            attempt["failure_detail"] = exc.reason_code
        except DiscordMediaResolutionError as exc:
            self._ensure_resolution_attempt_metadata(record, attempt)
            self._reset_resolution_outcome(record)
            record["status"] = "failed"
            attempt["failure_detail"] = exc.reason_code
            if exc.reason_code in TRANSIENT_RESOLUTION_DETAILS:
                record["terminal_reason"] = (
                    "media_resolution_retry_exhausted"
                    if attempt["resolution_retry_sequence"]
                    == MAX_RESOLUTION_RETRY_SEQUENCES
                    else "media_resolution_failed_transient"
                )
            else:
                record["terminal_reason"] = "media_resolution_unresolved"
        except DiscordMediaSecurityError:
            self._reset_resolution_outcome(record)
            record["status"] = "failed"
            record["terminal_reason"] = "unsafe_media_url"
            attempt["security_rejection"] = dict(
                FRESH_SECURITY_REJECTION_PROVENANCE
            )
        except DiscordAPIError as exc:
            record["status"] = "failed"
            if (
                exc.status_code is None
                or exc.status_code == 429
                or (
                    isinstance(exc.status_code, int)
                    and 500 <= exc.status_code < 600
                )
            ):
                record["terminal_reason"] = "download_failed_transient"
            else:
                record["terminal_reason"] = f"download_http_{exc.status_code}"
            record["actual_bytes"] = actual_bytes
        except (OSError, RuntimeError, ValueError):
            record["status"] = "failed"
            record["terminal_reason"] = "download_failed_transient"
            record["actual_bytes"] = actual_bytes
        except BaseException:
            record["status"] = "in_progress"
            record["terminal_reason"] = "interrupted"
            record["actual_bytes"] = actual_bytes
            if "resolution_retry_sequence" in attempt:
                self._reset_resolution_outcome(record)
                record["status"] = "in_progress"
                record["terminal_reason"] = "interrupted"
                attempt["failure_detail"] = None
            self._finish_asset_attempt(record, attempt, status="interrupted")
            self._write_asset_record(record)
            raise
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
        self._finish_asset_attempt(record, attempt, status=record["status"])
        self._reconcile_youtube_embed_player_reference(record)
        if not defer_terminal_commit:
            self._write_asset_record(record)

    def _ensure_resolution_attempt_metadata(
        self,
        record: Mapping[str, Any],
        attempt: dict[str, Any],
    ) -> None:
        if "resolution_retry_sequence" not in attempt:
            if has_resolution_attempt_history(record, str(attempt.get("url", ""))):
                raise ValueError(
                    "Discord media resolution sequence was not precommitted"
                )
            attempt["resolution_retry_sequence"] = 1
            attempt["policy_inputs_sha256"] = (
                self._resolution_context.policy_inputs_sha256
            )

    @staticmethod
    def _reset_resolution_outcome(record: dict[str, Any]) -> None:
        record["http_content_type"] = None
        record["http_content_length"] = None
        record["actual_bytes"] = 0
        record["sha256"] = None
        record["blob_path"] = None

    @staticmethod
    def _reconcile_youtube_embed_player_reference(
        record: dict[str, Any],
    ) -> bool:
        if (
            record.get("status") != "failed"
            or record.get("terminal_reason") != "unsafe_media_url"
        ):
            return False
        provenance = _youtube_embed_player_reference_provenance(record)
        if provenance is None:
            return False
        record["status"] = "reference_only"
        record["terminal_reason"] = _YOUTUBE_EMBED_PLAYER_REFERENCE_REASON
        record["reference_provenance"] = provenance
        return True

    @staticmethod
    def _clear_youtube_embed_player_reference(record: dict[str, Any]) -> bool:
        if (
            record.get("status") != "reference_only"
            or record.get("terminal_reason")
            != _YOUTUBE_EMBED_PLAYER_REFERENCE_REASON
        ):
            return False
        record.pop("reference_provenance", None)
        return True

    @staticmethod
    def _clear_stale_completed_youtube_embed_player_reference(
        record: dict[str, Any],
    ) -> bool:
        if not _is_stale_completed_youtube_embed_player_reference(record):
            return False
        record.pop("reference_provenance")
        return True

    def _finish_asset_attempt(
        self,
        record: dict[str, Any],
        attempt: dict[str, Any],
        *,
        status: str,
    ) -> None:
        attempt.update(
            {
                "status": status,
                "terminal_reason": record.get("terminal_reason"),
                "http_content_type": record.get("http_content_type"),
                "http_content_length": record.get("http_content_length"),
                "actual_bytes": record.get("actual_bytes"),
                "sha256": record.get("sha256"),
                "blob_path": record.get("blob_path"),
            }
        )

    def _promote_blob(
        self,
        temporary_path: Path,
        digest: str,
        content_type: str | None,
    ) -> Path:
        directory = self._run_root / "assets" / "sha256" / digest[:2]
        _safe_mkdir(directory, self._run_root)
        existing = sorted(directory.glob(f"{digest}.*"))
        if existing:
            existing_blob = existing[0]
            if existing_blob.is_symlink() or not existing_blob.is_file():
                raise ValueError("Discord content-addressed blob is unsafe")
            resolved = existing_blob.resolve(strict=True)
            fingerprint = _file_fingerprint(existing_blob)
            cached = self._blob_validation_cache.get(digest)
            if cached is not None and cached[0] != resolved:
                raise ValueError("Discord content-addressed blob identity is inconsistent")
            if cached != fingerprint and _sha256_file(existing_blob) != digest:
                raise ValueError("Discord content-addressed blob hash mismatch")
            verified = _file_fingerprint(existing_blob)
            if verified != fingerprint:
                raise ValueError("Discord content-addressed blob changed during validation")
            self._blob_validation_cache[digest] = verified
            temporary_path.unlink()
            _fsync_directory(temporary_path.parent)
            return existing_blob
        extension = _mime_extension(content_type)
        destination = directory / f"{digest}.{extension}"
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            if destination.is_symlink() or not destination.is_file():
                raise ValueError("Discord content-addressed blob is unsafe")
            resolved = destination.resolve(strict=True)
            fingerprint = _file_fingerprint(destination)
            cached = self._blob_validation_cache.get(digest)
            if cached is not None and cached[0] != resolved:
                raise ValueError("Discord content-addressed blob identity is inconsistent")
            if cached != fingerprint and _sha256_file(destination) != digest:
                raise ValueError("Discord content-addressed blob collision")
            verified = _file_fingerprint(destination)
            if verified != fingerprint:
                raise ValueError("Discord content-addressed blob changed during validation")
        else:
            _fsync_directory(destination.parent)
        temporary_path.unlink()
        _fsync_directory(temporary_path.parent)
        self._blob_validation_cache[digest] = _file_fingerprint(destination)
        return destination

    def _write_asset_record(self, record: dict[str, Any]) -> None:
        self._commit_asset_record(record)

    def _commit_asset_record(self, record: dict[str, Any]) -> None:
        filename = hashlib.sha256(record["logical_key"].encode("utf-8")).hexdigest()
        record_name = f"{filename}.json"
        content = _canonical_json_bytes(record)
        digest = _sha256_bytes(content)
        if not self._asset_ledger.prepare_commit(
            record["logical_key"], record_name, digest
        ):
            return
        _atomic_write_bytes(self._run_root / "asset-records" / record_name, content)
        self._asset_ledger.finish_commit(record["logical_key"], digest)

    def _write_asset_index(self) -> str:
        path = self._run_root / "asset-index.jsonl"
        if not self._asset_ledger.index_needs_write(path):
            return self._asset_ledger.bound_index_sha256(path)
        for _attempt in range(3):
            generation, rows = self._asset_ledger.index_snapshot()
            row_keys = [str(row["logical_key"]) for row in rows]
            if set(row_keys) != set(self._asset_records):
                self._asset_records = self._load_asset_records()
                continue

            def chunks() -> Iterator[bytes]:
                for row in rows:
                    logical_key = str(row["logical_key"])
                    expected_name = (
                        hashlib.sha256(logical_key.encode("utf-8")).hexdigest()
                        + ".json"
                    )
                    committed = row["committed_sha256"]
                    if (
                        row["record_name"] != expected_name
                        or not _valid_sha256(committed)
                        or row["pending_sha256"] is not None
                    ):
                        raise ValueError(
                            f"Discord asset index snapshot is invalid: {logical_key}"
                        )
                    content = _canonical_json_bytes(
                        self._asset_records[logical_key],
                        newline=True,
                    )
                    if _sha256_bytes(content) != committed:
                        raise ValueError(
                            f"Discord asset index record changed: {logical_key}"
                        )
                    yield content

            digest = _atomic_write_chunks(path, chunks())
            if self._asset_ledger.mark_index(
                digest,
                expected_generation=generation,
            ):
                return self._asset_ledger.bound_index_sha256(path)
            self._asset_records = self._load_asset_records()
        raise RuntimeError("Discord asset index changed repeatedly during publication")

    def _write_media_recovery_audit(
        self,
        *,
        asset_index_sha256: str,
    ) -> dict[str, Any]:
        audit = build_media_recovery_audit(
            run_id=self._checkpoint["run_id"],
            request_sha256=self._resolution_context.request_sha256,
            policy_inputs_sha256=self._resolution_context.policy_inputs_sha256,
            asset_index_sha256=asset_index_sha256,
            records=self._asset_records,
        )
        content = canonical_media_recovery_audit_bytes(audit)
        _atomic_write_bytes(
            self._run_root / MEDIA_RECOVERY_AUDIT_FILENAME,
            content,
        )
        return {"audit": audit, "sha256": _sha256_bytes(content)}

    def _write_message_reference_resolution_audit(self) -> dict[str, Any]:
        audit = build_message_reference_resolution_audit(
            run_root=self._run_root,
            checkpoint=self._checkpoint,
            run_id=self._checkpoint["run_id"],
            request_sha256=self._resolution_context.request_sha256,
        )
        return publish_message_reference_resolution_audit(
            run_root=self._run_root,
            audit=audit,
        )

    def _record_endpoint_error(self, stream_key: str, exc: DiscordAPIError) -> None:
        status_code = exc.status_code
        retryable = (
            status_code is None
            or status_code == 429
            or (isinstance(status_code, int) and 500 <= status_code < 600)
        )
        status = (
            "in_progress"
            if retryable
            else "blocked"
            if status_code == 403
            else "not_found"
            if status_code == 404
            else "failed"
        )
        reason = (
            "network_error"
            if status_code is None
            else "http_429"
            if status_code == 429
            else "http_5xx"
            if 500 <= status_code < 600
            else f"http_{status_code}"
        )
        state = self._checkpoint["streams"].setdefault(
            stream_key,
            {"pages": 0, "items": 0, "next_cursor": None, "page_hashes": []},
        )
        state["status"] = status
        state["terminal_reason"] = reason
        error = {
            "stream": stream_key,
            "status": status,
            "status_code": exc.status_code,
            "path": exc.path,
            "message": str(exc),
        }
        if error not in self._checkpoint["errors"]:
            self._checkpoint["errors"].append(error)
        self._save_checkpoint()

    def _set_simple_stream(
        self,
        key: str,
        status: str,
        reason: str,
        **evidence: Any,
    ) -> None:
        self._checkpoint["streams"][key] = {
            "status": status,
            "pages": 0,
            "items": 0,
            "next_cursor": None,
            "page_hashes": [],
            "terminal_reason": reason,
            **evidence,
        }
        self._save_checkpoint()

    def _save_checkpoint(self) -> None:
        _atomic_write_json(self._checkpoint_path, self._checkpoint)

    def _write_derived_outputs(self, *, interrupted: bool) -> dict[str, Any]:
        if self._asset_ledger.has_pending():
            self._asset_records = self._load_asset_records()
        self._save_checkpoint()
        asset_index_sha256 = self._write_asset_index()
        self._asset_ledger.checkpoint()
        audit_result = self._write_media_recovery_audit(
            asset_index_sha256=asset_index_sha256,
        )
        reference_audit_descriptor = (
            self._write_message_reference_resolution_audit()
        )
        errors = b"".join(
            _canonical_json_bytes(error, newline=True)
            for error in sorted(
                self._checkpoint["errors"],
                key=lambda item: (str(item.get("stream")), str(item.get("status_code"))),
            )
        )
        _atomic_write_bytes(self._run_root / "errors.jsonl", errors)
        streams = deepcopy(self._checkpoint["streams"])
        streams_complete = bool(streams) and all(
            state.get("status") == _COMPLETE_STREAM_STATUS
            for state in streams.values()
        )
        expected_evidence_pages = 0
        evidence_descriptors: list[dict[str, Any]] = []
        for stream_key, stream_state in streams.items():
            if not (
                stream_key.startswith("messages_")
                or stream_key.startswith("pins_")
            ):
                continue
            page_states = stream_state.get("page_states", [])
            if not isinstance(page_states, list):
                continue
            expected_evidence_pages += len(page_states)
            for page_state in page_states:
                if not isinstance(page_state, dict):
                    continue
                descriptor = page_state.get("message_evidence")
                if isinstance(descriptor, dict):
                    evidence_descriptors.append(descriptor)
        evidence_severity = {"error": 0, "warning": 0, "info": 0}
        for descriptor in evidence_descriptors:
            by_severity = descriptor.get("diagnostics_by_severity", {})
            if isinstance(by_severity, dict):
                for severity in evidence_severity:
                    value = by_severity.get(severity, 0)
                    if isinstance(value, int) and not isinstance(value, bool):
                        evidence_severity[severity] += value
        validation_streams = [
            state
            for key, state in streams.items()
            if (
                key.startswith("messages_") or key.startswith("pins_")
            )
            and key.endswith(("_item_validation", "_order_validation"))
        ]
        invalid_items = sum(
            int(state.get("invalid_items", 0))
            for state in validation_streams
        )
        validation_failed = any(
            state.get("status") != "complete" for state in validation_streams
        )
        message_evidence = {
            "status": "not_applicable",
            "pages": len(evidence_descriptors),
            "expected_pages": expected_evidence_pages,
            "root_messages": sum(
                int(item.get("root_messages", 0)) for item in evidence_descriptors
            ),
            "partial_messages": sum(
                int(item.get("partial_messages", 0)) for item in evidence_descriptors
            ),
            "nodes": sum(int(item.get("nodes", 0)) for item in evidence_descriptors),
            "media_occurrences": sum(
                int(item.get("media_occurrences", 0))
                for item in evidence_descriptors
            ),
            "references": sum(
                int(item.get("references", 0)) for item in evidence_descriptors
            ),
            "diagnostics": sum(
                int(item.get("diagnostics", 0)) for item in evidence_descriptors
            ),
            "diagnostics_by_severity": evidence_severity,
            "invalid_items": invalid_items,
        }
        if expected_evidence_pages:
            raw_message_evidence_complete = (
                len(evidence_descriptors) == expected_evidence_pages
                and message_evidence["partial_messages"] == 0
                and evidence_severity["error"] == 0
                and not validation_failed
            )
            if not raw_message_evidence_complete:
                message_evidence["status"] = "partial"
            elif evidence_severity["warning"]:
                message_evidence["status"] = "complete_with_warnings"
            else:
                message_evidence["status"] = "complete"
        else:
            raw_message_evidence_complete = not validation_failed
            if validation_failed:
                message_evidence["status"] = "partial"
        reference_counts = reference_audit_descriptor["counts"]
        raw_reference_severity = reference_counts.get(
            "raw_diagnostics_by_severity"
        )
        effective_reference_severity = reference_counts.get(
            "effective_diagnostics_by_severity"
        )
        raw_reference_codes = reference_counts.get(
            "raw_diagnostic_codes_by_severity"
        )
        effective_reference_codes = reference_counts.get(
            "effective_diagnostic_codes_by_severity"
        )
        if (
            reference_counts.get("raw_error_diagnostics")
            != evidence_severity["error"]
            or reference_counts.get("raw_partial_messages")
            != message_evidence["partial_messages"]
            or raw_reference_severity != evidence_severity
            or not _valid_diagnostic_code_counts(
                raw_reference_codes,
                evidence_severity,
            )
            or not _valid_diagnostic_code_counts(
                effective_reference_codes,
                effective_reference_severity,
            )
        ):
            raise ValueError(
                "Discord reference audit counts differ from message evidence"
            )
        effective_error_diagnostics = reference_counts.get(
            "effective_error_diagnostics"
        )
        effective_partial_messages = reference_counts.get(
            "effective_partial_messages"
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (
                effective_error_diagnostics,
                effective_partial_messages,
            )
        ):
            raise ValueError("Discord reference audit effective counts are invalid")
        message_evidence_complete = (
            len(evidence_descriptors) == expected_evidence_pages
            and effective_partial_messages == 0
            and effective_error_diagnostics == 0
            and not validation_failed
        )
        message_evidence["effective_partial_messages"] = (
            effective_partial_messages
        )
        if (
            not isinstance(effective_reference_severity, dict)
            or effective_reference_severity.get("error")
            != effective_error_diagnostics
        ):
            raise ValueError("Discord reference audit effective severity is invalid")
        message_evidence["diagnostic_codes_by_severity"] = deepcopy(
            raw_reference_codes
        )
        message_evidence["effective_diagnostics_by_severity"] = deepcopy(
            effective_reference_severity
        )
        message_evidence["effective_diagnostic_codes_by_severity"] = deepcopy(
            effective_reference_codes
        )
        message_evidence["effective_status"] = (
            "partial"
            if not message_evidence_complete
            else "complete_with_warnings"
            if evidence_severity["warning"]
            else "complete"
        )
        if self._download_assets:
            media_complete = all(
                record.get("status") in _COVERED_ASSET_STATUSES
                for record in self._asset_records.values()
            )
            media_has_warnings = any(
                record.get("status") in {"captured_with_warning", "reference_only"}
                for record in self._asset_records.values()
            )
            media_status = (
                "complete_with_warnings"
                if media_complete and media_has_warnings
                else "complete"
                if media_complete
                else "partial"
            )
            media_terminal_reason = None
        else:
            media_complete = not self._asset_records
            media_has_warnings = False
            media_status = "complete" if media_complete else "not_requested"
            media_terminal_reason = (
                None if media_complete else "asset_download_disabled"
            )
        run_complete = (
            streams_complete
            and media_complete
            and message_evidence_complete
            and not interrupted
        )
        status = (
            "complete_with_warnings"
            if run_complete
            and (
                media_has_warnings
                or message_evidence["effective_status"]
                == "complete_with_warnings"
            )
            else "complete"
            if run_complete
            else "partial"
        )
        manifest = {
            "version": 1,
            "run_id": self._checkpoint["run_id"],
            "status": status,
            "streams": streams,
            "media": {
                "status": media_status,
                "terminal_reason": media_terminal_reason,
                "records": len(self._asset_records),
                "complete": sum(
                    record.get("status") == "complete"
                    for record in self._asset_records.values()
                ),
                "captured_with_warning": sum(
                    record.get("status") == "captured_with_warning"
                    for record in self._asset_records.values()
                ),
                "reference_only": sum(
                    record.get("status") == "reference_only"
                    for record in self._asset_records.values()
                ),
                "binary_captured": sum(
                    record.get("status") in {"complete", "captured_with_warning"}
                    and isinstance(record.get("sha256"), str)
                    and isinstance(record.get("blob_path"), str)
                    for record in self._asset_records.values()
                ),
                "failed": sum(
                    record.get("status") == "failed"
                    for record in self._asset_records.values()
                ),
                "not_requested": sum(
                    record.get("status") == "not_requested"
                    for record in self._asset_records.values()
                ),
            },
            "media_recovery_audit": {
                "version": MEDIA_RECOVERY_AUDIT_VERSION,
                "path": MEDIA_RECOVERY_AUDIT_FILENAME,
                "sha256": audit_result["sha256"],
                "counts": deepcopy(audit_result["audit"]["counts"]),
            },
            "message_reference_resolution_audit": (
                deepcopy(reference_audit_descriptor)
            ),
            "message_evidence": message_evidence,
            "errors": len(self._checkpoint["errors"]),
            "not_api_exposed": list(_NON_API_EXPOSED),
        }
        _atomic_write_json(self._run_root / "manifest.json", manifest)
        return manifest


class _AssetTooLarge(Exception):
    pass


def _valid_snowflake(value: object) -> bool:
    return isinstance(value, str) and bool(_SNOWFLAKE.fullmatch(value)) and int(value) > 0


def _valid_diagnostic_code_counts(
    value: object,
    severity_counts: object,
) -> bool:
    if (
        not isinstance(value, dict)
        or set(value) != {"error", "warning", "info"}
        or not isinstance(severity_counts, dict)
        or set(severity_counts) != {"error", "warning", "info"}
    ):
        return False
    for severity in ("error", "warning", "info"):
        codes = value.get(severity)
        declared = severity_counts.get(severity)
        if (
            not isinstance(codes, dict)
            or isinstance(declared, bool)
            or not isinstance(declared, int)
            or declared < 0
            or any(
                not isinstance(code, str)
                or not code
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count <= 0
                for code, count in codes.items()
            )
            or sum(codes.values()) != declared
        ):
            return False
    return True


def _asset_source_sort_key(source: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(source.get(field) or "")
        for field in (
            "stream",
            "evidence_path",
            "json_pointer",
            "channel_id",
            "message_id",
            "root_channel_id",
            "root_message_id",
            "node_key",
        )
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_mkdir(path: Path, containment_root: Path) -> None:
    root = containment_root.resolve(strict=True)
    resolved = path.resolve(strict=False)
    if not _is_relative_to(resolved, root) and resolved != root:
        raise ValueError(f"Discord evidence path escapes containment root: {path}")
    current = root
    relative_parts = resolved.relative_to(root).parts
    for part in relative_parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Discord evidence path contains a symlink: {current}")
        try:
            current.mkdir()
        except FileExistsError:
            pass
        else:
            _fsync_directory(current.parent)
        if not current.is_dir():
            raise ValueError(f"Discord evidence path is not a directory: {current}")


def _canonical_json_bytes(value: Any, *, newline: bool = True) -> bytes:
    suffix = "\n" if newline else ""
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + suffix
    ).encode("utf-8")


def _atomic_write_json(path: Path, value: Any) -> None:
    _atomic_write_bytes(path, _canonical_json_bytes(value))


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_write_chunks(path: Path, chunks: Iterable[bytes]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "wb") as output:
            for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise TypeError("Discord evidence stream yielded a non-bytes chunk")
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return digest.hexdigest()


def _write_exclusive_or_same(path: Path, value: Any) -> str:
    content = _canonical_json_bytes(value)
    return _write_exclusive_bytes_or_same(path, content)


def _write_exclusive_bytes_or_same(path: Path, content: bytes) -> str:
    digest = _sha256_bytes(content)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        mode = None
    if mode is not None:
        if (
            stat.S_ISLNK(mode)
            or not stat.S_ISREG(mode)
            or _sha256_file(path) != digest
        ):
            raise ValueError(f"Discord evidence identity content mismatch: {path}")
        return digest

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            try:
                mode = os.lstat(path).st_mode
            except FileNotFoundError:
                raise ValueError(
                    f"Discord evidence identity publication changed: {path}"
                ) from None
            if (
                stat.S_ISLNK(mode)
                or not stat.S_ISREG(mode)
                or _sha256_file(path) != digest
            ):
                raise ValueError(
                    f"Discord evidence identity content mismatch: {path}"
                ) from None
        else:
            _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        else:
            _fsync_directory(path.parent)
    return digest


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _path_exists_without_following(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    return True


def _read_regular_bytes(path: Path, *, label: str) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{label} is missing or unsafe") from exc
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode):
            raise ValueError(f"{label} must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        final = os.fstat(descriptor)
        try:
            current = os.lstat(path)
        except FileNotFoundError:
            raise ValueError(f"{label} changed while being read") from None
        if (
            not stat.S_ISREG(current.st_mode)
            or (initial.st_dev, initial.st_ino) != (final.st_dev, final.st_ino)
            or (initial.st_dev, initial.st_ino) != (current.st_dev, current.st_ino)
            or initial.st_size != final.st_size
            or initial.st_mtime_ns != final.st_mtime_ns
            or initial.st_ctime_ns != final.st_ctime_ns
        ):
            raise ValueError(f"{label} changed while being read")
    finally:
        os.close(descriptor)
    return b"".join(chunks)


def _read_regular_json_bytes(path: Path, *, label: str) -> tuple[Any, bytes]:
    content = _read_regular_bytes(path, label=label)
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    return payload, content


def _parse_aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("timestamp must be valid ISO8601") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def _normalized_aware_timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return _parse_aware_datetime(value).astimezone(UTC).isoformat()
    except ValueError:
        return None


def _message_stream_bounds(
    first_page_values: list[tuple[str, str | None]],
    last_page_values: list[tuple[str, str | None]],
    *,
    first_fetched_at: str | None,
    last_fetched_at: str | None,
) -> dict[str, Any]:
    def boundary(
        values: list[tuple[str, str | None]],
        *,
        high: bool,
    ) -> dict[str, str | None] | None:
        if not values:
            return None
        ids = [int(message_id) for message_id, _ in values]
        timestamps = [timestamp for _, timestamp in values if timestamp is not None]
        selected_id = max(ids) if high else min(ids)
        selected_timestamp = (
            (max(timestamps) if high else min(timestamps))
            if timestamps
            else None
        )
        return {"id": str(selected_id), "timestamp": selected_timestamp}

    return {
        "high_water": boundary(first_page_values, high=True),
        "high_water_scope": "first_response",
        "low_water": boundary(last_page_values, high=False),
        "low_water_scope": "last_nonempty_response",
        "fetched_at": {
            "first_response": first_fetched_at,
            "last_response": last_fetched_at,
            "source": "collector_local_clock_after_response",
        },
    }


def _message_stream_channel_id(stream_key: str) -> str:
    match = re.fullmatch(r"(?:messages|pins)_([0-9]+)", stream_key)
    if match is None or not _valid_snowflake(match.group(1)):
        raise ValueError(f"Discord message evidence stream identity is invalid: {stream_key}")
    return match.group(1)


def _message_evidence_counts(
    rows: list[dict[str, Any]],
    *,
    stream_key: str,
    channel_id: str,
    page_number: int,
    raw_page_sha256: str,
) -> dict[str, Any]:
    totals: dict[str, Any] = {
        "root_messages": len(rows),
        "partial_messages": 0,
        "nodes": 0,
        "media_occurrences": 0,
        "references": 0,
        "diagnostics": 0,
        "pin_events": 0,
        "diagnostics_by_severity": {"error": 0, "warning": 0, "info": 0},
    }
    raw_relative_path = f"pages/{stream_key}/{page_number:06d}.json"
    for row_number, row in enumerate(rows, start=1):
        pointer = row.get("message_json_pointer")
        if (
            row.get("schema_version") != _MESSAGE_EVIDENCE_SCHEMA_VERSION
            or row.get("stream") != stream_key
            or row.get("channel_id") != channel_id
            or row.get("page_number") != page_number
            or not isinstance(pointer, str)
            or row.get("status") not in {"complete", "partial"}
        ):
            raise ValueError(
                f"Discord message evidence row identity mismatch: "
                f"{stream_key}/{page_number:06d}/{row_number}"
            )
        nodes = row.get("nodes")
        media = row.get("media")
        references = row.get("references")
        diagnostics = row.get("diagnostics")
        if not all(
            isinstance(value, list)
            for value in (nodes, media, references, diagnostics)
        ):
            raise ValueError(
                f"Discord message evidence row shape is invalid: "
                f"{stream_key}/{page_number:06d}/{row_number}"
            )
        assert isinstance(nodes, list)
        assert isinstance(media, list)
        assert isinstance(references, list)
        assert isinstance(diagnostics, list)
        totals["partial_messages"] += row["status"] == "partial"
        totals["nodes"] += len(nodes)
        totals["media_occurrences"] += len(media)
        totals["references"] += len(references)
        totals["diagnostics"] += len(diagnostics)
        for diagnostic in diagnostics:
            severity = diagnostic.get("severity") if isinstance(diagnostic, dict) else None
            if severity not in {"error", "warning", "info"}:
                raise ValueError(
                    f"Discord message evidence diagnostic is invalid: "
                    f"{stream_key}/{page_number:06d}/{row_number}"
                )
            totals["diagnostics_by_severity"][severity] += 1
        for occurrence in (*media, *references):
            source = occurrence.get("source") if isinstance(occurrence, dict) else None
            if (
                not isinstance(source, dict)
                or source.get("stream") != stream_key
                or source.get("evidence_path") != raw_relative_path
                or source.get("evidence_sha256") != raw_page_sha256
            ):
                raise ValueError(
                    f"Discord message evidence source is invalid: "
                    f"{stream_key}/{page_number:06d}/{row_number}"
                )
        pin_event = row.get("pin_event")
        if stream_key.startswith("pins_"):
            if not isinstance(pin_event, dict):
                raise ValueError(
                    f"Discord pin event evidence is missing: "
                    f"{stream_key}/{page_number:06d}/{row_number}"
                )
            pinned_at = pin_event.get("pinned_at")
            pinned_at_utc = pin_event.get("pinned_at_utc")
            message_id = pin_event.get("message_id")
            event_pointer = pin_event.get("json_pointer")
            if (
                pin_event.get("channel_id") != channel_id
                or not _valid_snowflake(message_id)
                or not isinstance(pinned_at, str)
                or not isinstance(pinned_at_utc, str)
                or _parse_aware_datetime(pinned_at).astimezone(UTC).isoformat()
                != pinned_at_utc
                or pointer != f"{event_pointer}/message"
                or pin_event.get("event_key")
                != f"pin_event:{channel_id}:{message_id}:{pinned_at_utc}"
            ):
                raise ValueError(
                    f"Discord pin event evidence is invalid: "
                    f"{stream_key}/{page_number:06d}/{row_number}"
                )
            totals["pin_events"] += 1
        elif pin_event is not None:
            raise ValueError(
                f"Discord history evidence unexpectedly contains a pin event: "
                f"{stream_key}/{page_number:06d}/{row_number}"
            )
    return totals


def _page_fetched_at(document: object, *, label: str) -> str:
    if not isinstance(document, dict):
        raise ValueError(f"{label} must be a JSON object")
    acquisition = document.get("acquisition")
    if (
        not isinstance(acquisition, dict)
        or set(acquisition) != {"fetched_at", "source"}
        or acquisition.get("source") != "collector_local_clock_after_response"
        or not isinstance(acquisition.get("fetched_at"), str)
    ):
        raise ValueError(f"{label} acquisition metadata is invalid")
    fetched_at = acquisition["fetched_at"]
    _parse_aware_datetime(fetched_at)
    return fetched_at


def _land_or_adopt_page(
    path: Path,
    page_document_base: dict[str, Any],
    *,
    label: str,
) -> tuple[str, dict[str, Any]]:
    try:
        os.lstat(path)
    except FileNotFoundError:
        page_document = {
            **deepcopy(page_document_base),
            "acquisition": {
                "fetched_at": datetime.now(UTC).isoformat(),
                "source": "collector_local_clock_after_response",
            },
        }
        return _write_exclusive_or_same(path, page_document), page_document

    stored, content = _read_regular_json_bytes(path, label=label)
    if not isinstance(stored, dict):
        raise ValueError(f"{label} must be a JSON object")
    stored_base = deepcopy(stored)
    stored_base.pop("acquisition", None)
    if stored_base != page_document_base:
        raise ValueError(f"{label} content does not match the response")
    _page_fetched_at(stored, label=label)
    return _sha256_bytes(content), stored


def _api_origin(transport: DiscordJSONTransport) -> str:
    raw_base_url = getattr(transport, "base_url", None)
    if raw_base_url is None:
        raise ValueError("Discord API origin is unavailable from transport")
    if not isinstance(raw_base_url, str):
        raise ValueError("Discord API origin is invalid")
    parsed = urlsplit(raw_base_url)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Discord API origin is invalid")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("Discord API origin is invalid") from None
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if scheme == "https" else 80
    rendered_port = "" if port is None or port == default_port else f":{port}"
    return f"{scheme}://{rendered_host}{rendered_port}"


def _add_secondary_exception_note(
    primary: BaseException,
    context: str,
    secondary: BaseException,
) -> None:
    primary.add_note(f"Discord collector {context}: {secondary!r}")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid Discord evidence JSON: {path}") from exc


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _file_fingerprint(path: Path) -> tuple[Path, int, int, int, int, int]:
    status = os.lstat(path)
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
        raise ValueError("Discord evidence file is missing or unsafe")
    return (
        path.parent.resolve(strict=True) / path.name,
        int(status.st_dev),
        int(status.st_ino),
        int(status.st_size),
        int(status.st_mtime_ns),
        int(status.st_ctime_ns),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_mime(value: object) -> str | None:
    return normalized_discord_media_mime(value)


def _mime_family(value: str) -> str:
    return value.partition("/")[0]


def _media_candidate_urls(occurrence: MediaOccurrence) -> list[str]:
    candidates: list[str] = []
    for value in (
        occurrence.observed_url,
        occurrence.url,
        occurrence.proxy_url,
    ):
        if not isinstance(value, str) or value in candidates:
            continue
        try:
            is_http = urlsplit(value).scheme.lower() in {"http", "https"}
        except ValueError:
            # Keep malformed absolute HTTP(S) observations in the audit ledger.
            # The byte transport's URL validator will classify them as unsafe,
            # allowing a later Discord proxy candidate to recover the asset.
            is_http = value.lower().startswith(("http://", "https://"))
        if is_http:
            candidates.append(value)
    return candidates


def _youtube_embed_player_url_identity(value: object) -> dict[str, str] | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    host = parsed.hostname
    if (
        parsed.scheme.lower() != "https"
        or host not in _YOUTUBE_EMBED_PLAYER_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
        or _YOUTUBE_EMBED_PLAYER_PATH.fullmatch(parsed.path) is None
    ):
        return None
    return {
        "scheme": "https",
        "host": host,
        "path": parsed.path,
    }


def _youtube_embed_player_reference_provenance(
    record: Mapping[str, Any],
) -> dict[str, Any] | None:
    source_url = record.get("url")
    metadata = record.get("declared_metadata")
    attempts = record.get("attempt_history")
    if (
        not isinstance(metadata, Mapping)
        or (
            isinstance(metadata.get("proxy_url"), str)
            and bool(metadata.get("proxy_url"))
        )
        or not isinstance(attempts, list)
        or not attempts
        or record.get("http_content_type") is not None
        or record.get("http_content_length") is not None
        or record.get("actual_bytes") != 0
        or record.get("sha256") is not None
        or record.get("blob_path") is not None
    ):
        return None
    stored_provenance = record.get("reference_provenance")
    failed_attempt_number = (
        stored_provenance.get("failed_attempt_number")
        if isinstance(stored_provenance, Mapping)
        else len(attempts)
    )
    provenance = _youtube_embed_player_attempt_provenance(
        record,
        source_url=source_url,
        failed_attempt_number=failed_attempt_number,
        allow_pending_tail=False,
    )
    if provenance is None or not discord_media_reference_candidate_ledger_is_exact(
        record,
        source_url=source_url,
        failed_attempt_number=failed_attempt_number,
    ):
        return None
    return provenance


def _youtube_embed_player_attempt_provenance(
    record: Mapping[str, Any],
    *,
    source_url: object,
    failed_attempt_number: object,
    allow_pending_tail: bool = False,
    allow_later_covered: bool = False,
) -> dict[str, Any] | None:
    identity = _youtube_embed_player_url_identity(source_url)
    attempts = record.get("attempt_history")
    if (
        record.get("kind") != "embed"
        or record.get("field") != "video"
        or record.get("declared_content_type") is not None
        or identity is None
        or isinstance(failed_attempt_number, bool)
        or not isinstance(failed_attempt_number, int)
        or not isinstance(attempts, list)
        or failed_attempt_number < 1
        or failed_attempt_number > len(attempts)
        or discord_media_reference_source_observation(
            record,
            source_url,
        )
        is None
    ):
        return None
    failed_attempt = attempts[failed_attempt_number - 1]
    if (
        not isinstance(failed_attempt, Mapping)
        or failed_attempt.get("url") != source_url
        or failed_attempt.get("status") != "failed"
        or failed_attempt.get("terminal_reason") != "unsafe_media_url"
        or failed_attempt.get("http_content_type") is not None
        or failed_attempt.get("http_content_length") is not None
        or failed_attempt.get("actual_bytes") != 0
        or failed_attempt.get("sha256") is not None
        or failed_attempt.get("blob_path") is not None
    ):
        return None
    later_attempts = attempts[failed_attempt_number:]
    for later_index, later_attempt in enumerate(
        later_attempts,
        start=failed_attempt_number + 1,
    ):
        if (
            not isinstance(later_attempt, Mapping)
            or later_attempt.get("url") == source_url
        ):
            return None
        later_status = later_attempt.get("status")
        later_reason = later_attempt.get("terminal_reason")
        if later_status == "failed" and isinstance(later_reason, str) and later_reason:
            continue
        if allow_later_covered and later_status in _COVERED_ASSET_STATUSES:
            continue
        if (
            allow_pending_tail
            and later_index == len(attempts)
            and (
                (later_status == "in_progress" and later_reason is None)
                or (
                    later_status == "interrupted"
                    and later_reason == "interrupted"
                )
            )
        ):
            continue
        return None
    return {
        "classification": "youtube_embed_player",
        "classification_rule": _YOUTUBE_EMBED_PLAYER_REFERENCE_RULE,
        "source_url": source_url,
        "url_identity": identity,
        "failed_attempt_number": failed_attempt_number,
        "failed_attempt_status": "failed",
        "failed_attempt_terminal_reason": "unsafe_media_url",
        "proxy_candidate_present": False,
        "binary_captured": False,
    }


def _is_stale_completed_youtube_embed_player_reference(
    record: Mapping[str, Any],
) -> bool:
    provenance = record.get("reference_provenance")
    if not isinstance(provenance, Mapping):
        return False
    source_url = provenance.get("source_url")
    failed_attempt_number = provenance.get("failed_attempt_number")
    expected = _youtube_embed_player_attempt_provenance(
        record,
        source_url=source_url,
        failed_attempt_number=failed_attempt_number,
        allow_later_covered=True,
    )
    current_url = record.get("url")
    candidate_urls = record.get("candidate_urls")
    metadata = record.get("declared_metadata")
    attempts = record.get("attempt_history")
    sources = record.get("sources")
    observations = record.get("observations")
    if (
        expected is None
        or provenance != expected
        or record.get("status") != "complete"
        or record.get("terminal_reason") != "downloaded"
        or not isinstance(current_url, str)
        or current_url == source_url
        or candidate_urls != [source_url, current_url]
        or not _is_discord_external_proxy_url(current_url)
        or not isinstance(metadata, Mapping)
        or metadata.get("proxy_url") != current_url
        or not isinstance(record.get("http_content_type"), str)
        or not record["http_content_type"].startswith("video/")
        or record.get("http_content_length") != record.get("actual_bytes")
        or isinstance(record.get("actual_bytes"), bool)
        or not isinstance(record.get("actual_bytes"), int)
        or record["actual_bytes"] <= 0
        or not isinstance(record.get("sha256"), str)
        or not isinstance(record.get("blob_path"), str)
        or not isinstance(attempts, list)
        or len(attempts) <= failed_attempt_number
        or not isinstance(sources, list)
        or not isinstance(observations, list)
        or not any(
            isinstance(observation, Mapping)
            and observation.get("url") == source_url
            and observation.get("proxy_url") == current_url
            and observation.get("source") in sources
            for observation in observations
        )
    ):
        return False
    successful_attempt = attempts[-1]
    return (
        isinstance(successful_attempt, Mapping)
        and successful_attempt.get("url") == current_url
        and successful_attempt.get("status") == "complete"
        and successful_attempt.get("terminal_reason") == "downloaded"
        and successful_attempt.get("http_content_type")
        == record.get("http_content_type")
        and successful_attempt.get("http_content_length")
        == record.get("http_content_length")
        and successful_attempt.get("actual_bytes") == record.get("actual_bytes")
        and successful_attempt.get("sha256") == record.get("sha256")
        and successful_attempt.get("blob_path") == record.get("blob_path")
    )


def _is_discord_external_proxy_url(value: object) -> bool:
    return _recovery_is_discord_external_proxy_url(value)


def _supports_candidate_fallback(reason: object) -> bool:
    return (
        reason in _RETRYABLE_ASSET_REASONS
        or reason
        in {
            "unsafe_media_url",
            "media_resolution_failed_transient",
            "media_resolution_retry_exhausted",
            "media_resolution_unresolved",
            "media_resolution_invalid_answer",
            "media_type_mismatch",
            "declared_media_type_mismatch",
        }
        or isinstance(reason, str)
        and reason.startswith("download_http_")
    )


def _is_reusable_generic_pending_attempt(attempt: Mapping[str, Any]) -> bool:
    status = attempt.get("status")
    reason = attempt.get("terminal_reason")
    return (
        (
            (status == "in_progress" and reason is None)
            or (status == "interrupted" and reason == "interrupted")
        )
        and isinstance(attempt.get("actual_bytes"), int)
        and not isinstance(attempt.get("actual_bytes"), bool)
        and attempt.get("actual_bytes") == 0
        and all(
            attempt.get(field) is None
            for field in (
                "http_content_type",
                "http_content_length",
                "sha256",
                "blob_path",
                "failure_detail",
            )
        )
        and not any(
            field in attempt
            for field in (
                "policy_inputs_sha256",
                "resolution_retry_sequence",
                "retry_trigger",
                "retry_of_attempt_number",
            )
        )
    )


def _attachment_identity_metadata(attachment: dict[str, Any]) -> dict[str, Any]:
    return discord_media_identity_metadata("attachment", attachment)


def _sticker_identity_metadata(sticker: dict[str, Any]) -> dict[str, Any]:
    return discord_media_identity_metadata("sticker", sticker)


def _without_url_metadata(value: Any) -> Any:
    return discord_media_metadata_without_urls(value)


def _mime_extension(content_type: str | None) -> str:
    return {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/gif": "gif",
        "image/webp": "webp",
        "video/mp4": "mp4",
        "video/webm": "webm",
        "audio/ogg": "ogg",
        "audio/mpeg": "mp3",
        "application/pdf": "pdf",
    }.get(content_type or "", "bin")


def _object_time(value: dict[str, Any]) -> str | None:
    for key in ("pinned_at", "timestamp"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    message = value.get("message")
    if isinstance(message, dict):
        candidate = message.get("timestamp")
        if isinstance(candidate, str) and candidate:
            return candidate
    metadata = value.get("thread_metadata")
    if isinstance(metadata, dict):
        candidate = metadata.get("archive_timestamp")
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _object_id(value: dict[str, Any]) -> str | None:
    candidate = value.get("id")
    if isinstance(candidate, str):
        return candidate
    message = value.get("message")
    if isinstance(message, dict):
        candidate = message.get("id")
        if isinstance(candidate, str):
            return candidate
    return None
