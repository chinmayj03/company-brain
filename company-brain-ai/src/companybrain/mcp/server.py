"""ADR-006 Phase 7: company-brain MCP server — unified tool surface.

Exposes company-brain's graph capabilities as a Model Context Protocol (MCP)
server.  Two transports are supported:

  HTTP + SSE (SaaS default):
    uvicorn companybrain.mcp.server:asgi_app --host 0.0.0.0 --port 9000

  stdio (on-prem agent tier):
    python -m companybrain.mcp.server --stdio

The server is stateless — it holds no data of its own. Every tool call
delegates to one of two backends:

  BackendClient (Java REST, port 8080):
    Blast-radius CTE, annotations, human context, execution flows, pipeline
    jobs.  Multi-tenancy and ACL enforcement live in Spring Security here.

  TrpcClient (TypeScript tRPC, port 8090):
    Structural / graph queries: symbol search, call graphs, function
    signatures, file summaries, repo map, API contracts, drift signals,
    database schema.  Backed by Neo4j.

Authentication:
  HTTP transport — Bearer JWT in Authorization header, validated by the
  backend on each request (pass-through pattern).
  stdio transport — pre-shared API key in BACKEND_API_KEY env var.

All tool responses include a `_hints` field (list of {tool, suggestion} dicts)
to guide AI assistants toward the next useful tool call in the workflow.
Structural tools additionally carry ``_source: "neo4j-structural"`` so agents
know which data layer produced each result.

ADR-006 §§15–21, Phase 7 §§1–14.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

import structlog
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP

from companybrain.mcp.client import BackendClient
from companybrain.mcp.trpc_client import TrpcClient
from companybrain.mcp.tools.context import get_minimal_context
from companybrain.mcp.tools.structural import (
    find_bridges,
    find_hubs,
    find_large_functions,
    get_impact_radius,
    query_graph,
)
from companybrain.mcp.tools.semantic import (
    get_business_context,
    get_review_context,
    semantic_search_nodes,
)
from companybrain.mcp.tools.flows import (
    detect_changes,
    get_flow,
    list_flows,
)
from companybrain.mcp.tools.structural_v2 import (
    find_symbol              as _sv2_find_symbol,
    find_callers             as _sv2_find_callers,
    find_callees             as _sv2_find_callees,
    get_function_signature   as _sv2_get_function_signature,
    get_contract_for_endpoint as _sv2_get_contract_for_endpoint,
    get_drift_signals        as _sv2_get_drift_signals,
    get_table_schema         as _sv2_get_table_schema,
    get_repo_map             as _sv2_get_repo_map,
    hybrid_blast_radius      as _sv2_hybrid_blast_radius,
    hybrid_get_node_context  as _sv2_hybrid_get_node_context,
)

log = structlog.get_logger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

_BACKEND_URL  = os.getenv("BACKEND_URL",   "http://company-brain-backend:8080")
_BACKEND_KEY  = os.getenv("BACKEND_API_KEY", "")
_TRPC_API_URL = os.getenv("TRPC_API_URL",  "http://cb-api:8090/trpc")

# ── FastMCP app ────────────────────────────────────────────────────────────────

mcp_app = FastMCP(
    name="company-brain",
    instructions=(
        "company-brain gives AI assistants structural + semantic insight into a "
        "software workspace. "
        "\n\nTool groups:\n"
        "  • Orientation   — tool_get_minimal_context (start here)\n"
        "  • Structural    — find_symbol, find_callers, find_callees, "
        "get_function_signature (Neo4j via tRPC)\n"
        "  • Contracts     — get_contract_for_endpoint, get_drift_signals "
        "(Neo4j via tRPC)\n"
        "  • Database      — get_table_schema (Neo4j via tRPC)\n"
        "  • Repo overview — get_repo_map (Neo4j via tRPC)\n"
        "  • Blast radius  — tool_get_impact_radius (Postgres via Java REST)\n"
        "  • Flows         — tool_list_flows, tool_get_flow (Postgres via Java REST)\n"
        "  • Semantic      — tool_semantic_search_nodes, tool_get_business_context "
        "(Postgres via Java REST)\n"
        "  • Code health   — tool_find_hubs, tool_find_bridges, "
        "tool_find_large_functions (Postgres via Java REST)\n"
        "\nAlways start with tool_get_minimal_context, then follow the _hints in "
        "each response. Structural tool responses carry _source: 'neo4j-structural' "
        "to distinguish them from Java REST results."
    ),
)

# ── Shared clients (lifecycle managed by FastAPI lifespan) ───────────────────

_client: BackendClient | None = None
_trpc:   TrpcClient   | None = None


def _get_client() -> BackendClient:
    if _client is None:
        raise RuntimeError("BackendClient not initialised — server not started yet")
    return _client


def _get_trpc() -> TrpcClient:
    if _trpc is None:
        raise RuntimeError("TrpcClient not initialised — server not started yet")
    return _trpc


# ── Tool registrations ────────────────────────────────────────────────────────
# Each tool is a thin wrapper that injects the shared BackendClient.

@mcp_app.tool(
    description=(
        "Get a ~100-token orientation to a workspace: node counts, dominant language, "
        "top 5 hub nodes by degree, top 3 execution flows. This should be the FIRST "
        "tool called in any workflow. Follow the _hints to continue."
    )
)
async def tool_get_minimal_context(
    workspace_id: str,
    task_keywords: Optional[list[str]] = None,
) -> dict[str, Any]:
    return await get_minimal_context(
        workspace_id=workspace_id,
        task_keywords=task_keywords,
        client=_get_client(),
    )


@mcp_app.tool(
    description=(
        "Compute the blast radius for a node — all nodes reachable via dependency edges "
        "within 5 hops. direction: FORWARD (downstream dependents), REVERSE (upstream "
        "callers), BOTH (default). Supply node_id or qualified_name."
    )
)
async def tool_get_impact_radius(
    workspace_id: str,
    node_id: Optional[str] = None,
    qualified_name: Optional[str] = None,
    direction: str = "BOTH",
) -> dict[str, Any]:
    return await get_impact_radius(
        workspace_id=workspace_id,
        node_id=node_id,
        qualified_name=qualified_name,
        direction=direction,  # type: ignore[arg-type]
        client=_get_client(),
    )


@mcp_app.tool(
    description=(
        "Explore the call / import graph around a node. "
        "relation: callers_of | callees_of | imports_of | imported_by. "
        "Supply node_id or qualified_name."
    )
)
async def tool_query_graph(
    workspace_id: str,
    relation: str,
    node_id: Optional[str] = None,
    qualified_name: Optional[str] = None,
    depth: int = 2,
) -> dict[str, Any]:
    return await query_graph(
        workspace_id=workspace_id,
        node_id=node_id,
        qualified_name=qualified_name,
        relation=relation,
        depth=depth,
        client=_get_client(),
    )


@mcp_app.tool(
    description=(
        "Retrieve the LLM-extracted business context for a node: purpose, data reads/writes, "
        "risk flags, business rules. Supply node_id or qualified_name."
    )
)
async def tool_get_business_context(
    workspace_id: str,
    node_id: Optional[str] = None,
    qualified_name: Optional[str] = None,
) -> dict[str, Any]:
    return await get_business_context(
        workspace_id=workspace_id,
        node_id=node_id,
        qualified_name=qualified_name,
        client=_get_client(),
    )


@mcp_app.tool(
    description=(
        "Search for nodes by semantic meaning using full-text + embedding similarity. "
        "node_type filter: Function | Class | Method | Module | File | Service | API | Schema."
    )
)
async def tool_semantic_search_nodes(
    workspace_id: str,
    query: str,
    top_k: int = 10,
    node_type: Optional[str] = None,
) -> dict[str, Any]:
    return await semantic_search_nodes(
        workspace_id=workspace_id,
        query=query,
        top_k=top_k,
        node_type=node_type,
        client=_get_client(),
    )


@mcp_app.tool(
    description=(
        "Assemble a review-ready context bundle (structural + semantic) for a set of nodes. "
        "Typically called after detect_changes or get_impact_radius."
    )
)
async def tool_get_review_context(
    workspace_id: str,
    node_ids: list[str],
) -> dict[str, Any]:
    return await get_review_context(
        workspace_id=workspace_id,
        node_ids=node_ids,
        client=_get_client(),
    )


@mcp_app.tool(
    description=(
        "List execution flows detected in this workspace, ordered by criticality desc. "
        "Flows are BFS-traced paths from framework entry-points (HTTP handlers, event "
        "listeners, scheduled tasks). Filter by min_criticality (0.0–1.0)."
    )
)
async def tool_list_flows(
    workspace_id: str,
    min_criticality: float = 0.0,
) -> dict[str, Any]:
    return await list_flows(
        workspace_id=workspace_id,
        min_criticality=min_criticality,
        client=_get_client(),
    )


@mcp_app.tool(
    description=(
        "Retrieve the full node sequence of an execution flow. "
        "Use list_flows first to find flow IDs."
    )
)
async def tool_get_flow(
    workspace_id: str,
    flow_id: str,
) -> dict[str, Any]:
    return await get_flow(
        workspace_id=workspace_id,
        flow_id=flow_id,
        client=_get_client(),
    )


@mcp_app.tool(
    description=(
        "Find the top-N hub nodes by degree (in-degree + out-degree). "
        "Hub nodes are structural chokepoints — changes to them have the widest blast radius."
    )
)
async def tool_find_hubs(
    workspace_id: str,
    top_n: int = 20,
) -> dict[str, Any]:
    return await find_hubs(
        workspace_id=workspace_id,
        top_n=top_n,
        client=_get_client(),
    )


@mcp_app.tool(
    description=(
        "Find the top-N bridge nodes by betweenness centrality. "
        "Bridge nodes are critical single points of failure in the dependency graph."
    )
)
async def tool_find_bridges(
    workspace_id: str,
    top_n: int = 10,
) -> dict[str, Any]:
    return await find_bridges(
        workspace_id=workspace_id,
        top_n=top_n,
        client=_get_client(),
    )


@mcp_app.tool(
    description=(
        "Find functions or methods with more than min_lines lines of code. "
        "Long functions are a complexity and maintainability signal."
    )
)
async def tool_find_large_functions(
    workspace_id: str,
    min_lines: int = 50,
    top_n: int = 20,
) -> dict[str, Any]:
    return await find_large_functions(
        workspace_id=workspace_id,
        min_lines=min_lines,
        top_n=top_n,
        client=_get_client(),
    )


@mcp_app.tool(
    description=(
        "Detect nodes that changed since since_sha (or last indexed commit). "
        "Returns changed nodes sorted by risk score desc — highest-risk change first."
    )
)
async def tool_detect_changes(
    workspace_id: str,
    since_sha: Optional[str] = None,
) -> dict[str, Any]:
    return await detect_changes(
        workspace_id=workspace_id,
        since_sha=since_sha,
        client=_get_client(),
    )


# ── Phase 7: structural tools backed by tRPC / Neo4j ─────────────────────────

@mcp_app.tool(
    description=(
        "Find classes, functions, methods, or types by name pattern. "
        "Searches the Neo4j structural graph. "
        "Use kind to narrow to 'function', 'class', 'method', or 'type'. "
        "Source: neo4j-structural (tRPC)."
    )
)
async def find_symbol(
    workspace_id: str,
    pattern: str,
    kind: Optional[str] = None,
) -> dict[str, Any]:
    return await _sv2_find_symbol(
        scope=workspace_id,
        pattern=pattern,
        kind=kind,
        trpc=_get_trpc(),
    )


@mcp_app.tool(
    description=(
        "Find all callers of a function or method in the Neo4j structural graph. "
        "Supply the symbol's qualified name or UUID. "
        "Call find_symbol first if you are unsure of the exact name. "
        "Source: neo4j-structural (tRPC)."
    )
)
async def find_callers(
    workspace_id: str,
    symbol: str,
) -> dict[str, Any]:
    return await _sv2_find_callers(
        scope=workspace_id,
        symbol=symbol,
        trpc=_get_trpc(),
    )


@mcp_app.tool(
    description=(
        "Find what a function or method calls internally (its direct callees). "
        "Supply the symbol's qualified name or UUID. "
        "Source: neo4j-structural (tRPC)."
    )
)
async def find_callees(
    workspace_id: str,
    symbol: str,
) -> dict[str, Any]:
    return await _sv2_find_callees(
        scope=workspace_id,
        symbol=symbol,
        trpc=_get_trpc(),
    )


@mcp_app.tool(
    description=(
        "Get the full signature and parameter list of a function or method. "
        "Returns parameter names, types, return type, and any extracted docstring. "
        "Source: neo4j-structural (tRPC)."
    )
)
async def get_function_signature(
    workspace_id: str,
    symbol: str,
) -> dict[str, Any]:
    return await _sv2_get_function_signature(
        scope=workspace_id,
        symbol=symbol,
        trpc=_get_trpc(),
    )


@mcp_app.tool(
    description=(
        "Look up the API contract (OpenAPI spec) for a specific endpoint. "
        "Returns the OpenAPI operation object including request/response schemas. "
        "path: URL template e.g. '/api/users/{id}'. "
        "method: HTTP verb e.g. 'GET', 'POST'. "
        "Source: neo4j-structural (tRPC)."
    )
)
async def get_contract_for_endpoint(
    workspace_id: str,
    path: str,
    method: str,
) -> dict[str, Any]:
    return await _sv2_get_contract_for_endpoint(
        scope=workspace_id,
        path=path,
        method=method,
        trpc=_get_trpc(),
    )


@mcp_app.tool(
    description=(
        "Get contract-to-implementation divergence signals. "
        "Drift signals highlight where the OpenAPI contract and live code disagree "
        "(missing fields, type mismatches, undocumented endpoints). "
        "severity filter: 'low' | 'medium' | 'high'. "
        "Source: neo4j-structural (tRPC)."
    )
)
async def get_drift_signals(
    workspace_id: str,
    severity: Optional[str] = None,
) -> dict[str, Any]:
    return await _sv2_get_drift_signals(
        scope=workspace_id,
        severity=severity,
        trpc=_get_trpc(),
    )


@mcp_app.tool(
    description=(
        "Get database table columns, types, and foreign-key relationships. "
        "Combines column list and FK edges from the Neo4j graph into one schema snapshot. "
        "Source: neo4j-structural (tRPC)."
    )
)
async def get_table_schema(
    workspace_id: str,
    table_name: str,
) -> dict[str, Any]:
    return await _sv2_get_table_schema(
        scope=workspace_id,
        table_name=table_name,
        trpc=_get_trpc(),
    )


@mcp_app.tool(
    description=(
        "Get a token-budgeted overview of the repository directory and module structure. "
        "token_budget controls the approximate size of the response (default 2000 tokens). "
        "Use this for high-level orientation before drilling into specific files. "
        "Source: neo4j-structural (tRPC)."
    )
)
async def get_repo_map(
    workspace_id: str,
    token_budget: int = 2000,
) -> dict[str, Any]:
    return await _sv2_get_repo_map(
        scope=workspace_id,
        token_budget=token_budget,
        trpc=_get_trpc(),
    )


# ── FastAPI ASGI app (HTTP + SSE transport) ───────────────────────────────────

@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _client, _trpc
    log.info(
        "company-brain MCP server starting",
        backend_url=_BACKEND_URL,
        trpc_url=_TRPC_API_URL,
    )
    _client = BackendClient(base_url=_BACKEND_URL, api_key=_BACKEND_KEY)
    _trpc   = TrpcClient(base_url=_TRPC_API_URL)
    yield
    await _client.close()
    await _trpc.close()
    log.info("company-brain MCP server stopped")


asgi_app = FastAPI(
    title="company-brain MCP",
    version="1.0.0",
    description="Model Context Protocol server for company-brain's structural + semantic graph.",
    lifespan=_lifespan,
)

asgi_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Mount the FastMCP SSE handler under /mcp
asgi_app.mount("/mcp", mcp_app.sse_app())


@asgi_app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "company-brain-mcp"}


# ── stdio entry-point (on-prem agent tier) ────────────────────────────────────

def run_stdio() -> None:
    """Run the MCP server over stdio transport (on-prem agent mode)."""
    global _client, _trpc
    import asyncio

    _client = BackendClient(base_url=_BACKEND_URL, api_key=_BACKEND_KEY)
    _trpc   = TrpcClient(base_url=_TRPC_API_URL)
    log.info(
        "company-brain MCP server (stdio) starting",
        backend_url=_BACKEND_URL,
        trpc_url=_TRPC_API_URL,
    )
    mcp_app.run(transport="stdio")


# ── JSON-RPC 2.0 stdio server (ADR-0019) ─────────────────────────────────────
# Raw JSON-RPC 2.0 over stdin/stdout per the MCP spec.
# Tools are sourced from TOOL_REGISTRY (brain_query, brain_get, brain_search,
# brain_blast_radius, brain_rebuild) — Python-native, no BackendClient/TrpcClient.
#
# Run as:  python -m companybrain.mcp.server
#
# Register in ~/.claude.json:
#   {
#     "mcpServers": {
#       "company-brain": {
#         "command": "python",
#         "args": ["-m", "companybrain.mcp.server"],
#         "env": {
#           "BRAIN_REPO_ROOT": "/path/to/pilot",
#           "BRAIN_WORKSPACE_ID": "00000000-0000-0000-0000-000000000001",
#           "NEO4J_URI": "bolt://localhost:7687",
#           "QDRANT_URL": "http://localhost:6333"
#         }
#       }
#     }
#   }

import asyncio as _asyncio
import json as _json
import sys as _sys
import traceback as _traceback
from typing import Any as _Any

from companybrain.mcp.tools import TOOL_REGISTRY

_JSONRPC_VERSION = "2.0"


def _stdio_emit(payload: dict[_Any, _Any]) -> None:
    _sys.stdout.write(_json.dumps(payload) + "\n")
    _sys.stdout.flush()


def _stdio_emit_result(rid: _Any, result: _Any) -> None:
    _stdio_emit({"jsonrpc": _JSONRPC_VERSION, "id": rid, "result": result})


def _stdio_emit_error(rid: _Any, code: int, message: str, data: _Any = None) -> None:
    err: dict[str, _Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    _stdio_emit({"jsonrpc": _JSONRPC_VERSION, "id": rid, "error": err})


async def _stdio_handle(request: dict[str, _Any]) -> None:
    rid    = request.get("id")
    method = request.get("method")
    params = request.get("params", {}) or {}
    try:
        if method == "tools/list":
            _stdio_emit_result(rid, {"tools": [t.schema for t in TOOL_REGISTRY.values()]})
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments", {}) or {}
            tool = TOOL_REGISTRY.get(name)
            if tool is None:
                _stdio_emit_error(rid, -32601, f"Unknown tool: {name}")
                return
            result = await tool.handler(args)
            _stdio_emit_result(rid, {"content": [{"type": "text", "text": result}], "isError": False})
        elif method == "initialize":
            _stdio_emit_result(rid, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "company-brain", "version": "0.1.0"},
            })
        elif method == "shutdown":
            _stdio_emit_result(rid, None)
            _sys.exit(0)
        else:
            _stdio_emit_error(rid, -32601, f"Method not found: {method}")
    except Exception as exc:
        log.error("mcp_server.handler_error", method=method, error=str(exc),
                  traceback=_traceback.format_exc())
        _stdio_emit_error(rid, -32603, f"Internal error: {exc}")


def _configure_stdio_logging() -> None:
    """Redirect structlog to stderr so it doesn't pollute the JSON-RPC stream."""
    import logging
    import structlog

    logging.basicConfig(stream=_sys.stderr, level=logging.WARNING)
    structlog.configure(
        logger_factory=structlog.PrintLoggerFactory(file=_sys.stderr),
    )


async def main() -> None:
    """Read JSON-RPC requests from stdin, write responses to stdout, line-delimited."""
    _configure_stdio_logging()
    log.info("mcp_server.start")
    loop = _asyncio.get_running_loop()
    reader = _asyncio.StreamReader()
    protocol = _asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, _sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            break  # EOF — Claude Code closed the pipe
        line_str = line.decode().strip()
        if not line_str:
            continue
        try:
            request = _json.loads(line_str)
        except _json.JSONDecodeError:
            _stdio_emit_error(rid=None, code=-32700, message="Parse error")
            continue
        await _stdio_handle(request)
    log.info("mcp_server.stop")


if __name__ == "__main__":
    import sys
    if "--http" in sys.argv:
        import uvicorn
        uvicorn.run(
            "companybrain.mcp.server:asgi_app",
            host="0.0.0.0",
            port=9000,
            reload=False,
        )
    elif "--fastmcp-stdio" in sys.argv:
        run_stdio()
    else:
        # Default: JSON-RPC 2.0 stdio server (ADR-0019)
        _asyncio.run(main())


# ── get_affected_flows (registered after initial module load) ─────────────────
# Imported here to avoid circular import; tools/flows.py imports hints at call-time.

from companybrain.mcp.tools.flows import get_affected_flows as _get_affected_flows


@mcp_app.tool(
    description=(
        "Find all execution flows that contain a specific node. "
        "Use after get_impact_radius to understand which end-to-end paths are disrupted. "
        "Supply node_id or qualified_name."
    )
)
async def tool_get_affected_flows(
    workspace_id: str,
    node_id: Optional[str] = None,
    qualified_name: Optional[str] = None,
) -> dict[str, Any]:
    return await _get_affected_flows(
        workspace_id=workspace_id,
        node_id=node_id,
        qualified_name=qualified_name,
        client=_get_client(),
    )


# ── Hybrid tools (Neo4j structural + Postgres semantic) ───────────────────────

@mcp_app.tool(
    description=(
        "Get the full blast radius of a symbol using BOTH Neo4j (structural call/import graph) "
        "AND Postgres (LLM risk scores + business context). "
        "This is the definitive 'what breaks if I change X?' tool. "
        "Returns nodes sorted by: breaking risk first, then risk score descending, then graph distance. "
        "Source: neo4j-structural + postgres-semantic."
    )
)
async def hybrid_blast_radius(
    scope: str,
    node_id_or_name: str,
    depth: int = 3,
) -> dict[str, Any]:
    return await _sv2_hybrid_blast_radius(
        scope=scope,
        node_id_or_name=node_id_or_name,
        depth=depth,
        trpc=_get_trpc(),
    )


@mcp_app.tool(
    description=(
        "Get full context for a node: structural facts from Neo4j (callers, callees, importers) "
        "combined with semantic context from Postgres (LLM-generated business context, purpose, "
        "invariants, and human annotations). "
        "Use when you need to understand BOTH what a function does structurally "
        "AND what the team knows about it. "
        "Source: neo4j-structural + postgres-semantic."
    )
)
async def hybrid_get_node_context(
    scope: str,
    node_id_or_name: str,
) -> dict[str, Any]:
    return await _sv2_hybrid_get_node_context(
        scope=scope,
        node_id_or_name=node_id_or_name,
        trpc=_get_trpc(),
    )
