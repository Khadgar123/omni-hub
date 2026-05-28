"""CLI Channel — wraps stdin/stdout for the Channel Protocol.

In normal use the CLI subcommands run synchronously so the CLI channel is
*not* the long-running listener — it's a thin adapter that lets the
Application Plane treat one-shot ``omni-hub`` invocations the same way it
treats an inbound email.  Useful for tests + simple scripted flows.
"""

from __future__ import annotations

import sys
from typing import Iterator

from .base import Channel, ChannelHealth, InboundMessage, OutboundMessage


class CLIChannel:
    name = "cli"

    def __init__(self, *, user: str = "cli-user") -> None:
        self.user = user
        self._stdin = sys.stdin
        self._stdout = sys.stdout

    def health_check(self) -> ChannelHealth:
        return ChannelHealth(
            name=self.name, ok=True,
            detail={"user": self.user, "stdin_isatty": self._stdin.isatty()},
        )

    def listen(self) -> Iterator[InboundMessage]:
        """Yield one line at a time from stdin until EOF.

        Mainly for tests / pipeline scripts; real interactive use happens
        through the argparse-backed ``omni-hub`` entrypoint, not here.
        """

        for line in self._stdin:
            body = line.rstrip("\n")
            if not body:
                continue
            yield InboundMessage.new(
                channel=self.name, sender=self.user, body=body,
            )

    def reply(self, msg: OutboundMessage) -> None:
        self._stdout.write(msg.body + "\n")
        self._stdout.flush()

    def shutdown(self) -> None:
        # Don't close stdin/stdout — they're shared with the process.
        return None


__all__ = ["CLIChannel"]
