"""ADR-006 Week 3: Flow MCP tools.

Implements:
  - list_flows  — list detected execution flows with criticality scores
  - get_flow    — fetch the full node sequence of a specific flow

Flows are BFS-traced execution paths originating from framework entry points
(HTTP handlers, event listeners, scheduled tasks, etc.), ported from CRG's
flows.py::trace_flows.  They are persisted in the `flows` + `flow_memberships`
tables by the structural indexer (flows.py, which ships in Week 4).

If flows have not yet been detected for a workspace (structural indexer not
yet run), both tools return an empty list with a hint to trigger indexing.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


async def list_flows(
    *,
    workspace_id: str,
    min_criticality: float = 0.0,
    client: Any,
) -> dict[str, Any]:
    """List execution flows detected in this workspace.

    Flows are ordered by criticality (descending).  Each flow includes:
      id, name, entryNodeId, entryNodeName, depth, nodeCount, fileCount,
      criticality (0.0–1.0).

    Criticality combines fan-in (how many other flows call this entry point),
    the risk scores of nodes in the flow, and whether the flow touches
    security-sensitive code paths.

    Args:
        workspace_id:    UUID of the workspace.
        min_criticality: Filter to flows with criticality >= this value.
        client:          Injected BackendClient.

    Returns:
        MCP result with flows list and _hints.
    """
    from companybrain.mcp.hints import build_hints

    result = await client.list_flows(workspace_id, min_criticality=min_criticality)

    flows = result.get("flows") or []
    if not flows:
        log.info(
            "list_flows: no flows for workspace %s (structural indexer may not have run yet)",
            workspace_id,
        )

    result["_hints"] = build_hints("list_flows", result)
    return result


async def get_flow(
    *,
    workspace_id: str,
    flow_id: str,
    client: Any,
) -> dict[str, Any]:
    """Retrieve the full node sequence of an execution flow.

    Returns every node in the flow path in traversal order, with each node's:
      nodeId, nodeName, nodeType, qualifiedName, riskScore, position.

    Use `list_flows` first to find flow IDs, or look for flowMembership on
    any node returned by `get_impact_radius`.

    Args:
        workspace_id: UUID of the workspace.
        flow_id:      UUID of the flow to retrieve.
        client:       Injected BackendClient.

    Returns:
        MCP result with flow metadata + nodes list and _hints.
    """
    from companybrain.mcp.hints import build_hints

    result = await client.get_flow(workspace_id, flow_id)
    result["_hints"] = build_hints("get_flow", result)
    return result


async def detect_changes(
    *,
    workspace_id: str,
    since_sha: str | None = None,
    client: Any,
) -> dict[str, Any]:
    """Detect recently changed nodes and their risk scores.

    Queries the structural index for files changed since *since_sha* (or
    since the last indexed commit if omitted).  Returns affected nodes sorted
    by risk score, ready to feed into `get_impact_radius` or `get_review_context`.

    Args:
        workspace_id: UUID of the workspace.
        since_sha:    Git SHA to diff from (defaults to last indexed commit).
        client:       Injected BackendClient.

    Returns:
        MCP result with changedNodes list (sorted by riskScore desc) and _hints.
    """
    from companybrain.mcp.hints import build_hints

    result = await client.detect_changes(workspace_id, since_sha=since_sha)

    # Sort changed nodes by risk score descending so the agent sees the
    # riskiest change first — mirrors CRG's change detection sort order.
    changed = result.get("changedNodes") or []
    changed.sort(key=lambda n: n.get("riskScore") or 0.0, reverse=True)
    result["changedNodes"] = changed

    result["_hints"] = build_hints("detect_changes", result)
    return result


async def get_affected_flows(
    *,
    workspace_id: str,
    node_id: str | None = None,
    qualified_name: str | None = None,
    client: Any,
) -> dict[str, Any]:
    """Find all execution flows that contain a specific node.

    Useful after `get_impact_radius` to understand which end-to-end paths
    are disrupted when a node changes.  Returns flows ordered by criticality.

    Args:
        workspace_id:   UUID of the workspace.
        node_id:        UUID of the node to check.
        qualified_name: Fully-qualified name (resolved to node_id if absent).
        client:         Injected BackendClient.

    Returns:
        MCP result with affectedFlows list (flowId, name, criticality, nodePosition) and _hints.
    """
    from companybrain.mcp.hints import build_hints

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

    result = await client.get_affected_flows(workspace_id, node_id)
    result["_hints"] = build_hints("list_flows", result)
    return result
