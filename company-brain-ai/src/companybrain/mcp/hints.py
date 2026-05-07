"""ADR-006 Week 3: Hints engine — ported from CRG's hints.py (MIT License).

Every MCP tool response is wrapped with a `_hints` list that suggests the next
useful tools to call and explains why. This turns the MCP surface from a
collection of one-shot queries into an agent-friendly workflow guide.

Original: code_review_graph/hints.py — _INTENT_TOOLS, _WORKFLOW, _hints
Key changes from original:
  - Tool names adapted to our MCP surface (get_impact_radius, not get_impact).
  - _WORKFLOW sequences reference our 10-tool set.
  - build_hints() produces hints from both the calling tool name and keywords
    found in the result payload, so hints stay contextually relevant.

Usage::

    from companybrain.mcp.hints import build_hints

    # In any tool:
    result = {"affectedNodes": [...], ...}
    hints  = build_hints("get_impact_radius", result)
    return {"result": result, "_hints": hints}
"""

from __future__ import annotations

from typing import Any

# ── Intent → suggested tools ──────────────────────────────────────────────────
#
# Maps keyword phrases (lowercased, space-normalised) that appear in a user's
# query or in tool result payloads to the tools most likely to help next.
# This mirrors CRG's `_INTENT_TOOLS` dict.

_INTENT_TOOLS: dict[str, list[str]] = {
    # Change-impact queries
    "changed":          ["detect_changes", "get_impact_radius"],
    "changed files":    ["detect_changes", "get_impact_radius"],
    "blast radius":     ["get_impact_radius"],
    "impact":           ["get_impact_radius", "get_affected_flows"],
    "affected":         ["get_impact_radius", "get_review_context"],

    # Call-graph queries
    "who calls":        ["query_graph"],
    "callers":          ["query_graph"],
    "callees":          ["query_graph"],
    "callee":           ["query_graph"],
    "imports":          ["query_graph"],
    "dependencies":     ["query_graph", "get_impact_radius"],

    # Review and context
    "review":           ["get_review_context", "get_impact_radius"],
    "review pr":        ["get_review_context", "detect_changes", "get_impact_radius"],
    "code review":      ["get_review_context", "get_impact_radius"],
    "understand":       ["get_minimal_context", "get_business_context"],
    "explain":          ["get_business_context", "get_minimal_context"],
    "context":          ["get_minimal_context", "get_business_context"],
    "business context": ["get_business_context"],

    # Structural / architectural
    "architecture":     ["find_hubs", "find_bridges", "list_flows"],
    "hubs":             ["find_hubs"],
    "bridges":          ["find_bridges"],
    "chokepoints":      ["find_hubs", "find_bridges"],
    "critical":         ["find_hubs", "list_flows"],
    "large functions":  ["find_large_functions"],
    "complex":          ["find_large_functions", "find_hubs"],
    "risk":             ["get_impact_radius", "find_hubs", "get_knowledge_gaps"],

    # Knowledge gaps / codebase health
    "gaps":             ["get_knowledge_gaps"],
    "knowledge gaps":   ["get_knowledge_gaps"],
    "untested":         ["get_knowledge_gaps", "find_hubs"],
    "no tests":         ["get_knowledge_gaps"],
    "isolated":         ["get_knowledge_gaps"],
    "coverage":         ["get_knowledge_gaps"],
    "health":           ["get_knowledge_gaps", "find_hubs"],
    "audit":            ["get_knowledge_gaps", "get_review_context"],
    "technical debt":   ["get_knowledge_gaps", "find_large_functions"],

    # Flow queries
    "flows":            ["list_flows", "get_flow"],
    "flow":             ["list_flows", "get_flow"],
    "execution":        ["list_flows", "get_flow"],
    "entry points":     ["list_flows"],

    # Search
    "search":           ["semantic_search_nodes"],
    "find":             ["semantic_search_nodes", "query_graph"],
    "where is":         ["semantic_search_nodes"],
}

# ── Workflow sequences ─────────────────────────────────────────────────────────
#
# Ordered tool sequences for common high-level tasks. When a tool at position N
# in a workflow is called, we hint at position N+1 (and N+2 if short).
# Mirrors CRG's `_WORKFLOW` dict.

_WORKFLOW: dict[str, list[str]] = {
    "review-pr": [
        "get_minimal_context",
        "detect_changes",
        "get_impact_radius",
        "get_review_context",
        "get_business_context",
    ],
    "audit-policy": [
        "get_minimal_context",
        "semantic_search_nodes",
        "get_business_context",
        "get_impact_radius",
    ],
    "onboard-engineer": [
        "get_minimal_context",
        "list_flows",
        "find_hubs",
        "semantic_search_nodes",
    ],
    "debug-incident": [
        "detect_changes",
        "get_impact_radius",
        "get_review_context",
        "list_flows",
    ],
    "explore-codebase": [
        "get_minimal_context",
        "find_hubs",
        "find_bridges",
        "list_flows",
        "get_knowledge_gaps",
        "query_graph",
    ],
    "impact-analysis": [
        "detect_changes",
        "get_impact_radius",
        "list_flows",
        "get_review_context",
    ],
    "codebase-health": [
        "get_minimal_context",
        "get_knowledge_gaps",
        "find_hubs",
        "find_large_functions",
        "get_review_context",
    ],
}

# ── Per-tool follow-up hints ──────────────────────────────────────────────────
#
# Static hints that make sense whenever a specific tool is called,
# regardless of the result payload.

_TOOL_FOLLOWUPS: dict[str, list[tuple[str, str]]] = {
    "get_minimal_context": [
        ("get_impact_radius",     "See the full blast radius of a specific node"),
        ("semantic_search_nodes", "Search for nodes matching a concept"),
        ("list_flows",            "List execution flows in this workspace"),
    ],
    "get_impact_radius": [
        ("get_review_context",    "Assemble review context for the impacted nodes"),
        ("get_business_context",  "Get business context for the origin node"),
        ("list_flows",            "See which execution flows are affected"),
    ],
    "query_graph": [
        ("get_impact_radius",     "Get full blast radius from this node"),
        ("get_business_context",  "Get business context for any of these nodes"),
    ],
    "get_review_context": [
        ("get_impact_radius",     "Widen to full blast radius if context is incomplete"),
        ("get_business_context",  "Drill into business rules for a specific node"),
    ],
    "semantic_search_nodes": [
        ("get_business_context",  "Get business context for a matching node"),
        ("get_impact_radius",     "See blast radius of a matching node"),
        ("query_graph",           "Explore callers/callees of a matching node"),
    ],
    "get_business_context": [
        ("get_impact_radius",     "See all nodes affected if this one changes"),
        ("query_graph",           "Explore callers or imports of this node"),
    ],
    "list_flows": [
        ("get_flow",              "Drill into the most critical flow"),
        ("get_impact_radius",     "See blast radius of a flow entry-point"),
    ],
    "get_flow": [
        ("get_impact_radius",     "See blast radius of the flow entry-point"),
        ("get_review_context",    "Assemble review context for flow nodes"),
    ],
    "find_hubs": [
        ("get_impact_radius",     "Get blast radius of the top hub node"),
        ("query_graph",           "Explore callers/callees of a hub node"),
        ("find_bridges",          "Also find structural bridge nodes"),
    ],
    "find_bridges": [
        ("get_impact_radius",     "Get blast radius of the top bridge node"),
        ("find_hubs",             "Also find high-degree hub nodes"),
    ],
    "find_large_functions": [
        ("get_business_context",  "Get business context for a large function"),
        ("get_impact_radius",     "See blast radius of a large function"),
        ("get_knowledge_gaps",    "Check if these large functions are also untested hotspots"),
    ],
    "detect_changes": [
        ("get_impact_radius",     "Get full blast radius of the riskiest changed node"),
        ("get_review_context",    "Assemble review context for changed nodes"),
    ],
    "get_knowledge_gaps": [
        ("find_hubs",             "Check if untested hotspots are also high-degree hubs"),
        ("get_business_context",  "Get business context for a specific gap node"),
        ("find_large_functions",  "See if gap nodes are also oversized functions"),
        ("get_review_context",    "Assemble review bundle for the riskiest gap nodes"),
    ],
}


# ── Public API ────────────────────────────────────────────────────────────────


def build_hints(
    tool_name: str,
    result: dict[str, Any],
    *,
    keywords: list[str] | None = None,
    max_hints: int = 4,
) -> list[dict[str, str]]:
    """Build a `_hints` list for a tool response.

    Combines three hint sources:
      1. Static per-tool followups (_TOOL_FOLLOWUPS).
      2. Intent-based hints derived from *keywords* (e.g. user query tokens).
      3. Payload-derived hints: if the result has `affectedNodes`, hint at
         `get_review_context`; if it has `flows`, hint at `get_flow`; etc.

    Args:
        tool_name:  Name of the tool being called.
        result:     The tool's result payload (used for payload-derived hints).
        keywords:   Optional list of lowercase keywords from the user query.
        max_hints:  Maximum number of hints to emit (default 4).

    Returns:
        List of {"tool": str, "suggestion": str} dicts, deduplicated.
    """
    seen: set[str] = set()
    hints: list[dict[str, str]] = []

    def _add(tool: str, suggestion: str) -> None:
        if tool != tool_name and tool not in seen and len(hints) < max_hints:
            seen.add(tool)
            hints.append({"tool": tool, "suggestion": suggestion})

    # 1. Static per-tool followups
    for tool, suggestion in _TOOL_FOLLOWUPS.get(tool_name, []):
        _add(tool, suggestion)

    # 2. Keyword-based intent hints
    if keywords:
        for kw in keywords:
            kw_lower = kw.lower()
            for phrase, tools in _INTENT_TOOLS.items():
                if phrase in kw_lower or kw_lower in phrase:
                    for t in tools:
                        _add(t, f"Suggested by keyword: '{kw}'")

    # 3. Payload-derived hints
    if result.get("affectedNodes") and "get_review_context" not in seen:
        _add("get_review_context", "Assemble review context for the affected nodes")
    if result.get("flows") and "get_flow" not in seen:
        _add("get_flow", "Drill into a specific flow")
    if result.get("nodes") and "get_business_context" not in seen:
        _add("get_business_context", "Get business context for any matching node")
    if result.get("riskScore") and "get_impact_radius" not in seen:
        _add("get_impact_radius", "Explore blast radius for this high-risk node")
    # Knowledge gap signals
    if result.get("untestedHotspots") and "get_business_context" not in seen:
        _add("get_business_context", "Get business context for the top untested hotspot")
    if result.get("gapScore", 0) > 10 and "find_hubs" not in seen:
        _add("find_hubs", "High gap score — check if untested nodes are also architectural hubs")
    if result.get("warnings") and "get_knowledge_gaps" not in seen:
        _add("get_knowledge_gaps", "Warnings detected — check for broader coverage gaps")

    return hints


def workflow_hints(
    workflow: str,
    current_tool: str,
    max_hints: int = 2,
) -> list[dict[str, str]]:
    """Return the next 1–2 tools in a named workflow sequence.

    Useful when a client sends a `X-Workflow` header indicating which
    high-level task is being executed (e.g. `review-pr`).

    Args:
        workflow:     Name of the workflow (key in _WORKFLOW).
        current_tool: The tool just called.
        max_hints:    Maximum workflow hints to emit.

    Returns:
        List of {"tool": str, "suggestion": str} dicts.
    """
    sequence = _WORKFLOW.get(workflow, [])
    try:
        idx = sequence.index(current_tool)
    except ValueError:
        return []

    hints = []
    for next_tool in sequence[idx + 1: idx + 1 + max_hints]:
        hints.append({
            "tool": next_tool,
            "suggestion": f"Next step in the '{workflow}' workflow",
        })
    return hints
