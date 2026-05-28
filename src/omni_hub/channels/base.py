"""Channel Protocol + dataclasses + registry (stdlib-only).

All channels — CLI, MCP, Email, Feishu, Discord — implement the same
``Channel`` Protocol so the Application Plane never branches on channel
type.  Each ``InboundMessage`` carries the ``trace_id`` that follows the
work all the way through Knowledge Plane → Skill Plane → outbound reply,
matching v0.18-C's trace_id propagation rule.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterator, Protocol
from uuid import uuid4


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _new_trace_id(channel: str) -> str:
    """A trace_id is ``<channel>-<uuid8>``; channel prefix aids debugging."""

    return f"{channel}-{uuid4().hex[:8]}"


@dataclass(slots=True)
class InboundMessage:
    """One message arriving from a channel.

    The body is **always plain text or markdown** — channel adapters
    convert their native payload (Email MIME, Feishu rich card, Discord
    embed) into markdown before constructing the InboundMessage.
    Attachments and channel-specific identifiers live in ``metadata``.
    """

    channel: str
    trace_id: str
    sender: str
    body: str
    timestamp: str = field(default_factory=_utcnow)
    subject: str = ""                              # email subject, discord thread name, etc.
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        channel: str,
        sender: str,
        body: str,
        subject: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> "InboundMessage":
        return cls(
            channel=channel,
            trace_id=_new_trace_id(channel),
            sender=sender,
            body=body,
            subject=subject,
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class OutboundMessage:
    """One agent reply going back through a channel.

    The ``trace_id`` MUST echo the inbound message's trace_id so cross-
    channel audit + the AuditLogger can stitch the lifecycle together.
    """

    channel: str
    trace_id: str
    recipient: str
    body: str
    subject: str = ""
    in_reply_to: str = ""                         # native message id (Message-Id / discord msg id / ...)
    timestamp: str = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def in_reply_to_msg(
        cls,
        inbound: InboundMessage,
        body: str,
        *,
        subject: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> "OutboundMessage":
        """Construct a reply that echoes the inbound trace_id."""

        return cls(
            channel=inbound.channel,
            trace_id=inbound.trace_id,
            recipient=inbound.sender,
            body=body,
            subject=subject or (f"Re: {inbound.subject}" if inbound.subject else ""),
            in_reply_to=str(inbound.metadata.get("message_id", "")),
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ChannelHealth:
    """Channel health snapshot, used by ``channel-health`` CLI."""

    name: str
    ok: bool
    last_checked_at: str = field(default_factory=_utcnow)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Channel(Protocol):
    """Contract every channel adapter MUST satisfy."""

    name: str

    def health_check(self) -> ChannelHealth:
        """Quick liveness probe.  Should return within ~1s.

        For network-backed channels, prefer a cached health snapshot rather
        than a real round-trip on every call — ``channel-list`` may be hot.
        """
        ...

    def listen(self) -> Iterator[InboundMessage]:
        """Blocking generator that yields each inbound message.

        Implementations SHOULD:
          * close gracefully on ``StopIteration`` / ``KeyboardInterrupt``,
          * commit / ack each message ONLY after the caller advances the
            generator (at-least-once delivery),
          * tag each message with a fresh ``trace_id``.
        """
        ...

    def reply(self, msg: OutboundMessage) -> None:
        """Send an outbound reply.  MUST be idempotent on retries (use
        ``in_reply_to`` to deduplicate)."""
        ...

    def shutdown(self) -> None:
        """Release resources (close sockets, log out, etc.)."""
        ...


# ---------------------------------------------------------------------------
# Registry — Application Plane consumes a registry, not individual channels.
# ---------------------------------------------------------------------------


class ChannelRegistry:
    """Holds one Channel per name, exposes health + fan-out reply.

    Stdlib-only: no threading required at registration time.  Long-running
    ``listen`` loops are the responsibility of each Channel implementation;
    the registry orchestrates simple operations (health, reply-by-name).
    """

    def __init__(self) -> None:
        self._channels: dict[str, Channel] = {}

    def register(self, channel: Channel) -> None:
        if channel.name in self._channels:
            raise ValueError(f"channel {channel.name!r} already registered")
        self._channels[channel.name] = channel

    def unregister(self, name: str) -> None:
        self._channels.pop(name, None)

    def names(self) -> list[str]:
        return sorted(self._channels)

    def get(self, name: str) -> Channel:
        try:
            return self._channels[name]
        except KeyError as exc:
            raise KeyError(
                f"channel {name!r} not registered; known: {self.names()}"
            ) from exc

    def health(self) -> list[ChannelHealth]:
        out: list[ChannelHealth] = []
        for name in self.names():
            try:
                out.append(self._channels[name].health_check())
            except Exception as exc:                                # noqa: BLE001
                out.append(ChannelHealth(
                    name=name, ok=False,
                    detail={"error": str(exc), "exc_type": type(exc).__name__},
                ))
        return out

    def reply(self, outbound: OutboundMessage) -> None:
        """Dispatch the reply to its originating channel.

        Echoes the inbound trace_id (caller's responsibility) so the
        AuditLogger can stitch the lifecycle.
        """

        self.get(outbound.channel).reply(outbound)

    def shutdown(self) -> None:
        for name in list(self._channels):
            try:
                self._channels[name].shutdown()
            except Exception:                                       # noqa: BLE001
                pass
        self._channels.clear()
