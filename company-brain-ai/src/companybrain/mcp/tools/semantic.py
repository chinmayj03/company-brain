"""ADR-006: Semantic MCP tools — ported from CRG (MIT), extended for company-brain.

Implements:
  - get_business_context  — LLM-extracted business meaning for a node
                            (purpose, risk, data access, invariants, callers, tests)
  - semantic_search_nodes — FTS + embedding search across nodes
  - get_review_context    — full review bundle for a set of changed nodes

These tools sit at tier 2–3 of the cost-tiered retrieval flow:
  structural (free) → retrieval ($0.001) → LLM synthesis ($0.01–$0.05)

CRG principle: structural tools first. Come here only when you need to understand
*what* a node does at the business level, not just how it's connected.

All tools support detail_level="minimal"|"standard"|"verbose".
Auto-enrichment: get_business_context attaches callers, callees, flow memberships,
and test coverage so a single call gives the full CRG-style structural+semantic view.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal, Optional

log = logging.getLogger(__name__)


# ── get_business_context ─────────────────────────────────────────────────────

async def get_business_context(
    *,
    workspace_id: str,
    node_id: str | None = None,
    qualified_name: str | None = None,
    detail_level: Literal["minimal", "standard", "verbose"] = "standard",
    include_structural: bool = True,
    client: Any,
) -> dict[str, Any]:
    """Retrieve LLM-extracted business context for a node, enriched with structural
    context (callers, callees, flow memberships, test coverage).

    This is the primary tool for understanding what a node DOES at the business level.
    It combines:
      - LLM-synthesised purpose, risk flags, data reads/writes, invariants
      - Structural callers (who calls this?) and callees (what does it call?)
      - Execution flow memberships (which user-facing flows touch this?)
      - Test coverage (is this node tested? by what?)

    Ported from CRG's enrich._format_node_context (MIT License).
    Extension: adds LLM-synthesised business context and risk analysis.

    Args:
        workspace_id:       UUID of the workspace.
        node_id:            UUID of the node.
        qualified_name:     Fully-qualified name (resolved to node_id if absent).
        detail_level:       minimal | standard | verbose.
        include_structural: If True (default), attach callers/callees/flows/tests.
        client:             Injected BackendClient.

    Returns:
        MCP result with:
          purpose         — one-sentence business description
          riskFlags       — list of risk observations
          dataReads       — DB tables/columns read
          dataWrites      — DB tables/columns written
          invariants      — business rules enforced
          callers         — list of node names that call this (structural)
          callees         — list of node names this calls (structural)
          flows           — execution flows this node participates in
          testedBy        — test functions that cover this node
          changeRisk      — low | medium | high
          _hints          — next tool suggestions
    """
    from companybrain.mcp.hints import build_hints

    # Resolve qualified_name → node_id
    if not node_id:
        if not qualified_name:
            raise ValueError("Supply either node_id or qualified_name")
        node_info = await client.get_node_by_qualified_name(workspace_id, qualified_name)
        node_id = node_info.get("id") or node_info.get("nodeId")
        if not node_id:
            return {
                "error": f"Node not found: {qualified_name!r}",
                "_hints": [{"tool": "semantic_search_nodes",
                            "suggestion": "Search for nodes matching this name"}],
            }

    # Fetch business context + structural enrichment in parallel
    ctx_task = asyncio.create_task(client.get_business_context(workspace_id, node_id))

    callers_task  = None
    callees_task  = None
    flows_task    = None

    if include_structural and detail_level != "minimal":
        callers_task = asyncio.create_task(
            client.query_graph(workspace_id, "callers_of", node_id)
        )
        callees_task = asyncio.create_task(
            client.query_graph(workspace_id, "callees_of", node_id)
        )
        flows_task = asyncio.create_task(
            client.get_node_flows(workspace_id, node_id)
        )

    ctx = await ctx_task

    callers: list[str] = []
    callees: list[str] = []
    flows:   list[str] = []
    tested_by: list[str] = []

    if callers_task:
        try:
            cr = await callers_task
            callers = [n.get("name", "") for n in (cr.get("nodes") or [])[:5]]
        except Exception:
            pass

    if callees_task:
        try:
            ce = await callees_task
            callees = [n.get("name", "") for n in (ce.get("nodes") or [])[:5]]
            # Extract TESTED_BY edges from callees response
            tested_by = [
                n.get("name", "") for n in (ce.get("nodes") or [])
                if n.get("edgeType") == "TESTED_BY"
            ]
        except Exception:
            pass

    if flows_task:
        try:
            fl = await flows_task
            flows = [f.get("name", "") for f in (fl.get("flows") or [])[:3]]
        except Exception:
            pass

    # Build tiered response
    if detail_level == "minimal":
        result: dict[str, Any] = {
            "purpose":    ctx.get("purpose") or ctx.get("businessContext", "")[:120],
            "changeRisk": ctx.get("changeRisk", "unknown"),
            "summary":    ctx.get("purpose", "No LLM context extracted yet."),
        }
    elif detail_level == "standard":
        result = {
            "purpose":     ctx.get("purpose") or ctx.get("businessContext", ""),
            "changeRisk":  ctx.get("changeRisk", "unknown"),
            "riskFlags":   (ctx.get("riskFlags") or [])[:3],
            "dataReads":   (ctx.get("dataReads")  or [])[:5],
            "dataWrites":  (ctx.get("dataWrites") or [])[:5],
            "callers":     callers,
            "callees":     callees,
            "flows":       flows,
            "testedBy":    tested_by,
        }
    else:  # verbose
        result = dict(ctx)
        result["callers"]   = callers
        result["callees"]   = callees
        result["flows"]     = flows
        result["testedBy"]  = tested_by

    # Add coverage warning if high-risk and no tests
    if ctx.get("changeRisk") == "high" and not tested_by:
        result.setdefault("warnings", []).append(
            "High-risk node with no detected test coverage — review carefully."
        )

    result["_hints"] = build_hints("get_business_context", result)
    return result


# ── semantic_search_nodes ────────────────────────────────────────────────────

async def semantic_search_nodes(
    *,
    workspace_id: str,
    query: str,
    top_k: int = 10,
    node_type: Optional[str] = None,
    detail_level: Literal["minimal", "standard", "verbose"] = "standard",
    client: Any,
) -> dict[str, Any]:
    """Search for nodes by semantic meaning using FTS + embeddings.

    Uses full-text search on node names/qualified names first, then falls back
    to embedding similarity when FTS returns fewer than top_k results.

    node_type filter options:
      Function | Class | ApiEndpoint | DatabaseQuery | FrontendComponent |
      SchemaField | DatabaseColumn | ExternalService  (None = search all)

    Args:
        workspace_id: UUID of the workspace.
        query:        Natural-language query, e.g. "payer competitor analysis".
        top_k:        Max results (default 10).
        node_type:    Optional type filter.
        detail_level: minimal=names+scores | standard=+file+type | verbose=full.
        client:       Injected BackendClient.
    """
    from companybrain.mcp.hints import build_hints

    raw   = await client.semantic_search_nodes(
        workspace_id, query=query, top_k=top_k, node_type=node_type
    )
    nodes = raw.get("nodes") or []

    if detail_level == "minimal":
        result: dict[str, Any] = {
            "count":   len(nodes),
            "names":   [n.get("nodeName") or n.get("name") for n in nodes[:8]],
            "summary": f"{len(nodes)} node(s) matching '{query}'",
        }
    elif detail_level == "standard":
        result = {
            "count": len(nodes),
            "nodes": [
                {
                    "name":      n.get("nodeName") or n.get("name"),
                    "type":      n.get("nodeType"),
                    "score":     round(n.get("matchScore", 0), 3),
                    "file":      n.get("qualifiedName", "").split("::")[0],
                }
                for n in nodes[:top_k]
            ],
        }
    else:
        result = dict(raw)

    result["_hints"] = build_hints("semantic_search_nodes", result)
    return result


# ── get_review_context ───────────────────────────────────────────────────────

async def get_review_context(
    *,
    workspace_id: str,
    node_ids: list[str],
    detail_level: Literal["minimal", "standard", "verbose"] = "standard",
    client: Any,
) -> dict[str, Any]:
    """Assemble a rich review-ready context bundle for a set of changed nodes.

    Combines structural context (blast radius, risk scores, callers, flows) with
    semantic context (business purpose, risk flags, invariants) for each node.
    This is the preferred single-call entrypoint for code-review workflows.

    Typically called after detect_changes or get_impact_radius to build a context
    bundle over the changed/affected nodes.

    Risk scoring:
      high   — any node with >20 affected downstream OR changeRisk=high
      medium — any node with >5 affected OR changeRisk=medium
      low    — all nodes have small blast radius and low change risk

    Args:
        workspace_id: UUID of the workspace.
        node_ids:     List of node UUIDs to build context for (max 20).
        detail_level: minimal | standard | verbose.
        client:       Injected BackendClient.
    """
    from companybrain.mcp.hints import build_hints

    if not node_ids:
        return {
            "error": "node_ids must not be empty",
            "_hints": [{"tool": "detect_changes",
                        "suggestion": "Detect changed nodes first"}],
        }

    raw  = await client.get_review_context(workspace_id, node_ids=node_ids[:20])
    bundle = raw.get("contextBundle") or []

    # Compute overall risk from bundle
    has_high   = any(b.get("changeRisk") == "high"   for b in bundle)
    has_medium = any(b.get("changeRisk") == "medium" for b in bundle)
    overall_risk = "high" if has_high else "medium" if has_medium else "low"

    test_gaps = [
        b.get("name") for b in bundle
        if not b.get("testedBy") and b.get("changeRisk") in ("high", "medium")
    ]

    if detail_level == "minimal":
        result: dict[str, Any] = {
            "risk":          overall_risk,
            "nodeCount":     len(bundle),
            "testGapCount":  len(test_gaps),
            "summary":       f"{len(bundle)} nodes reviewed — {overall_risk} risk, {len(test_gaps)} test gaps",
            "next_tool_suggestions": ["get_affected_flows", "get_impact_radius", "get_knowledge_gaps"],
        }
    elif detail_level == "standard":
        result = {
            "risk":         overall_risk,
            "nodeCount":    len(bundle),
            "testGaps":     test_gaps[:5],
            "contextBundle": [
                {
                    "name":       b.get("name"),
                    "purpose":    (b.get("purpose") or "")[:100],
                    "changeRisk": b.get("changeRisk"),
                    "callers":    (b.get("callers") or [])[:3],
                    "flows":      (b.get("flows")   or [])[:2],
                }
                for b in bundle[:10]
            ],
        }
    else:
        result = dict(raw)
        result["risk"]         = overall_risk
        result["testGaps"]     = test_gaps

    result["_hints"] = build_hints("get_review_context", result)
    return result
