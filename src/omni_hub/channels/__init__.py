"""Interface Plane — Channel abstraction (v0.19).

The Channel Plane is the unified surface for **inbound user messages** and
**outbound agent replies**.  Where v0.7 had only CLI + v0.14 added MCP, v0.19
formalises both under one Protocol and adds a stdlib-only Email channel.

External-SDK channels (Feishu / Discord / Slack) live as **adapter shims**
in this package but their real implementations sit in
``agent-harness/integrations/<channel>/`` (pinned forks) so the main repo
stays 100% Python stdlib.

Usage::

    from omni_hub.channels import (
        Channel,
        ChannelRegistry,
        InboundMessage,
        OutboundMessage,
    )

    registry = ChannelRegistry()
    registry.register(EmailChannel(...))

    for inbound in registry.fan_in():
        # route to Application Plane (task_router)
        outbound = route_and_handle(inbound)
        registry.reply(outbound)

The Application Plane (``src/omni_hub/app/``) consumes ``InboundMessage`` and
emits ``OutboundMessage`` — it never touches a channel SDK directly.
"""

from __future__ import annotations

from .base import (
    Channel,
    ChannelHealth,
    ChannelRegistry,
    InboundMessage,
    OutboundMessage,
)
from .cli_channel import CLIChannel
from .email_channel import EmailChannel, EmailChannelConfig
from .external_stubs import DiscordChannel, FeishuChannel
from .mcp_channel import MCPChannel

__all__ = [
    "Channel",
    "ChannelHealth",
    "ChannelRegistry",
    "CLIChannel",
    "DiscordChannel",
    "EmailChannel",
    "EmailChannelConfig",
    "FeishuChannel",
    "InboundMessage",
    "MCPChannel",
    "OutboundMessage",
]
