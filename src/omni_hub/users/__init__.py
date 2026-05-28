"""Users plane (v0.31).

Single-machine local-first system with optional multi-tenancy.  The
**default user** is the project owner (handle ``hzh`` by default), used
when no ``user_id`` is supplied; additional users (channel members,
shared devices, etc.) live alongside and get their own:

* persona block (Letta-style core memory the agent can rewrite)
* style preferences (tone / language / verbosity)
* per-user PreferenceStore segment at
  ``.omni/preference/users/<user_id>/<domain>.jsonl``
* recall + archival memory under ``vault/users/<user_id>/{recall,archival}/``

No identity / OAuth — single-machine; future v0.40+ may add HTTP auth.
"""

from __future__ import annotations

from .memory_tiers import (
    ArchivalEntry,
    MemoryTier,
    PerUserMemoryStore,
    RecallEntry,
)
from .profile import (
    DEFAULT_USER_HANDLE,
    UserProfile,
    UserProfileStore,
    UserStatus,
)

__all__ = [
    "ArchivalEntry",
    "DEFAULT_USER_HANDLE",
    "MemoryTier",
    "PerUserMemoryStore",
    "RecallEntry",
    "UserProfile",
    "UserProfileStore",
    "UserStatus",
]
