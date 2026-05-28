"""Local MCP (Model Context Protocol) server — stdlib-only, stdio JSON-RPC.

Exposes a curated subset of omni-hub operations as MCP tools so Claude
desktop, MCP-aware clients, or external Codex CLIs can interact with the
control plane without going through the omni-hub CLI itself.

Wire protocol: JSON-RPC 2.0 over stdio.  Each request on a separate line;
each response on a separate line.  This is the simplest MCP transport
("stdio" mode) — sufficient for personal use; no HTTP / WebSocket needed.

Tools exposed:

* ``task-enqueue``    — push a TaskPacket into the queue
* ``task-list``       — read pending/done tasks
* ``task-stats``      — queue depth / latency / dead count
* ``propose-list``    — list Proposal[T] by state/kind
* ``propose-approve`` — approve a pending proposal
* ``propose-reject``  — reject a pending proposal
* ``memory-search``   — substring/score search over MemoryStore
* ``memory-stats``    — documents / entities / relations counts
* ``harness-stats``   — flywheel trace stats

Each tool maps 1:1 to a registered OperationRunner handler — so MCP calls
still go through policy + audit, just like the CLI path.  Nothing
bypasses the control plane.

Spec reference: https://spec.modelcontextprotocol.io/specification/
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, TextIO

from .models import OperationSpec, OperationStatus, RiskLevel
from .runner import OperationRunner


PROTOCOL_VERSION = "2025-06-18"          # MCP spec rev we target
SERVER_NAME = "omni-hub"
SERVER_VERSION = "0.8.0"


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Tool:
    """One MCP tool — name, JSON-schema input, and OperationSpec factory."""

    name: str
    description: str
    input_schema: dict[str, Any]
    operation_name: str
    risk_level: RiskLevel
    action: str = "call"
    payload_from_args: Callable[[dict[str, Any]], dict[str, Any]] = field(
        default_factory=lambda: lambda args: dict(args)
    )

    def to_mcp_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


def _string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _integer(description: str, default: int | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer", "description": description}
    if default is not None:
        schema["default"] = default
    return schema


def _object(description: str, default: dict | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "description": description}
    if default is not None:
        schema["default"] = default
    return schema


def default_tools() -> list[Tool]:
    """The curated set of operations exposed to MCP clients."""

    return [
        Tool(
            name="task-enqueue",
            description="Enqueue a TaskPacket onto a worker lane.",
            input_schema={
                "type": "object",
                "properties": {
                    "lane": _string("python | claude | codex | openhands"),
                    "packet": _object("Task packet payload", default={}),
                    "idempotency_key": _string(
                        "Stable key; second enqueue with same key returns existing task."
                    ),
                    "domain_profile": _string("Optional domain tag."),
                    "max_attempts": _integer("Retry budget", default=3),
                },
                "required": ["lane"],
            },
            operation_name="enqueue_task",
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
        Tool(
            name="task-list",
            description="List queue rows filtered by state and/or lane.",
            input_schema={
                "type": "object",
                "properties": {
                    "state": _string("pending|claimed|done|failed|dead"),
                    "lane": _string("Lane filter"),
                    "limit": _integer("Max rows", default=50),
                },
            },
            operation_name="list_tasks",
            risk_level=RiskLevel.READ_ONLY,
        ),
        Tool(
            name="task-stats",
            description=(
                "Queue observability snapshot — depth by lane/state, oldest "
                "pending age, claim→done latency p50/p95/p99, attempts "
                "distribution, dead count."
            ),
            input_schema={"type": "object", "properties": {}},
            operation_name="task_stats",
            risk_level=RiskLevel.READ_ONLY,
        ),
        Tool(
            name="propose-list",
            description="List Proposal[T] by state and/or kind.",
            input_schema={
                "type": "object",
                "properties": {
                    "state": _string("pending|approved|rejected"),
                    "kind": _string(
                        "knowledge|duplicate|stale|conflict|low_signal|generation"
                    ),
                    "limit": _integer("Max rows", default=50),
                },
            },
            operation_name="list_proposals",
            risk_level=RiskLevel.READ_ONLY,
        ),
        Tool(
            name="propose-approve",
            description="Approve a pending proposal by id.",
            input_schema={
                "type": "object",
                "properties": {
                    "proposal_id": _string("Proposal id to approve"),
                    "reason": _string("Optional approval reason"),
                },
                "required": ["proposal_id"],
            },
            operation_name="approve_proposal",
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
        Tool(
            name="propose-reject",
            description="Reject a pending proposal by id.",
            input_schema={
                "type": "object",
                "properties": {
                    "proposal_id": _string("Proposal id to reject"),
                    "reason": _string("Optional rejection reason"),
                },
                "required": ["proposal_id"],
            },
            operation_name="reject_proposal",
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
        Tool(
            name="memory-search",
            description="Search MemoryStore (documents + entities + relations).",
            input_schema={
                "type": "object",
                "properties": {
                    "query": _string("Query string"),
                    "limit": _integer("Max results", default=10),
                },
                "required": ["query"],
            },
            operation_name="search_memory",
            risk_level=RiskLevel.READ_ONLY,
        ),
        Tool(
            name="memory-stats",
            description="Document / entity / relation counts.",
            input_schema={"type": "object", "properties": {}},
            operation_name="memory_stats",
            risk_level=RiskLevel.READ_ONLY,
        ),
    ]


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class MCPServer:
    """JSON-RPC 2.0 over stdio."""

    def __init__(
        self,
        runner: OperationRunner,
        tools: list[Tool] | None = None,
    ) -> None:
        self.runner = runner
        self.tools = tools if tools is not None else default_tools()
        self.tools_by_name = {t.name: t for t in self.tools}
        self.initialized = False

    # ------- entry point ---------------------------------------------------

    def serve(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                stdout.write(json.dumps(self._error(None, -32700, f"parse error: {exc}")) + "\n")
                stdout.flush()
                continue
            response = self.handle(request)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()

    # ------- request dispatch ---------------------------------------------

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        if request.get("jsonrpc") != "2.0":
            return self._error(request.get("id"), -32600, "jsonrpc must be '2.0'")
        method = str(request.get("method", ""))
        params = request.get("params") or {}
        request_id = request.get("id")

        # Notifications (no id) — handle silently, return None
        if request_id is None and method.startswith("notifications/"):
            return None

        if method == "initialize":
            return self._handle_initialize(request_id, params)
        if method == "initialized":
            self.initialized = True
            return None
        if method == "tools/list":
            return self._handle_tools_list(request_id)
        if method == "tools/call":
            return self._handle_tools_call(request_id, params)
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}

        return self._error(request_id, -32601, f"method not found: {method}")

    # ------- method implementations ---------------------------------------

    def _handle_initialize(
        self, request_id: Any, params: dict[str, Any],
    ) -> dict[str, Any]:
        # MCP clients send their protocol version; we echo ours.  We accept
        # any version — being permissive on the server side avoids breaking
        # older Claude desktop builds.
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    def _handle_tools_list(self, request_id: Any) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": [t.to_mcp_dict() for t in self.tools]},
        }

    def _handle_tools_call(
        self, request_id: Any, params: dict[str, Any],
    ) -> dict[str, Any]:
        name = str(params.get("name", ""))
        arguments = dict(params.get("arguments") or {})
        tool = self.tools_by_name.get(name)
        if tool is None:
            return self._error(request_id, -32602, f"unknown tool: {name}")

        spec = OperationSpec(
            name=tool.operation_name,
            action=tool.action,
            payload=tool.payload_from_args(arguments),
            risk_level=tool.risk_level,
        )
        result = self.runner.run(spec)

        if result.status is not OperationStatus.SUCCEEDED:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "status": result.status.value,
                                    "error": result.error,
                                    "policy_reason": result.policy_reason,
                                    "audit_id": result.audit_id,
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ],
                    "isError": True,
                },
            }

        text = json.dumps(result.output, ensure_ascii=False, indent=2)
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            },
        }

    # ------- helpers -------------------------------------------------------

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
