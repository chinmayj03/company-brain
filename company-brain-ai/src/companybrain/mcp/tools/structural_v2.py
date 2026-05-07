"""Phase 7: Structural MCP tools backed by the tRPC / Neo4j API.

These tools supersede the Java-REST-backed structural queries for symbol,
call-graph, contract, drift, database-schema, and repo-map lookups.  The
Java REST backend (BackendClient) continues to serve blast-radius, annotations,
human context, and pipeline jobs.

Each function:
  - Accepts a ``scope`` (workspace / repo identifier) as its first argument.
  - Delegates to :class:`~companybrain.mcp.trpc_client.TrpcClient`.
  - Returns ``{"data": ..., "_hints": [...], "_source": "neo4j-structural"}``.
  - Handles a ``None`` response from the tRPC layer gracefully.
  - Never raises — errors surface as ``{"error": ..., "_hints": [...]}`` dicts.

ADR-006 Phase 7 §§7–14.
"""

from __future__ import annotations

from typing import Any, Optional

import structlog

log = structlog.get_logger(__name__)

# Marker embedded in every response so agents can tell these results come from
# the Neo4j structural graph rather than the Postgres / Java layer.
_SOURCE = "neo4j-structural"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _not_found(name: str, hints: list[dict[str, str]]) -> dict[str, Any]:
    """Return a standardised not-found response."""
    return {
        "data":    None,
        "found":   False,
        "message": f"{name!r} was not found in the structural graph.",
        "_hints":  hints,
        "_source": _SOURCE,
    }


def _ok(data: Any, hints: list[dict[str, str]]) -> dict[str, Any]:
    """Return a standardised success response."""
    return {
        "data":    data,
        "found":   True,
        "_hints":  hints,
        "_source": _SOURCE,
    }


# ── find_symbol ───────────────────────────────────────────────────────────────

async def find_symbol(
    scope: str,
    pattern: str,
    kind: Optional[str] = None,
    *,
    trpc: Any,
) -> dict[str, Any]:
    """Find classes, functions, methods, or types by name pattern.

    Searches the Neo4j structural graph for symbols whose name matches
    *pattern* (glob-style or substring).  Use *kind* to narrow results to a
    specific symbol category.

    Args:
        scope:   Workspace / repo scope identifier.
        pattern: Name pattern, e.g. ``"BillingService*"`` or ``"checkout"``.
        kind:    Optional kind filter: ``"function"``, ``"class"``, ``"method"``,
                 ``"type"``, etc.
        trpc:    Injected :class:`~companybrain.mcp.trpc_client.TrpcClient`.
    """
    log.debug("structural_v2.find_symbol", scope=scope, pattern=pattern, kind=kind)

    data = await trpc.find_symbol(scope=scope, pattern=pattern, kind=kind)

    if data is None:
        return _not_found(
            pattern,
            hints=[
                {
                    "tool": "get_repo_map",
                    "suggestion": "Browse the repo map to discover the right symbol name",
                },
                {
                    "tool": "tool_semantic_search_nodes",
                    "suggestion": "Try a semantic search if the exact name is unknown",
                },
            ],
        )

    symbols = data.get("symbols") or data.get("results") or []
    return _ok(
        data,
        hints=[
            {
                "tool": "find_callers",
                "suggestion": (
                    f"Find all callers of {symbols[0]['name']!r}"
                    if symbols else "Find callers of a matched symbol"
                ),
            },
            {
                "tool": "get_function_signature",
                "suggestion": "Inspect the full signature of a matched symbol",
            },
            {
                "tool": "tool_get_impact_radius",
                "suggestion": "Compute blast radius for a matched symbol",
            },
        ],
    )


# ── find_callers ──────────────────────────────────────────────────────────────

async def find_callers(
    scope: str,
    symbol: str,
    *,
    trpc: Any,
) -> dict[str, Any]:
    """Find all callers of a function or method.

    Returns the set of code locations in the graph that call *symbol*.  Use
    this after ``find_symbol`` to understand who depends on a particular
    function before making changes.

    Args:
        scope:  Workspace / repo scope identifier.
        symbol: Symbol UUID or qualified name.
        trpc:   Injected :class:`~companybrain.mcp.trpc_client.TrpcClient`.
    """
    log.debug("structural_v2.find_callers", scope=scope, symbol=symbol)

    data = await trpc.find_callers(scope=scope, symbol_id_or_name=symbol)

    if data is None:
        return _not_found(
            symbol,
            hints=[
                {
                    "tool": "find_symbol",
                    "suggestion": f"Confirm the symbol name with find_symbol first",
                },
            ],
        )

    callers = data.get("callers") or data.get("results") or []
    return _ok(
        data,
        hints=[
            {
                "tool": "tool_get_impact_radius",
                "suggestion": f"Compute full blast radius from {symbol!r}",
            },
            {
                "tool": "find_callees",
                "suggestion": f"Also see what {symbol!r} calls internally",
            },
            {
                "tool": "tool_get_business_context",
                "suggestion": "Get business context for the top caller",
            },
        ],
    )


# ── find_callees ──────────────────────────────────────────────────────────────

async def find_callees(
    scope: str,
    symbol: str,
    *,
    trpc: Any,
) -> dict[str, Any]:
    """Find what a function or method calls internally.

    Returns the set of symbols that *symbol* directly invokes.  Use this to
    understand the dependencies of a function before refactoring it.

    Args:
        scope:  Workspace / repo scope identifier.
        symbol: Symbol UUID or qualified name.
        trpc:   Injected :class:`~companybrain.mcp.trpc_client.TrpcClient`.
    """
    log.debug("structural_v2.find_callees", scope=scope, symbol=symbol)

    data = await trpc.find_callees(scope=scope, symbol_id_or_name=symbol)

    if data is None:
        return _not_found(
            symbol,
            hints=[
                {
                    "tool": "find_symbol",
                    "suggestion": "Confirm the symbol name with find_symbol first",
                },
            ],
        )

    return _ok(
        data,
        hints=[
            {
                "tool": "find_callers",
                "suggestion": f"Also see who calls {symbol!r}",
            },
            {
                "tool": "get_function_signature",
                "suggestion": "Inspect the signature of a callee",
            },
            {
                "tool": "tool_get_impact_radius",
                "suggestion": "Blast radius of a callee you plan to change",
            },
        ],
    )


# ── get_function_signature ────────────────────────────────────────────────────

async def get_function_signature(
    scope: str,
    symbol: str,
    *,
    trpc: Any,
) -> dict[str, Any]:
    """Get the full signature and parameter list of a function.

    Returns the function's name, parameter names and types, return type, and
    any docstring or annotation extracted from the graph.

    Args:
        scope:  Workspace / repo scope identifier.
        symbol: Symbol UUID or qualified name.
        trpc:   Injected :class:`~companybrain.mcp.trpc_client.TrpcClient`.
    """
    log.debug("structural_v2.get_function_signature", scope=scope, symbol=symbol)

    data = await trpc.get_function_signature(scope=scope, symbol_id_or_name=symbol)

    if data is None:
        return _not_found(
            symbol,
            hints=[
                {
                    "tool": "find_symbol",
                    "suggestion": "Find the exact symbol name first",
                },
            ],
        )

    return _ok(
        data,
        hints=[
            {
                "tool": "find_callers",
                "suggestion": "See who calls this function",
            },
            {
                "tool": "find_callees",
                "suggestion": "See what this function calls",
            },
            {
                "tool": "tool_get_business_context",
                "suggestion": "Get business-level explanation of this function",
            },
        ],
    )


# ── get_contract_for_endpoint ─────────────────────────────────────────────────

async def get_contract_for_endpoint(
    scope: str,
    path: str,
    method: str,
    *,
    trpc: Any,
) -> dict[str, Any]:
    """Look up the API contract (OpenAPI spec) for a specific endpoint.

    Returns the OpenAPI operation object for the given *method* + *path*
    combination, including request/response schemas and any extensions.

    Args:
        scope:  Workspace / repo scope identifier.
        path:   URL path template, e.g. ``"/api/users/{id}"``.
        method: HTTP method, e.g. ``"GET"``, ``"POST"``.
        trpc:   Injected :class:`~companybrain.mcp.trpc_client.TrpcClient`.
    """
    log.debug(
        "structural_v2.get_contract_for_endpoint",
        scope=scope,
        path=path,
        method=method,
    )

    data = await trpc.get_contract_for_endpoint(scope=scope, path=path, method=method)

    if data is None:
        return _not_found(
            f"{method.upper()} {path}",
            hints=[
                {
                    "tool": "get_drift_signals",
                    "suggestion": "Check drift signals — this endpoint may lack a contract",
                },
                {
                    "tool": "find_symbol",
                    "suggestion": "Find the handler function implementing this endpoint",
                },
            ],
        )

    contract_id = (data.get("contract") or {}).get("operationId") or data.get("operationId")
    hints = [
        {
            "tool": "get_drift_signals",
            "suggestion": "Check for contract↔implementation drift on this endpoint",
        },
        {
            "tool": "find_symbol",
            "suggestion": "Find the handler that implements this contract",
        },
    ]
    if contract_id:
        hints.insert(
            0,
            {
                "tool": "list_endpoints_implementing_contract",
                "suggestion": f"Find all endpoints implementing contract {contract_id!r}",
            },
        )

    return _ok(data, hints=hints)


# ── get_drift_signals ─────────────────────────────────────────────────────────

async def get_drift_signals(
    scope: str,
    severity: Optional[str] = None,
    *,
    trpc: Any,
) -> dict[str, Any]:
    """Get contract-to-implementation divergence signals.

    Drift signals are points where the OpenAPI contract and the live
    implementation disagree — missing fields, type mismatches, undocumented
    endpoints, etc.  Filter by *severity* to focus on the most critical gaps.

    Args:
        scope:    Workspace / repo scope identifier.
        severity: Optional filter: ``"low"``, ``"medium"``, or ``"high"``.
        trpc:     Injected :class:`~companybrain.mcp.trpc_client.TrpcClient`.
    """
    log.debug("structural_v2.get_drift_signals", scope=scope, severity=severity)

    data = await trpc.get_drift_signals(scope=scope, severity=severity)

    if data is None:
        return _ok(
            {"signals": [], "count": 0, "message": "No drift signals found."},
            hints=[
                {
                    "tool": "get_contract_for_endpoint",
                    "suggestion": "Inspect a specific endpoint's contract",
                },
            ],
        )

    signals = data.get("signals") or data.get("results") or []
    top_endpoint = (signals[0].get("endpoint") if signals else None)

    hints = [
        {
            "tool": "get_contract_for_endpoint",
            "suggestion": (
                f"Inspect the contract for the drifted endpoint {top_endpoint!r}"
                if top_endpoint else "Inspect the contract for a drifted endpoint"
            ),
        },
        {
            "tool": "find_symbol",
            "suggestion": "Find the handler implementing the drifted endpoint",
        },
    ]

    return _ok(data, hints=hints)


# ── get_table_schema ──────────────────────────────────────────────────────────

async def get_table_schema(
    scope: str,
    table_name: str,
    *,
    trpc: Any,
) -> dict[str, Any]:
    """Get database table columns, types, and relationships.

    Combines the column list (from ``findColumnsWithPattern``) and foreign-key
    edges (from ``getForeignKeys``) into a single schema snapshot for the
    requested table.

    Args:
        scope:      Workspace / repo scope identifier.
        table_name: Database table name (e.g. ``"orders"``, ``"users"``).
        trpc:       Injected :class:`~companybrain.mcp.trpc_client.TrpcClient`.
    """
    log.debug("structural_v2.get_table_schema", scope=scope, table_name=table_name)

    # Fetch columns and foreign keys concurrently.
    import asyncio as _asyncio

    columns_data, fk_data = await _asyncio.gather(
        trpc.find_columns_with_pattern(scope=scope, pattern=table_name, limit=100),
        trpc.get_foreign_keys(scope=scope, table_name=table_name),
        return_exceptions=True,
    )

    # Tolerate partial failures.
    columns: list[Any] = []
    if isinstance(columns_data, dict):
        columns = columns_data.get("columns") or columns_data.get("results") or []

    foreign_keys: list[Any] = []
    if isinstance(fk_data, dict):
        foreign_keys = fk_data.get("foreignKeys") or fk_data.get("results") or []

    if not columns and not foreign_keys:
        return _not_found(
            table_name,
            hints=[
                {
                    "tool": "find_symbol",
                    "suggestion": (
                        f"Find the entity class mapped to table {table_name!r}"
                    ),
                },
                {
                    "tool": "tool_semantic_search_nodes",
                    "suggestion": "Search for the domain entity by name",
                },
            ],
        )

    schema = {
        "table":      table_name,
        "columns":    columns,
        "foreignKeys": foreign_keys,
    }
    return _ok(
        schema,
        hints=[
            {
                "tool": "find_symbol",
                "suggestion": f"Find the entity / repository class for {table_name!r}",
            },
            {
                "tool": "tool_get_impact_radius",
                "suggestion": "Check blast radius of the entity class",
            },
            {
                "tool": "get_drift_signals",
                "suggestion": "Check for schema↔contract drift involving this table",
            },
        ],
    )


# ── get_repo_map ──────────────────────────────────────────────────────────────

async def get_repo_map(
    scope: str,
    token_budget: int = 2000,
    *,
    trpc: Any,
) -> dict[str, Any]:
    """Get a token-budgeted overview of the repository structure.

    Returns a condensed directory / module tree sized to fit within
    *token_budget* tokens.  Use this when you need a high-level orientation
    before drilling into specific files or symbols.

    Args:
        scope:        Workspace / repo scope identifier.
        token_budget: Approximate token budget (default 2000).
        trpc:         Injected :class:`~companybrain.mcp.trpc_client.TrpcClient`.
    """
    log.debug("structural_v2.get_repo_map", scope=scope, token_budget=token_budget)

    data = await trpc.get_repo_map(scope=scope, token_budget=token_budget)

    if data is None:
        return _not_found(
            scope,
            hints=[
                {
                    "tool": "tool_get_minimal_context",
                    "suggestion": "Fall back to workspace minimal context",
                },
            ],
        )

    return _ok(
        data,
        hints=[
            {
                "tool": "find_symbol",
                "suggestion": "Search for a specific symbol in the repository",
            },
            {
                "tool": "tool_get_minimal_context",
                "suggestion": "Get hub nodes and flow counts for this workspace",
            },
            {
                "tool": "get_function_signature",
                "suggestion": "Drill into a specific function's signature",
            },
        ],
    )


async def hybrid_blast_radius(
    scope: str,
    node_id_or_name: str,
    depth: int = 3,
    *,
    trpc: Any,
) -> dict[str, Any]:
    """Get the full blast radius of a symbol using BOTH Neo4j (structural) and Postgres (risk scores).

    This is the definitive impact-analysis tool. It traverses the Neo4j call/import/implement
    graph up to *depth* hops, then enriches every node with the Postgres risk score and
    business context from the Java backend.  The result is sorted: breaking changes first,
    then by risk score descending, then by graph distance.

    Use this when you need to answer "what breaks if I change X?" for production planning.

    Args:
        scope:             Workspace / repo scope identifier.
        node_id_or_name:   URN, qualified name, or plain function/class name.
        depth:             How many hops to traverse (1-5, default 3).
        trpc:              Injected TrpcClient (routes to hybridBlastRadius tRPC procedure).
    """
    log.debug(
        "structural_v2.hybrid_blast_radius",
        scope=scope,
        node=node_id_or_name,
        depth=depth,
    )

    data = await trpc.call(
        "hybridBlastRadius",
        {"scope": scope, "nodeIdOrName": node_id_or_name, "depth": depth},
    )

    if data is None:
        return _not_found(
            scope,
            hints=[
                {
                    "tool": "find_symbol",
                    "suggestion": f"Check if '{node_id_or_name}' exists under scope '{scope}'",
                },
                {
                    "tool": "get_repo_map",
                    "suggestion": "Get a repo overview to find the correct node name",
                },
            ],
        )

    node_count = data.get("mergedNodeCount", 0)
    return {
        "found": True,
        "data": data,
        "_source": "neo4j-structural+postgres-semantic",
        "_hints": [
            {
                "tool": "hybrid_get_node_context",
                "suggestion": "Get full business context for the highest-risk node",
            },
            {
                "tool": "get_drift_signals",
                "suggestion": f"Check for contract drift signals in scope '{scope}'",
            },
            {
                "tool": "get_contract_for_endpoint",
                "suggestion": "If this node is an API route, look up its OpenAPI contract",
            },
        ],
        "_stats": {
            "merged_node_count": node_count,
            "neo4j_nodes":       data.get("neo4jNodeCount", 0),
            "postgres_nodes":    data.get("postgresNodeCount", 0),
        },
    }


async def hybrid_get_node_context(
    scope: str,
    node_id_or_name: str,
    *,
    trpc: Any,
) -> dict[str, Any]:
    """Get the full context for a node: structural facts from Neo4j + semantic context from Postgres.

    Returns callers, callees, importers (Neo4j) alongside LLM-generated business context,
    purpose, invariants, and human annotations (Postgres/Java).

    Use this when you need to understand BOTH what a function does structurally
    AND what the team knows about it semantically.

    Args:
        scope:             Workspace / repo scope identifier.
        node_id_or_name:   URN, qualified name, or plain function/class name.
        trpc:              Injected TrpcClient.
    """
    log.debug(
        "structural_v2.hybrid_get_node_context",
        scope=scope,
        node=node_id_or_name,
    )

    data = await trpc.call(
        "hybridGetNodeContext",
        {"scope": scope, "nodeIdOrName": node_id_or_name},
    )

    if data is None:
        return _not_found(
            scope,
            hints=[
                {
                    "tool": "find_symbol",
                    "suggestion": f"Find the correct name for '{node_id_or_name}' in scope '{scope}'",
                },
            ],
        )

    return {
        "found": True,
        "data": data,
        "_source": "neo4j-structural+postgres-semantic",
        "_hints": [
            {
                "tool": "hybrid_blast_radius",
                "suggestion": "Compute the full blast radius for this node",
            },
            {
                "tool": "find_callers",
                "suggestion": "Get the full caller list from Neo4j",
            },
        ],
    }
