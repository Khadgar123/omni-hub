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


PROTOCOL_VERSION = "2025-11-25"          # MCP spec rev we target (Q3 2026)
SERVER_NAME = "omni-hub"
SERVER_VERSION = "0.8.0"


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Tool:
    """One MCP tool — name, JSON-schema input, and OperationSpec factory.

    ``destructive`` and ``idempotent`` are surfaced as MCP "tool annotations"
    (spec 2025-11-25 §tools) — Claude Desktop / other clients use them to
    decide whether to auto-approve a call or prompt the human.  Default for
    READ_ONLY ops is ``readOnlyHint=True``; LOCAL_WRITE ops default to
    non-destructive non-idempotent unless explicitly flagged.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    operation_name: str
    risk_level: RiskLevel
    action: str = "call"
    payload_from_args: Callable[[dict[str, Any]], dict[str, Any]] = field(
        default_factory=lambda: lambda args: dict(args)
    )
    destructive: bool = False
    idempotent: bool = False
    title: str | None = None        # human-friendly label for clients

    def annotations(self) -> dict[str, Any]:
        ann: dict[str, Any] = {
            "readOnlyHint": self.risk_level == RiskLevel.READ_ONLY,
            "destructiveHint": self.destructive,
            "idempotentHint": self.idempotent,
            # We never call external services from MCP tools — every tool
            # routes to an in-process OperationRunner handler.
            "openWorldHint": False,
        }
        if self.title:
            ann["title"] = self.title
        return ann

    def to_mcp_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": self.annotations(),
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


def _boolean(description: str, default: bool = False) -> dict[str, Any]:
    return {"type": "boolean", "description": description, "default": default}


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
            idempotent=True,                     # idempotency_key dedups
            title="Enqueue Task",
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
                        "knowledge|duplicate|stale|conflict|low_signal|generation|"
                        "wiki_update|lint_finding"
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
            idempotent=True,                     # re-approve is a no-op
            title="Approve Proposal",
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
            destructive=True,                    # rejected proposals are terminal
            idempotent=True,
            title="Reject Proposal",
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
        # ----- Karpathy LLM-Wiki + claims surface (v0.11 – v0.13) -----
        Tool(
            name="wiki-status",
            description=(
                "Compiled-wiki status — directory layout, page count, claim ledger size. "
                "Use this before wiki-search to confirm the wiki is initialised."
            ),
            input_schema={"type": "object", "properties": {}},
            operation_name="wiki_status",
            risk_level=RiskLevel.READ_ONLY,
        ),
        Tool(
            name="wiki-search",
            description=(
                "Search the compiled wiki (vault/wiki/) with bitemporal + state filtering. "
                "By default skips review_state=rejected/superseded and pages whose "
                "t_valid_to lies in the past.  Set include_closed=true for the full audit view."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": _string("Query string (terms split on whitespace)"),
                    "limit": _integer("Max results", default=10),
                    "include_closed": _boolean(
                        "Include rejected/superseded/expired pages",
                        default=False,
                    ),
                },
                "required": ["query"],
            },
            operation_name="wiki_search",
            risk_level=RiskLevel.READ_ONLY,
        ),
        Tool(
            name="wiki-ingest",
            description=(
                "Bridge a retrieval cascade run (.omni/retrieval/<run_id>/) into the wiki "
                "Ingest pipeline.  Writes evidence files under vault/evidence/<domain>/ "
                "and produces a Proposal(kind=wiki_update) carrying a synthesis page body + "
                "N candidate claims (bitemporal t_valid_from set).  Approve via propose-approve "
                "then materialise via wiki-apply-proposal."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "run_id": _string("Retrieval run id under .omni/retrieval/"),
                    "domain": _string(
                        "Override the cascade domain (defaults to manifest.domain). "
                        "research|engineering|finance|policy|international_relations|"
                        "ai_progress|agent_systems|photography|fashion|chat_relationships|"
                        "social_en|social_zh"
                    ),
                    "title": _string("Override the page title (defaults to manifest.query)"),
                    "max_records": _integer("Max evidence records to ingest", default=20),
                },
                "required": ["run_id"],
            },
            operation_name="wiki_ingest",
            risk_level=RiskLevel.LOCAL_WRITE,
            idempotent=True,                     # run_id makes ingest replay-safe
            title="Wiki Ingest",
        ),
        Tool(
            name="wiki-lint",
            description=(
                "Run the six Karpathy wiki-lint rules: contradiction / stale_fact / "
                "orphan_page / missing_concept / broken_cross_ref / data_gap.  With "
                "persist=true, each finding becomes a Proposal(kind=lint_finding) "
                "pending human review.  Default (persist=false) returns findings "
                "without writing anything."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "domain": _string("Restrict to a domain folder; default = all"),
                    "stale_after_days": _integer(
                        "Override per-domain data_gap threshold", default=30
                    ),
                    "persist": _boolean(
                        "Write each finding as Proposal(kind=lint_finding)",
                        default=False,
                    ),
                },
            },
            operation_name="wiki_lint",
            risk_level=RiskLevel.LOCAL_WRITE,   # persist=True writes; pin LOCAL_WRITE conservatively
        ),
        Tool(
            name="context-pack-build",
            description=(
                "Assemble a task-specific context pack with Karpathy progressive disclosure. "
                "tier=minimal (frontmatter only, ~1k tok), standard (+snippet, ~5k tok), "
                "expanded (+body excerpts up to 8000 chars/result).  Filters closed pages "
                "by default; set include_closed=true to surface superseded content."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": _string("Task query / topic"),
                    "domain": _string("Domain tag for the pack (default: research)"),
                    "tier": _string("minimal | standard | expanded (default: standard)"),
                    "wiki_limit": _integer("Max wiki results", default=6),
                    "research_limit": _integer("Max research results", default=6),
                    "persist": _boolean("Write pack to .omni/context_packs/", default=False),
                    "include_closed": _boolean(
                        "Include rejected/superseded/expired pages", default=False,
                    ),
                },
                "required": ["query"],
            },
            operation_name="context_pack_build",
            risk_level=RiskLevel.LOCAL_WRITE,   # persist=True writes; conservative
        ),
        Tool(
            name="claims-list",
            description=(
                "List atomic claims from .omni/claims.jsonl.  By default filters out "
                "claims with t_valid_to set or review_state ∈ {rejected, superseded}.  "
                "Pass include_closed=true for the full audit view."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "state": _string(
                        "approved | proposed | conflict | superseded | rejected"
                    ),
                    "domain": _string("Filter by claim.domain field"),
                    "include_closed": _boolean(
                        "Include closed claims (t_valid_to set or state ∈ rejected/superseded)",
                        default=False,
                    ),
                    "limit": _integer("Max rows", default=50),
                },
            },
            operation_name="claims_list",
            risk_level=RiskLevel.READ_ONLY,
        ),
        Tool(
            name="claims-show",
            description=(
                "Show a single claim plus its supersession chain "
                "(supersedes + superseded_by walks in both directions)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "claim_id": _string("16-char hex claim_id"),
                },
                "required": ["claim_id"],
            },
            operation_name="claims_show",
            risk_level=RiskLevel.READ_ONLY,
        ),
        Tool(
            name="claims-stats",
            description=(
                "Aggregate claim counts: total / open / closed, plus by_state and "
                "by_domain buckets.  Open = t_valid_to is null AND state not in "
                "{rejected, superseded}."
            ),
            input_schema={"type": "object", "properties": {}},
            operation_name="claims_stats",
            risk_level=RiskLevel.READ_ONLY,
        ),
        # ----- v0.17-E: full wiki write surface for Claude Desktop -----
        Tool(
            name="wiki-apply-proposal",
            description=(
                "Materialise an approved Proposal(kind=wiki_update) into "
                "vault/wiki/.  Writes the synthesis page, appends approved "
                "claims to .omni/claims.jsonl, records a PreferenceRecord, "
                "and incrementally reindexes the FTS5 sidecar.  Proposal "
                "must already be in state=approved (use propose-approve)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "proposal": _string("Proposal id (must be state=approved)"),
                },
                "required": ["proposal"],
            },
            operation_name="wiki_apply_proposal",
            risk_level=RiskLevel.LOCAL_WRITE,
            idempotent=True,                    # second apply is a no-op-ish reapply
        ),
        Tool(
            name="wiki-supersede",
            description=(
                "Graphiti-style bitemporal supersede: close the old claim's "
                "t_valid_to, link superseded_by, append to the new claim's "
                "supersedes list.  Old claim is NEVER deleted — full audit "
                "trail preserved.  Also prunes the old page from index.md."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "new_claim_id": _string("Replacement claim id"),
                    "old_claim_id": _string("Claim being closed"),
                    "reason": _string("Short explanation (free text)"),
                },
                "required": ["new_claim_id", "old_claim_id"],
            },
            operation_name="wiki_supersede",
            risk_level=RiskLevel.LOCAL_WRITE,
            title="Wiki Supersede",
        ),
        Tool(
            name="wiki-conflict-resolve",
            description=(
                "Apply a decision to a contradiction lint_finding proposal. "
                "Decisions: keep_both (mark both review_state=conflict), "
                "reject_old / reject_new (mark one rejected), supersede "
                "(call wiki-supersede on the inferred old/new pair)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "proposal_id": _string("lint_finding proposal id"),
                    "decision": _string("keep_both | reject_old | reject_new | supersede"),
                    "new_claim_id": _string("(optional) override the auto-inferred new claim"),
                    "old_claim_id": _string("(optional) override the auto-inferred old claim"),
                    "reason": _string("(optional) reviewer note"),
                },
                "required": ["proposal_id", "decision"],
            },
            operation_name="wiki_conflict_resolve",
            risk_level=RiskLevel.LOCAL_WRITE,
            title="Wiki Conflict Resolve",
        ),
        Tool(
            name="wiki-reindex",
            description=(
                "Drop and rebuild the FTS5 sidecar from every page under "
                "vault/wiki/.  Use after manual edits to multiple pages or "
                "if wiki-doctor reports a fts5_freshness mismatch."
            ),
            input_schema={"type": "object", "properties": {}},
            operation_name="wiki_reindex",
            risk_level=RiskLevel.LOCAL_WRITE,
            idempotent=True,
        ),
        Tool(
            name="wiki-doctor",
            description=(
                "One-stop wiki integrity probe: layout, 12 domain schemas, "
                "FTS5 freshness, claims.jsonl validity, supersede graph "
                "(cycles + dangling), index.md dead links, orphan SKILL.md."
            ),
            input_schema={"type": "object", "properties": {}},
            operation_name="wiki_doctor",
            risk_level=RiskLevel.READ_ONLY,
        ),
        Tool(
            name="wiki-dream",
            description=(
                "Offline consolidation pass — local-first dual of "
                "Anthropic Dreaming.  Scans recent retrieval evidence, raw "
                "files, and claims; proposes consolidations as "
                "Proposal(kind=wiki_dream).  Heuristics: cluster_canonical "
                "(≥2 hits on same canonical_id w/ no page) / statement_cluster "
                "/ raw_orphan / stale_active."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "since_days": _integer(
                        "Window in days; 0 = full history.  Default 7 (weekly).",
                        default=7,
                    ),
                    "persist": _boolean(
                        "Write findings as Proposal(kind=wiki_dream)", default=False,
                    ),
                },
            },
            operation_name="wiki_dream",
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
        Tool(
            name="harness-compile-skill",
            description=(
                "Compile accepted preference spans into a SKILL.md file "
                "loadable by Claude Code / Codex (Anthropic Skills spec, "
                "32-tool open standard).  Output:  .agents/skills/<skill-id>/SKILL.md.  "
                "Auto-syncs to registry/skills.json after compile."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "domain": _string("Domain id (research|engineering|...)"),
                    "skill_id": _string("(optional) override kebab-case skill id"),
                    "description": _string("(optional) override description (≤1024 chars)"),
                    "max_positive": _integer("Max positive exemplars", default=10),
                    "max_negative": _integer("Max negative exemplars", default=4),
                },
                "required": ["domain"],
            },
            operation_name="harness_compile_skill",
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
        # ----- v0.17-J: Anthropic Memory Tool (memory_20250818) surface -----
        Tool(
            name="memory-tool",
            description=(
                "Anthropic Memory Tool surface (memory_20250818).  Six "
                "commands against vault/memory/: view / create / "
                "str_replace / insert / delete / rename.  Paths MUST start "
                "with /memories.  This is the on-prem dual of Anthropic's "
                "Managed Agents memory store — any agent already speaking "
                "the standard tool contract gets a working backend."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "command": _string(
                        "view | create | str_replace | insert | delete | rename"
                    ),
                    "path": _string("Path under /memories (e.g. /memories/notes/foo.md)"),
                    "file_text": _string("Body for create"),
                    "old_str": _string("Exact substring (str_replace)"),
                    "new_str": _string("Replacement (str_replace)"),
                    "insert_line": _integer("0-indexed insertion point", default=0),
                    "insert_text": _string("Text for insert"),
                    "new_path": _string("Destination for rename"),
                    "view_range": _object(
                        "[start, end] line range (view); end=-1 means EOF",
                    ),
                },
                "required": ["command", "path"],
            },
            operation_name="memory_tool",
            risk_level=RiskLevel.LOCAL_WRITE,   # view commands are READ_ONLY in practice but pin conservatively
            title="Memory Tool (memory_20250818)",
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
