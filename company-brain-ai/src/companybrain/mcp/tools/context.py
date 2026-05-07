"""ADR-006 Week 3: get_minimal_context MCP tool.

Ported from CRG's tools/context.py::get_minimal_context (MIT License).
Key changes:
  - Delegates to company-brain Spring Boot backend instead of SQLite.
  - Returns workspace-level summary + top hubs + top flows in ~100 tokens.
  - Appends _hints to guide the agent toward the next most useful tool.

This is the recommended *first* tool for any AI assistant connecting to
company-brain. It gives the agent an orientation (node counts, dominant
language, top risk nodes, active flows) without overwhelming the context window.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


async def get_minimal_context(
    *,
    workspace_id: str,
    task_keywords: list[str] | None = None,
    client: Any,          # BackendClient — injected by server.py
) -> dict[str, Any]:
    """Return a ~100-token orientation to a workspace.

    Aggregates workspace summary stats, the top 5 hub nodes by degree,
    and the top 3 most critical flows. Suggests next tools via _hints.

    Args:
        workspace_id:   UUID of the workspace.
        task_keywords:  Optional keywords from the user's task description
                        (used to specialise hint suggestions).
        client:         Injected BackendClient instance.

    Returns:
        MCP result dict with keys:
          summary      — workspace stats (nodeCount, edgeCount, language, ...)
          topHubs      — list of top 5 hub nodes with risk scores
          topFlows     — list of top 3 flows by criticality
          _hints       — suggested next tools
    """
    from companybrain.mcp.hints import build_hints

    # Fetch workspace summary, top hubs, top flows in parallel
    import asyncio
    summary_task   = asyncio.create_task(client.get_workspace_summary(workspace_id))
    hubs_task      = asyncio.create_task(client.find_hubs(workspace_id, top_n=5))
    flows_task     = asyncio.create_task(client.list_flows(workspace_id, min_criticality=0.3))

    try:
        summary = await summary_task
    except Exception as exc:
        log.warning("get_minimal_context: workspace summary failed: %s", exc)
        summary = {}

    try:
        hubs_raw = await hubs_task
    except Exception as exc:
        log.warning("get_minimal_context: find_hubs failed: %s", exc)
        hubs_raw = {"hubs": []}

    try:
        flows_raw = await flows_task
    except Exception as exc:
        log.warning("get_minimal_context: list_flows failed: %s", exc)
        flows_raw = {"flows": []}

    top_hubs = (hubs_raw.get("hubs") or [])[:5]
    top_flows = sorted(
        (flows_raw.get("flows") or []),
        key=lambda f: f.get("criticality", 0),
        reverse=True,
    )[:3]

    result: dict[str, Any] = {
        "summary":  summary,
        "topHubs":  top_hubs,
        "topFlows": top_flows,
    }

    result["_hints"] = build_hints(
        "get_minimal_context",
        result,
        keywords=task_keywords or [],
    )
    return result
