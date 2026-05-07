"""ADR-006: Structural MCP tools — ported from CRG (MIT), extended for company-brain.

Implements:
  - get_impact_radius     — bidirectional blast radius with risk scoring
  - query_graph           — callers_of / callees_of / imports_of / imported_by
  - find_hubs             — top-N nodes by degree + risk score
  - find_bridges          — top-N bridge nodes by betweenness centrality
  - find_large_functions  — largest functions by line count
  - get_knowledge_gaps    — untested hotspots, isolated nodes, no-context nodes

All tools support detail_level="minimal"|"standard"|"verbose" for token efficiency.
CRG principle: always call get_minimal_context first, use "minimal" unless you need more.

detail_level:
  "minimal"  — counts + summary only (~50 tokens). Start here.
  "standard" — top entities + key fields (~200 tokens). Default.
  "verbose"  — full lists + all fields (~500+ tokens). Use sparingly.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

log = logging.getLogger(__name__)

_VALID_RELATIONS  = frozenset({"callers_of", "callees_of", "imports_of", "imported_by"})
_VALID_DIRECTIONS = frozenset({"FORWARD", "REVERSE", "BOTH"})


# ── get_impact_radius ────────────────────────────────────────────────────────

async def get_impact_radius(
    *,
    workspace_id: str,
    node_id: str | None = None,
    qualified_name: str | None = None,
    direction: Literal["FORWARD", "REVERSE", "BOTH"] = "BOTH",
    detail_level: Literal["minimal", "standard", "verbose"] = "standard",
    client: Any,
) -> dict[str, Any]:
    """Compute the bidirectional blast radius for a node.

    Finds all nodes reachable within the configured max depth via dependency
    edges. Use FORWARD for downstream dependents, REVERSE for upstream callers,
    BOTH for full blast radius (default).

    Risk is estimated from affected count: >20 nodes = high, >5 = medium, else low.

    Args:
        workspace_id:   UUID of the workspace.
        node_id:        UUID of the seed node.
        qualified_name: Fully-qualified name (resolved to node_id if absent).
        direction:      FORWARD | REVERSE | BOTH.
        detail_level:   minimal | standard | verbose.
        client:         Injected BackendClient.
    """
    from companybrain.mcp.hints import build_hints

    if direction not in _VALID_DIRECTIONS:
        raise ValueError(f"direction must be one of {sorted(_VALID_DIRECTIONS)}, got {direction!r}")

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

    raw     = await client.get_blast_radius(workspace_id, node_id, direction)
    affected = raw.get("affectedNodes") or []
    total    = len(affected)
    risk     = "high" if total > 20 else "medium" if total > 5 else "low"

    if detail_level == "minimal":
        result: dict[str, Any] = {
            "risk":          risk,
            "affectedCount": total,
            "depth":         raw.get("traversalDepth", 0),
            "summary":       f"{total} nodes affected — {risk} risk",
            "next_tool_suggestions": ["get_business_context", "query_graph", "get_affected_flows"],
        }
    elif detail_level == "standard":
        result = dict(raw)
        result["risk"]          = risk
        result["affectedCount"] = total
        result["affectedNodes"] = affected[:15]   # cap for token budget
        result.pop("rawEdges", None)
    else:
        result = dict(raw)
        result["risk"]          = risk
        result["affectedCount"] = total

    result["_hints"] = build_hints("get_impact_radius", result)
    return result


# ── query_graph ──────────────────────────────────────────────────────────────

async def query_graph(
    *,
    workspace_id: str,
    pattern: Literal["callers_of", "callees_of", "imports_of", "imported_by"],
    target: str,
    detail_level: Literal["minimal", "standard", "verbose"] = "standard",
    client: Any,
) -> dict[str, Any]:
    """Traverse the call graph from a named node.

    Patterns:
      callers_of   — who calls this function?
      callees_of   — what does this function call?
      imports_of   — what does this module import?
      imported_by  — what imports this module?

    Args:
        pattern:      Traversal direction (see above).
        target:       Node name or qualified name to start from.
        detail_level: minimal | standard | verbose.
    """
    from companybrain.mcp.hints import build_hints

    if pattern not in _VALID_RELATIONS:
        raise ValueError(f"pattern must be one of {sorted(_VALID_RELATIONS)}, got {pattern!r}")

    raw   = await client.query_graph(workspace_id, pattern, target)
    nodes = raw.get("nodes") or []

    if detail_level == "minimal":
        result: dict[str, Any] = {
            "count":   len(nodes),
            "names":   [n.get("name") for n in nodes[:10]],
            "summary": f"{len(nodes)} result(s) for {pattern.replace('_', ' ')} {target!r}",
        }
    elif detail_level == "standard":
        result = dict(raw)
        result["nodes"] = nodes[:20]
    else:
        result = dict(raw)

    result["_hints"] = build_hints("query_graph", result)
    return result


# ── find_hubs ────────────────────────────────────────────────────────────────

async def find_hubs(
    *,
    workspace_id: str,
    top_n: int = 10,
    detail_level: Literal["minimal", "standard", "verbose"] = "standard",
    client: Any,
) -> dict[str, Any]:
    """Find the most connected nodes (highest in+out degree).

    Hub nodes are architectural chokepoints. Risk scores (0–1) indicate how
    dangerous modifications would be — high-degree + no tests = highest risk.

    Use get_knowledge_gaps to find which hubs are untested.

    Args:
        top_n:        How many hubs to return (default 10, max 50).
        detail_level: minimal=names+risk | standard=+file+degree | verbose=all.
    """
    from companybrain.mcp.hints import build_hints

    top_n  = min(top_n, 50)
    raw    = await client.find_hubs(workspace_id, top_n=top_n)
    hubs   = raw.get("hubs") or []

    if detail_level == "minimal":
        result: dict[str, Any] = {
            "count":   len(hubs),
            "topHubs": [
                {"name": h.get("name"), "riskScore": h.get("riskScore", 0)}
                for h in hubs[:5]
            ],
            "summary": f"Top {min(5, len(hubs))} hubs by connection degree",
        }
    elif detail_level == "standard":
        result = {
            "count": len(hubs),
            "hubs":  [
                {k: h[k] for k in ("name", "nodeType", "riskScore", "rank") if k in h}
                for h in hubs[:top_n]
            ],
        }
    else:
        result = dict(raw)

    result["_hints"] = build_hints("find_hubs", result)
    return result


# ── find_bridges ─────────────────────────────────────────────────────────────

async def find_bridges(
    *,
    workspace_id: str,
    top_n: int = 10,
    detail_level: Literal["minimal", "standard", "verbose"] = "standard",
    client: Any,
) -> dict[str, Any]:
    """Find architectural chokepoints by betweenness centrality.

    Bridge nodes sit on shortest paths between many node pairs. Breaking them
    disconnects large portions of the codebase. These require the most careful
    change management and test coverage.

    Args:
        top_n:        How many bridges to return (default 10).
        detail_level: minimal | standard | verbose.
    """
    from companybrain.mcp.hints import build_hints

    raw     = await client.find_bridges(workspace_id, top_n=top_n)
    bridges = raw.get("bridges") or []

    if detail_level == "minimal":
        result: dict[str, Any] = {
            "count":      len(bridges),
            "topBridges": [b.get("name") for b in bridges[:5]],
            "summary":    f"{len(bridges)} bridge nodes (architectural chokepoints)",
        }
    elif detail_level == "standard":
        result = dict(raw)
        result["bridges"] = bridges[:top_n]
    else:
        result = dict(raw)

    result["_hints"] = build_hints("find_bridges", result)
    return result


# ── find_large_functions ─────────────────────────────────────────────────────

async def find_large_functions(
    *,
    workspace_id: str,
    min_lines: int = 50,
    detail_level: Literal["minimal", "standard", "verbose"] = "standard",
    client: Any,
) -> dict[str, Any]:
    """Find functions that exceed a line-count threshold.

    Large functions are prime refactoring candidates and frequently lack test
    coverage. Cross-reference with get_knowledge_gaps(untested_hotspots).

    Args:
        min_lines:    Only return functions with >= this many lines (default 50).
        detail_level: minimal | standard | verbose.
    """
    from companybrain.mcp.hints import build_hints

    raw       = await client.find_large_functions(workspace_id, min_lines=min_lines)
    functions = raw.get("functions") or []

    if detail_level == "minimal":
        result: dict[str, Any] = {
            "count":   len(functions),
            "largest": [f.get("name") for f in functions[:5]],
            "summary": f"{len(functions)} functions with ≥{min_lines} lines",
        }
    elif detail_level == "standard":
        result = dict(raw)
        result["functions"] = functions[:20]
    else:
        result = dict(raw)

    result["_hints"] = build_hints("find_large_functions", result)
    return result


# ── get_knowledge_gaps ───────────────────────────────────────────────────────

async def get_knowledge_gaps(
    *,
    workspace_id: str,
    detail_level: Literal["minimal", "standard", "verbose"] = "standard",
    client: Any,
) -> dict[str, Any]:
    """Identify structural weaknesses in the codebase knowledge graph.

    Ported from CRG's analysis.find_knowledge_gaps (MIT License).
    Extended with a 'no_context_nodes' category for nodes that have never been
    processed by the company-brain LLM extraction pipeline.

    Categories:
      untested_hotspots  — high-degree nodes with no TESTED_BY edge (highest risk)
      isolated_nodes     — degree <= 1, disconnected from the graph
      no_context_nodes   — never processed by LLM pipeline (no business context)
      thin_communities   — communities with fewer than 3 members

    detail_level:
      "minimal"  — counts only + summary sentence
      "standard" — top 5 per category
      "verbose"  — full lists

    Use this tool after get_minimal_context to find where to focus effort next.
    """
    from companybrain.mcp.hints import build_hints

    try:
        raw = await client.get_knowledge_gaps(workspace_id)
    except Exception as exc:
        log.warning("get_knowledge_gaps: backend call failed: %s", exc)
        raw = {}

    untested    = raw.get("untestedHotspots") or []
    isolated    = raw.get("isolatedNodes")    or []
    no_context  = raw.get("noContextNodes")   or []
    thin_comms  = raw.get("thinCommunities")  or []

    total_gaps = len(untested) + len(isolated) + len(no_context) + len(thin_comms)

    if detail_level == "minimal":
        result: dict[str, Any] = {
            "gapScore":         total_gaps,
            "untestedHotspots": len(untested),
            "isolatedNodes":    len(isolated),
            "noContextNodes":   len(no_context),
            "thinCommunities":  len(thin_comms),
            "summary": (
                f"{len(untested)} untested hotspots · "
                f"{len(isolated)} isolated nodes · "
                f"{len(no_context)} missing LLM context · "
                f"{len(thin_comms)} thin communities"
            ),
        }
    elif detail_level == "standard":
        result = {
            "gapScore":         total_gaps,
            "untestedHotspots": [
                {"name": n.get("name"), "degree": n.get("degree", 0)}
                for n in untested[:5]
            ],
            "isolatedNodes":   [n.get("name") for n in isolated[:5]],
            "noContextNodes":  [n.get("name") for n in no_context[:5]],
            "thinCommunities": [
                {"name": c.get("name"), "size": c.get("size", 0)}
                for c in thin_comms[:5]
            ],
            "summary": f"Gap score {total_gaps}. Top priority: {len(untested)} untested hotspots.",
        }
    else:  # verbose
        result = {
            "gapScore":         total_gaps,
            "untestedHotspots": untested,
            "isolatedNodes":    isolated,
            "noContextNodes":   no_context,
            "thinCommunities":  thin_comms,
        }

    result["_hints"] = build_hints("get_knowledge_gaps", result)
    return result
