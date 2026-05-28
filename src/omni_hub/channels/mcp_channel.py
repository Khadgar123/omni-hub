"""MCP Channel — wraps the MCP server as a Channel.

The MCP server in ``src/omni_hub/mcp_server.py`` (v0.14+) exposes
omni-hub operations as MCP tools.  This adapter is the **Channel-shaped
view** of that server: from the Application Plane's perspective an MCP
tool call is just an InboundMessage, and the tool return value is an
OutboundMessage.

For v0.19 this is a thin wrapper — most MCP work continues to live in
``mcp_server.py``.  The wrapper exists so ``channel-list`` / health checks
treat MCP uniformly with email / feishu / discord.
"""

from __future__ import annotations

from typing import Iterator

from .base import Channel, ChannelHealth, InboundMessage, OutboundMessage


class MCPChannel:
    name = "mcp"

    def __init__(self, *, server_label: str = "omni-hub") -> None:
        self.server_label = server_label

    def health_check(self) -> ChannelHealth:
        # MCP availability is best inferred from whether the mcp module
        # imports.  We don't probe the live server here — that would
        # require an active stdio peer.
        try:
            from .. import mcp_server  # noqa: F401
            ok = True
            detail: dict[str, object] = {"server_label": self.server_label}
        except Exception as exc:                                    # noqa: BLE001
            ok = False
            detail = {"error": str(exc)}
        return ChannelHealth(name=self.name, ok=ok, detail=detail)

    def listen(self) -> Iterator[InboundMessage]:
        # MCP listening is owned by mcp_server.main() which is driven by
        # an external client over stdio.  Channel-protocol listen here is
        # a no-op generator so the registry can include MCP without
        # forking a server thread.
        if False:                                                   # pragma: no cover
            yield InboundMessage.new(channel=self.name, sender="", body="")

    def reply(self, msg: OutboundMessage) -> None:
        # MCP tool returns are handled inline by mcp_server.dispatch(); a
        # ChannelRegistry.reply(msg) routed here is a programmer error.
        raise NotImplementedError(
            "MCP responses are returned inline from tool handlers; do not call "
            "reply() on the MCP channel."
        )

    def shutdown(self) -> None:
        return None


__all__ = ["MCPChannel"]
