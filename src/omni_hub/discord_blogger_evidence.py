"""Streaming rich evidence envelopes for the verified blogger corpus."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
from typing import Iterator, Sequence

from .discord_blogger_corpus import (
    BloggerMessage,
    VerifiedRawMessageSnapshot,
    _json_file,
    _message,
    _root,
    iter_verified_blogger_raw_snapshots,
    read_blogger_closure_bytes,
)
from .discord_sharding import canonical_json_sha256
from .discord_message_evidence import MessageEvidence, extract_message_evidence


@dataclass(frozen=True, slots=True)
class SnapshotProvenance:
    source_kind: str
    snapshot_ref: str
    snapshot_sha256: str
    evidence_path: str
    evidence_sha256: str
    current: bool


@dataclass(frozen=True, slots=True)
class VerifiedMessageEnvelope:
    message: BloggerMessage
    evidence: MessageEvidence
    snapshot_provenance: tuple[SnapshotProvenance, ...]


def iter_verified_blogger_evidence(
    *,
    export_root: Path,
    closure_audit: Path,
    target_ids: Sequence[str],
) -> Iterator[VerifiedMessageEnvelope]:
    """Yield current rich snapshots with append-only snapshot provenance.

    SQLite owns global message uniqueness and current-snapshot selection, so
    the full raw corpus is never duplicated in Python memory.  Baseline rows
    establish the initial current pointer; a closure row advances that pointer
    while retaining the baseline provenance row.
    """

    closure_bytes, closure_sha = read_blogger_closure_bytes(
        export_root=export_root,
        closure_audit_path=closure_audit,
    )
    root = _root(export_root)
    with tempfile.TemporaryDirectory(
        prefix="omni-discord-blogger-evidence-"
    ) as directory:
        database = Path(directory) / "snapshots.sqlite3"
        conn = sqlite3.connect(database)
        conn.row_factory = sqlite3.Row
        try:
            _init_schema(conn)
            pending = 0
            for snapshot in iter_verified_blogger_raw_snapshots(
                export_root=export_root,
                closure_audit_path=closure_audit,
                target_ids=target_ids,
                expected_closure_sha256=closure_sha,
            ):
                _record_snapshot(conn, snapshot)
                pending += 1
                if pending >= 1000:
                    conn.commit()
                    pending = 0
            conn.commit()

            rows = conn.execute(
                """
                SELECT c.message_id, c.snapshot_id, c.timestamp_us,
                       s.snapshot_sha256, s.stream, s.evidence_path,
                       s.evidence_sha256, s.json_pointer
                FROM blogger_current_snapshots AS c
                JOIN blogger_snapshot_provenance AS s
                  ON s.snapshot_id = c.snapshot_id
                ORDER BY c.timestamp_us, CAST(c.message_id AS INTEGER)
                """
            )
            for row in rows:
                raw = _load_raw_snapshot(root, row)
                evidence = extract_message_evidence(
                    raw,
                    stream=str(row["stream"]),
                    evidence_path=str(row["evidence_path"]),
                    evidence_sha256=str(row["evidence_sha256"]),
                    json_pointer=str(row["json_pointer"]),
                )
                media_refs = tuple(
                    f"{occurrence.source.evidence_path}"
                    f"#{occurrence.json_pointer}"
                    for occurrence in evidence.media
                )
                message = _message(
                    raw,
                    _current_snapshot_ref(conn, int(row["snapshot_id"])),
                    media_refs,
                )
                yield VerifiedMessageEnvelope(
                    message=message,
                    evidence=evidence,
                    snapshot_provenance=_provenance(
                        conn,
                        message_id=message.message_id,
                        current_snapshot_id=int(row["snapshot_id"]),
                    ),
                )
            closure_after, closure_after_sha = read_blogger_closure_bytes(
                export_root=export_root,
                closure_audit_path=closure_audit,
            )
            if (
                closure_after != closure_bytes
                or closure_after_sha != closure_sha
            ):
                raise ValueError(
                    "Discord blogger closure audit changed during evidence iteration"
                )
        finally:
            conn.close()


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = FULL;

        CREATE TABLE blogger_snapshot_provenance (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL,
            source_kind TEXT NOT NULL
                CHECK (source_kind IN ('baseline', 'closure')),
            snapshot_ref TEXT NOT NULL,
            snapshot_sha256 TEXT NOT NULL,
            stream TEXT NOT NULL,
            evidence_path TEXT NOT NULL,
            evidence_sha256 TEXT NOT NULL,
            json_pointer TEXT NOT NULL,
            UNIQUE (
                message_id, source_kind, snapshot_ref, snapshot_sha256
            )
        );

        CREATE TABLE blogger_current_snapshots (
            message_id TEXT PRIMARY KEY,
            snapshot_id INTEGER NOT NULL UNIQUE,
            timestamp_us INTEGER NOT NULL,
            FOREIGN KEY (snapshot_id)
                REFERENCES blogger_snapshot_provenance(snapshot_id)
                ON DELETE RESTRICT
        );

        CREATE INDEX blogger_snapshot_message_order
            ON blogger_snapshot_provenance(message_id, snapshot_id);
        """
    )


def _record_snapshot(
    conn: sqlite3.Connection, snapshot: VerifiedRawMessageSnapshot
) -> None:
    raw = dict(snapshot.raw_message)
    message_id = raw.get("id")
    timestamp = raw.get("timestamp")
    if (
        not isinstance(message_id, str)
        or not message_id.isdecimal()
        or int(message_id) <= 0
        or not isinstance(timestamp, str)
    ):
        raise ValueError("Discord blogger raw snapshot identity is invalid")
    conn.execute(
        """
        INSERT OR IGNORE INTO blogger_snapshot_provenance (
            message_id, source_kind, snapshot_ref, snapshot_sha256,
            stream, evidence_path, evidence_sha256, json_pointer
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message_id,
            snapshot.source_kind,
            snapshot.snapshot_ref,
            snapshot.snapshot_sha256,
            snapshot.stream,
            snapshot.evidence_path,
            snapshot.evidence_sha256,
            snapshot.json_pointer,
        ),
    )
    row = conn.execute(
        """
        SELECT snapshot_id
        FROM blogger_snapshot_provenance
        WHERE message_id = ? AND source_kind = ? AND snapshot_ref = ?
          AND snapshot_sha256 = ?
        """,
        (
            message_id,
            snapshot.source_kind,
            snapshot.snapshot_ref,
            snapshot.snapshot_sha256,
        ),
    ).fetchone()
    if row is None:
        raise ValueError("Discord blogger snapshot identity collision")
    snapshot_id = int(row["snapshot_id"])
    timestamp_us = _timestamp_us(timestamp)
    current = conn.execute(
        """
        SELECT c.snapshot_id, s.source_kind, s.snapshot_sha256
        FROM blogger_current_snapshots AS c
        JOIN blogger_snapshot_provenance AS s
          ON s.snapshot_id = c.snapshot_id
        WHERE c.message_id = ?
        """,
        (message_id,),
    ).fetchone()
    if current is None:
        conn.execute(
            """
            INSERT INTO blogger_current_snapshots (
                message_id, snapshot_id, timestamp_us
            ) VALUES (?, ?, ?)
            """,
            (message_id, snapshot_id, timestamp_us),
        )
        return
    if snapshot.source_kind == "baseline":
        if (
            current["source_kind"] != "baseline"
            or current["snapshot_sha256"] != snapshot.snapshot_sha256
        ):
            raise ValueError(
                "Discord baseline contains conflicting duplicate message IDs"
            )
        return
    if (
        current["source_kind"] == "closure"
        and current["snapshot_sha256"] != snapshot.snapshot_sha256
    ):
        raise ValueError(
            "Discord closure contains conflicting duplicate message IDs"
        )
    conn.execute(
        """
        UPDATE blogger_current_snapshots
        SET snapshot_id = ?, timestamp_us = ?
        WHERE message_id = ?
        """,
        (snapshot_id, timestamp_us, message_id),
    )


def _provenance(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    current_snapshot_id: int,
) -> tuple[SnapshotProvenance, ...]:
    rows = conn.execute(
        """
        SELECT snapshot_id, source_kind, snapshot_ref, snapshot_sha256,
               evidence_path, evidence_sha256
        FROM blogger_snapshot_provenance
        WHERE message_id = ?
        ORDER BY snapshot_id
        """,
        (message_id,),
    ).fetchall()
    return tuple(
        SnapshotProvenance(
            source_kind=str(row["source_kind"]),
            snapshot_ref=str(row["snapshot_ref"]),
            snapshot_sha256=str(row["snapshot_sha256"]),
            evidence_path=str(row["evidence_path"]),
            evidence_sha256=str(row["evidence_sha256"]),
            current=int(row["snapshot_id"]) == current_snapshot_id,
        )
        for row in rows
    )


def _load_raw_snapshot(
    root: Path, row: sqlite3.Row
) -> dict[str, object]:
    relative = Path(str(row["evidence_path"]))
    source, source_sha = _json_file(
        root, relative, "blogger current snapshot source"
    )
    if source_sha != row["evidence_sha256"]:
        raise ValueError("Discord blogger current snapshot source changed")
    current: object = source
    pointer = str(row["json_pointer"])
    if not pointer.startswith("/"):
        raise ValueError("Discord blogger current snapshot pointer is invalid")
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if part not in current:
                raise ValueError(
                    "Discord blogger current snapshot pointer is invalid"
                )
            current = current[part]
        elif isinstance(current, list) and part.isdecimal():
            index = int(part)
            if index >= len(current):
                raise ValueError(
                    "Discord blogger current snapshot pointer is invalid"
                )
            current = current[index]
        else:
            raise ValueError(
                "Discord blogger current snapshot pointer is invalid"
            )
    if (
        not isinstance(current, dict)
        or canonical_json_sha256(current) != row["snapshot_sha256"]
    ):
        raise ValueError("Discord blogger current snapshot commitment changed")
    return current


def _current_snapshot_ref(conn: sqlite3.Connection, snapshot_id: int) -> str:
    row = conn.execute(
        """
        SELECT snapshot_ref
        FROM blogger_snapshot_provenance
        WHERE snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Discord current snapshot provenance is missing")
    return str(row["snapshot_ref"])


def _timestamp_us(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Discord blogger snapshot timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(
            "Discord blogger snapshot timestamp must be timezone-aware"
        )
    return int(parsed.timestamp() * 1_000_000)
