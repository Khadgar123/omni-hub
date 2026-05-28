"""command-* introspection CLI (v0.18-B)."""

from __future__ import annotations

import argparse

from ..command_registry import build_default_command_registry
from ._common import print_json


def register(subparsers: argparse._SubParsersAction) -> None:
    describe = subparsers.add_parser(
        "command-describe",
        help=(
            "Emit a JSON-schema bundle of every typed command "
            "(Pydantic/BAML/Anthropic-tool-spec compatible).  Use to wire "
            "omni-hub commands as tools in an external LLM agent."
        ),
    )
    describe.add_argument(
        "--name", default="",
        help="Restrict to a single command name (default: all)",
    )


def _describe(args, *, runner, workspace) -> int:
    registry = build_default_command_registry()
    if args.name:
        defn = registry.get(args.name)
        print_json(defn.to_dict())
    else:
        print_json(registry.describe())
    return 0


COMMANDS = {
    "command-describe": _describe,
}
