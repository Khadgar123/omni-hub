"""Stubs for SDK-backed external channels (Feishu / Discord).

These adapters intentionally do NOT import their respective SDKs.  The
real implementations live in ``agent-harness/integrations/<name>/`` as
pinned forks; the main repo only knows the **shape** of each channel so
the Channel Protocol stays uniform.

To wire a real Feishu / Discord channel:

1. ``./scripts/add_pending_harness_forks.sh feishu`` (or ``discord``) —
   pins the SDK fork under ``agent-harness/``.
2. Implement ``listen()`` + ``reply()`` in the fork, importing the SDK.
3. Replace the stub's ``configured()`` with a real probe.

The stub's ``health_check`` always reports ``ok=False`` with reason
``"not configured (requires agent-harness/integrations/<name>/)"`` so
``channel-list`` is honest about availability.
"""

from __future__ import annotations

from typing import Iterator

from .base import Channel, ChannelHealth, InboundMessage, OutboundMessage


class _ExternalSDKStub:
    """Shared stub behaviour for SDK-backed channels."""

    name = "stub"
    sdk_module: str = ""
    harness_path: str = ""

    def configured(self) -> bool:
        return False

    def health_check(self) -> ChannelHealth:
        return ChannelHealth(
            name=self.name, ok=False,
            detail={
                "configured": False,
                "reason": (
                    f"{self.name} channel requires {self.harness_path}; "
                    f"see agent-harness/manifest.json::pending_forks"
                ),
                "sdk_module": self.sdk_module,
            },
        )

    def listen(self) -> Iterator[InboundMessage]:
        raise NotImplementedError(
            f"{self.name} channel not implemented in main repo (stdlib-only "
            f"constraint).  Use {self.harness_path} to wire {self.sdk_module}."
        )

    def reply(self, msg: OutboundMessage) -> None:
        raise NotImplementedError(
            f"{self.name} channel not implemented in main repo. "
            f"Use {self.harness_path}."
        )

    def shutdown(self) -> None:
        return None


class FeishuChannel(_ExternalSDKStub):
    name = "feishu"
    sdk_module = "lark-oapi"
    harness_path = "agent-harness/integrations/feishu/"


class DiscordChannel(_ExternalSDKStub):
    name = "discord"
    sdk_module = "discord.py"
    harness_path = "agent-harness/integrations/discord/"


__all__ = ["DiscordChannel", "FeishuChannel"]
