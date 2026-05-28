"""Tests for the local MCP server (P2-2)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.builtins import build_default_registry
from omni_hub.mcp_server import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    MCPServer,
    default_tools,
)
from omni_hub.runner import OperationRunner


def _make_server(workspace: Path) -> MCPServer:
    runner = OperationRunner(build_default_registry(workspace))
    return MCPServer(runner)


class InitializeTests(unittest.TestCase):
    def test_initialize_advertises_tool_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = _make_server(Path(tmp))
            resp = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            })
            self.assertEqual(resp["result"]["serverInfo"]["name"], SERVER_NAME)
            self.assertEqual(resp["result"]["serverInfo"]["version"], SERVER_VERSION)
            self.assertEqual(resp["result"]["protocolVersion"], PROTOCOL_VERSION)
            self.assertIn("tools", resp["result"]["capabilities"])

    def test_initialized_notification_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = _make_server(Path(tmp))
            resp = server.handle({
                "jsonrpc": "2.0", "method": "initialized",  # notification has no id
            })
            self.assertIsNone(resp)
            self.assertTrue(server.initialized)


class ToolsListTests(unittest.TestCase):
    def test_lists_curated_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = _make_server(Path(tmp))
            resp = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/list",
            })
            tool_names = {t["name"] for t in resp["result"]["tools"]}
            for required in (
                "task-enqueue", "task-list", "task-stats",
                "propose-list", "propose-approve", "propose-reject",
                "memory-search", "memory-stats",
            ):
                self.assertIn(required, tool_names)

    def test_each_tool_has_json_schema_input(self) -> None:
        for tool in default_tools():
            self.assertEqual(tool.input_schema["type"], "object")
            self.assertIn("properties", tool.input_schema)


class ToolsCallTests(unittest.TestCase):
    def test_memory_stats_routes_through_operation_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = _make_server(Path(tmp))
            resp = server.handle({
                "jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "memory-stats", "arguments": {}},
            })
            self.assertFalse(resp["result"]["isError"])
            text = resp["result"]["content"][0]["text"]
            parsed = json.loads(text)
            self.assertEqual(parsed["documents"], 0)

    def test_task_enqueue_via_mcp_lands_in_queue(self) -> None:
        from omni_hub.queue import TaskQueue
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            server = _make_server(workspace)
            resp = server.handle({
                "jsonrpc": "2.0", "id": 7, "method": "tools/call",
                "params": {
                    "name": "task-enqueue",
                    "arguments": {
                        "lane": "python",
                        "packet": {"operation": "memory_stats", "kind": "text"},
                        "idempotency_key": "mcp-1",
                    },
                },
            })
            self.assertFalse(resp["result"]["isError"])
            self.assertEqual(TaskQueue(workspace).counts_by_state()["pending"], 1)

    def test_unknown_tool_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = _make_server(Path(tmp))
            resp = server.handle({
                "jsonrpc": "2.0", "id": 9, "method": "tools/call",
                "params": {"name": "no-such-tool", "arguments": {}},
            })
            self.assertIn("error", resp)
            self.assertIn("unknown tool", resp["error"]["message"])

    def test_unknown_method_returns_method_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = _make_server(Path(tmp))
            resp = server.handle({
                "jsonrpc": "2.0", "id": 11, "method": "nonsense/foo",
            })
            self.assertEqual(resp["error"]["code"], -32601)


class StdioLoopTests(unittest.TestCase):
    """End-to-end: feed JSON-RPC lines into serve(), inspect responses."""

    def test_serve_handles_initialize_then_tools_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = _make_server(Path(tmp))
            stdin = StringIO(
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {}}) + "\n"
                + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n"
            )
            stdout = StringIO()
            server.serve(stdin=stdin, stdout=stdout)

            outputs = [json.loads(line) for line in stdout.getvalue().strip().split("\n")]
            self.assertEqual(len(outputs), 2)
            self.assertEqual(outputs[0]["id"], 1)
            self.assertIn("tools", outputs[1]["result"])

    def test_serve_returns_parse_error_for_malformed_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = _make_server(Path(tmp))
            stdin = StringIO("{not json\n")
            stdout = StringIO()
            server.serve(stdin=stdin, stdout=stdout)
            resp = json.loads(stdout.getvalue().strip())
            self.assertEqual(resp["error"]["code"], -32700)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
