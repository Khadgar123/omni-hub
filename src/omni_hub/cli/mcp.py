"""``omni-hub mcp-serve`` — start the local MCP server on stdio.

Wire this into Claude desktop / other MCP clients by adding to their
config (e.g. ``~/Library/Application Support/Claude/claude_desktop_config.json``)::

    {
      "mcpServers": {
        "omni-hub": {
          "command": "python3",
          "args": ["-m", "omni_hub.cli", "mcp-serve"],
          "env": {"PYTHONPATH": "/abs/path/to/omni-hub/src"}
        }
      }
    }

The server reads JSON-RPC requests on stdin and writes responses to
stdout — it does NOT use ``run_and_print`` because it speaks a different
protocol (the OperationRunner audit log still records each tool call).
"""

from __future__ import annotations

import argparse

from ..mcp_server import MCPServer


def register(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser(
        "mcp-serve",
        help="Start the MCP server on stdio for Claude desktop / other clients.",
    )


def _serve(args, *, runner, workspace) -> int:
    server = MCPServer(runner)
    server.serve()
    return 0


COMMANDS = {"mcp-serve": _serve}
