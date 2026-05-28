"""Per-source health probe + ``omni-hub retrieve doctor`` aggregator.

Lifts the **Channel ABC + `tier` + `doctor`** pattern from
`Panniantong/Agent-Reach` (20k★, 2026-active): every source declares a
*tier* (how much setup it needs) and exposes an optional ``check()``
method that probes auth / connectivity / library installation without
spending any retrieval quota.

The aggregator returns one row per source so a single ``omni-hub
retrieve doctor`` call surfaces which sources will work this session
versus which need user setup (env var, paid key, pinned fork install).

Tier model (matches Agent-Reach exactly):

* ``0`` — works out of the box (no API key, no env var, no fork install)
* ``1`` — needs an env var or free API key
* ``2`` — complex setup (paid key, pinned fork, broker server, etc.)

A source is healthy iff its ``check()`` returns ``("ok", detail)``; a
``"warn"`` row still serves traffic but is degraded; ``"off"`` and
``"error"`` rows fail-soft (the cascade will skip them and capture the
diagnostic in ``CascadeResult.errors``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Literal, Protocol, runtime_checkable

from .base import RetrievalSource


HealthStatus = Literal["ok", "warn", "off", "error"]
Tier = Literal[0, 1, 2]


@runtime_checkable
class CheckableSource(Protocol):
    """Optional health-probe surface every source MAY implement.

    Sources without a ``check()`` default to ``("ok", "no probe defined")``
    so the doctor still has a row for them — it just can't verify.
    """

    name: str
    tier: Tier

    def check(self) -> tuple[HealthStatus, str]: ...


@dataclass(slots=True)
class HealthReport:
    """One row in ``omni-hub retrieve doctor`` output."""

    name: str
    tier: Tier
    status: HealthStatus
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def probe_source(source: RetrievalSource) -> HealthReport:
    """Return a single ``HealthReport`` for ``source``.

    If the source implements ``check()``, call it; else return a default
    "ok / no probe" row.  Any exception from the probe collapses into
    ``status="error"`` so a buggy probe can't break the doctor command.
    """

    name = getattr(source, "name", source.__class__.__name__)
    tier: Tier = int(getattr(source, "tier", 0))           # type: ignore[assignment]

    probe = getattr(source, "check", None)
    if probe is None or not callable(probe):
        return HealthReport(
            name=name, tier=tier, status="ok", detail="no probe defined",
        )
    try:
        result = probe()
    except Exception as exc:                                # noqa: BLE001
        return HealthReport(
            name=name, tier=tier, status="error",
            detail=f"{type(exc).__name__}: {exc}",
        )
    if not isinstance(result, tuple) or len(result) != 2:
        return HealthReport(
            name=name, tier=tier, status="error",
            detail=f"probe returned {result!r} (expected (status, detail))",
        )
    status, detail = result
    if status not in ("ok", "warn", "off", "error"):
        status = "error"
        detail = f"unknown status {status!r}: {detail}"
    return HealthReport(name=name, tier=tier, status=status, detail=detail)


def probe_all(sources: Iterable[RetrievalSource]) -> list[HealthReport]:
    """Probe every source.  Order = input order, deterministic for CLI."""

    return [probe_source(s) for s in sources]


def summarise(reports: Iterable[HealthReport]) -> dict[str, int]:
    """Aggregate counts for a one-line CLI footer."""

    out: dict[str, int] = {"ok": 0, "warn": 0, "off": 0, "error": 0}
    for r in reports:
        out[r.status] = out.get(r.status, 0) + 1
    return out


def env_var_probe(var_name: str, *, tier: Tier = 1) -> tuple[HealthStatus, str]:
    """Helper for connectors that just need an env var to be set.

    Used as:

        class FooSource:
            name, tier = "foo", 1
            def check(self): return env_var_probe("FOO_API_KEY")
    """

    import os

    val = os.environ.get(var_name, "").strip()
    if val:
        masked = f"{val[:4]}…" if len(val) > 4 else "set"
        return "ok", f"{var_name}={masked}"
    return "off", f"{var_name} not set"
