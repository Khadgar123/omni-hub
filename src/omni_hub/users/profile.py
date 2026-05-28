"""UserProfileStore — SQLite-backed multi-user identity (v0.31).

Stores ``UserProfile`` rows + the Letta-style **core memory block** (a
small free-form text block the agent rewrites mid-conversation to keep
the most-important user facts in-context).  Recall + archival memory
live in :mod:`omni_hub.users.memory_tiers`.

Design notes:

* ``user_id`` is a uuid4 (kebab-cased ``hex[:12]`` for handle-default).
* ``handle`` is human-readable, unique-but-mutable; ``user_id`` is the
  stable foreign key for everything else (preference, recall, etc.).
* Persona block is capped at 4096 chars — anything longer belongs in
  recall.  Same cap Letta uses by default.
* New users come in as ``PENDING``; an explicit ``approve`` call
  promotes them to ``ACTIVE``.  This is the single-write chokepoint
  for multi-user, mirroring Proposal[T].
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any


DEFAULT_USER_HANDLE = "hzh"
USERS_DB_REL = ".omni/users.sqlite3"

PERSONA_BLOCK_MAX_CHARS = 4096


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _new_user_id() -> str:
    return f"u_{secrets.token_hex(6)}"


class UserStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(slots=True)
class UserProfile:
    user_id: str
    handle: str
    status: UserStatus = UserStatus.PENDING
    persona_block: str = ""
    style_prefs: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "UserProfile":
        return cls(
            user_id=row["user_id"],
            handle=row["handle"],
            status=UserStatus(row["status"]),
            persona_block=row["persona_block"] or "",
            style_prefs=json.loads(row["style_prefs"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class UserProfileStore:
    """Persistent multi-user identity store."""

    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace).resolve()
        self.db_path = self.workspace / USERS_DB_REL
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._ensure_default_user()

    # ---- enrollment ---------------------------------------------

    def enroll(self, handle: str, *,
               status: UserStatus = UserStatus.PENDING) -> UserProfile:
        """Create a new user (PENDING by default).  ``handle`` must be
        unique."""

        handle = handle.strip()
        if not handle:
            raise ValueError("handle is required")
        existing = self.get_by_handle(handle)
        if existing is not None:
            raise ValueError(f"handle {handle!r} already enrolled (user_id={existing.user_id})")
        profile = UserProfile(
            user_id=_new_user_id(),
            handle=handle,
            status=status,
        )
        self._insert(profile)
        return profile

    def approve(self, user_id: str) -> UserProfile:
        return self._update_status(user_id, UserStatus.ACTIVE)

    def disable(self, user_id: str) -> UserProfile:
        return self._update_status(user_id, UserStatus.DISABLED)

    # ---- lookup -------------------------------------------------

    def get(self, user_id: str) -> UserProfile | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,),
            ).fetchone()
        return UserProfile.from_row(row) if row else None

    def get_by_handle(self, handle: str) -> UserProfile | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE handle = ?", (handle,),
            ).fetchone()
        return UserProfile.from_row(row) if row else None

    def resolve(self, user_id_or_handle: str | None) -> UserProfile:
        """Return the matching profile or the default user when empty.

        Tries ``user_id`` (``u_*``) first, then handle; raises if neither
        matches but a non-empty string was passed.
        """

        if not user_id_or_handle:
            handle_default = self.get_by_handle(DEFAULT_USER_HANDLE)
            assert handle_default is not None, "default user should always exist"
            return handle_default
        if user_id_or_handle.startswith("u_"):
            found = self.get(user_id_or_handle)
        else:
            found = self.get_by_handle(user_id_or_handle)
        if found is None:
            raise KeyError(f"no user matches {user_id_or_handle!r}")
        return found

    def list(self, *, status: UserStatus | None = None) -> list[UserProfile]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM users WHERE status = ? ORDER BY created_at",
                    (status.value,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM users ORDER BY created_at"
                ).fetchall()
        return [UserProfile.from_row(r) for r in rows]

    # ---- mutate -------------------------------------------------

    def set_persona_block(self, user_id: str, block: str) -> UserProfile:
        if len(block) > PERSONA_BLOCK_MAX_CHARS:
            raise ValueError(
                f"persona block exceeds {PERSONA_BLOCK_MAX_CHARS} chars; "
                "anything longer belongs in recall memory (vault/users/.../recall/)"
            )
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE users SET persona_block = ?, updated_at = ? "
                "WHERE user_id = ?",
                (block, _utcnow(), user_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise KeyError(f"no user_id {user_id!r}")
        return self.get(user_id)  # type: ignore[return-value]

    def set_style_prefs(self, user_id: str, style_prefs: dict[str, Any]) -> UserProfile:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE users SET style_prefs = ?, updated_at = ? "
                "WHERE user_id = ?",
                (json.dumps(style_prefs, ensure_ascii=False), _utcnow(), user_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise KeyError(f"no user_id {user_id!r}")
        return self.get(user_id)  # type: ignore[return-value]

    # ---- internals ---------------------------------------------

    def _insert(self, profile: UserProfile) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO users "
                "(user_id, handle, status, persona_block, style_prefs, "
                " created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (profile.user_id, profile.handle, profile.status.value,
                 profile.persona_block,
                 json.dumps(profile.style_prefs, ensure_ascii=False),
                 profile.created_at, profile.updated_at),
            )
            conn.commit()

    def _update_status(self, user_id: str, status: UserStatus) -> UserProfile:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE users SET status = ?, updated_at = ? WHERE user_id = ?",
                (status.value, _utcnow(), user_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise KeyError(f"no user_id {user_id!r}")
        return self.get(user_id)  # type: ignore[return-value]

    def _ensure_default_user(self) -> None:
        if self.get_by_handle(DEFAULT_USER_HANDLE) is None:
            profile = UserProfile(
                user_id=_new_user_id(),
                handle=DEFAULT_USER_HANDLE,
                status=UserStatus.ACTIVE,
                persona_block=(
                    f"# {DEFAULT_USER_HANDLE} (project owner)\n"
                    f"Default user for omni-hub.  Multi-tenant disabled "
                    f"until additional users are enrolled."
                ),
                style_prefs={"language": "zh-Hans", "tone": "terse"},
            )
            self._insert(profile)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA busy_timeout = 30000;

                CREATE TABLE IF NOT EXISTS users (
                    user_id        TEXT PRIMARY KEY,
                    handle         TEXT UNIQUE NOT NULL,
                    status         TEXT NOT NULL,
                    persona_block  TEXT DEFAULT '',
                    style_prefs    TEXT DEFAULT '{}',
                    created_at     TEXT NOT NULL,
                    updated_at     TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_users_status
                    ON users(status, created_at);
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


__all__ = [
    "DEFAULT_USER_HANDLE",
    "PERSONA_BLOCK_MAX_CHARS",
    "UserProfile",
    "UserProfileStore",
    "UserStatus",
]
