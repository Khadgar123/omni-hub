"""W3C Trace Context helpers (SEAM A, refactor step 9).

omni-hub stamps a UUID4 ``trace_id`` on every OperationSpec and now carries
it through the TaskQueue (step 1).  To correlate the claude-lane hop
``omni-hub -> ccLoad -> metapi -> upstream`` we emit a standard
``traceparent`` header so the gateway forks (and any OTel backend) can
stitch the spans together.

Spec: https://www.w3.org/TR/trace-context/ — ``traceparent`` is
``<version>-<trace-id>-<parent-id>-<flags>`` =
``00-<32 hex>-<16 hex>-01``.  Both ids must be non-all-zero.

Pure stdlib.  Deterministic: the parent/span id is derived from the
trace_id by hash, so the same logical operation yields the same header on
retry (no RNG → reproducible traces + testable).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

TRACECONTEXT_SCHEMA_VERSION = "v0.47"

_HEX_RE = re.compile(r"[^0-9a-f]")
_ZERO_TRACE = "0" * 32
_ZERO_SPAN = "0" * 16


def _trace_hex(trace_id: str) -> str:
    """32-hex trace-id: reuse the uuid hex when possible, else hash."""
    compact = _HEX_RE.sub("", trace_id.lower())
    if len(compact) >= 32 and compact[:32] != _ZERO_TRACE:
        return compact[:32]
    return hashlib.sha256(trace_id.encode("utf-8")).hexdigest()[:32]


def _span_hex(trace_id: str) -> str:
    """16-hex parent/span id derived deterministically from trace_id."""
    span = hashlib.sha256(("span:" + trace_id).encode("utf-8")).hexdigest()[:16]
    return span if span != _ZERO_SPAN else "00000000000000a1"


def make_traceparent(trace_id: str | None) -> str:
    """Build a W3C ``traceparent`` value, or ``""`` when trace_id is missing.

    Returning ``""`` lets callers omit the header entirely rather than
    emit an invalid all-zero trace id.
    """
    if not trace_id:
        return ""
    return f"00-{_trace_hex(trace_id)}-{_span_hex(trace_id)}-01"


@dataclass(slots=True)
class TraceParent:
    version: str
    trace_id: str
    parent_id: str
    flags: str

    @property
    def sampled(self) -> bool:
        try:
            return bool(int(self.flags, 16) & 0x01)
        except ValueError:
            return False


def parse_traceparent(value: str | None) -> TraceParent | None:
    """Parse a ``traceparent`` header; ``None`` if malformed."""
    if not value:
        return None
    parts = value.strip().split("-")
    if len(parts) != 4:
        return None
    version, trace_id, parent_id, flags = parts
    if (
        len(version) != 2
        or len(trace_id) != 32
        or len(parent_id) != 16
        or len(flags) != 2
        or trace_id == _ZERO_TRACE
        or parent_id == _ZERO_SPAN
    ):
        return None
    if _HEX_RE.sub("", trace_id.lower()) != trace_id.lower():
        return None
    return TraceParent(version=version, trace_id=trace_id, parent_id=parent_id, flags=flags)


def trace_headers(trace_id: str | None) -> dict[str, str]:
    """Convenience: ``{'traceparent': ...}`` or ``{}`` when no trace_id."""
    tp = make_traceparent(trace_id)
    return {"traceparent": tp} if tp else {}


__all__ = [
    "TRACECONTEXT_SCHEMA_VERSION",
    "TraceParent",
    "make_traceparent",
    "parse_traceparent",
    "trace_headers",
]
