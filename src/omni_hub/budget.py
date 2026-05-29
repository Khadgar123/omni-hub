"""ccLoad budget snapshot reader (SEAM B, refactor step 10).

The gateway (ccLoad) is the single authority for spend truth — it already
tracks per-auth-token cost (microUSD integers) and per-channel daily spend
(USD floats).  omni-hub must READ that, never recompute it, so the two
budget systems stop drifting.

This is the omni-hub-side contract: a tiny urllib reader that hits
``GET /admin/budget/snapshot`` on the local gateway.  It is **fail-open**:
any error (gateway down, endpoint absent, timeout, bad JSON) yields an
``available=False`` snapshot so a missing/slow gateway can NEVER block the
user.  The matching endpoint ships in the ccLoad fork (step 11); until then
this reader simply returns unavailable, which is the correct inert default.

NOTE the deliberate unit split, mirroring the gateway's own model:
auth-token cost is microUSD **int**; channel daily spend is USD **float**.
Do not conflate them.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

BUDGET_SCHEMA_VERSION = "v0.47"

DEFAULT_GATEWAY_URL = "http://127.0.0.1:8080"
_SNAPSHOT_PATH = "/admin/budget/snapshot"


@dataclass(slots=True)
class BudgetSnapshot:
    """A point-in-time read of gateway spend.  ``available=False`` means the
    gateway could not be reached and the snapshot carries no real figures
    (fail-open: callers must treat it as 'no constraint known')."""

    available: bool
    token_used_microusd: int = 0       # auth-token scope (integer microUSD)
    token_limit_microusd: int = 0
    channel_daily_used_usd: float = 0.0  # channel scope (float USD) — different unit!
    channel_daily_limit_usd: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)
    schema_version: str = BUDGET_SCHEMA_VERSION

    def token_fraction(self) -> float:
        """Fraction of the auth-token microUSD limit consumed (0.0 if no limit)."""
        if self.token_limit_microusd <= 0:
            return 0.0
        return self.token_used_microusd / self.token_limit_microusd

    def over_soft_ceiling(self, fraction: float) -> bool:
        """True only when the gateway answered AND usage has reached a
        positive soft-ceiling fraction.  An unavailable snapshot is never
        'over' (fail-open)."""
        if not self.available or fraction <= 0.0:
            return False
        return self.token_fraction() >= fraction


@dataclass(slots=True)
class BudgetReader:
    base_url: str = DEFAULT_GATEWAY_URL
    timeout: float = 0.5

    def snapshot(self, *, token_hash: str | None = None) -> BudgetSnapshot:
        url = self.base_url.rstrip("/") + _SNAPSHOT_PATH
        if token_hash:
            url += "?token=" + urllib.parse.quote(token_hash)
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            return BudgetSnapshot(available=False)  # fail-open
        if not isinstance(data, dict):
            return BudgetSnapshot(available=False)
        return BudgetSnapshot(
            available=True,
            token_used_microusd=int(data.get("token_used_microusd", 0) or 0),
            token_limit_microusd=int(data.get("token_limit_microusd", 0) or 0),
            channel_daily_used_usd=float(data.get("channel_daily_used_usd", 0.0) or 0.0),
            channel_daily_limit_usd=float(data.get("channel_daily_limit_usd", 0.0) or 0.0),
            raw=data,
        )


__all__ = [
    "BUDGET_SCHEMA_VERSION",
    "DEFAULT_GATEWAY_URL",
    "BudgetSnapshot",
    "BudgetReader",
]
