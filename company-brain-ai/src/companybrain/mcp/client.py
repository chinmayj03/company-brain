"""ADR-006 Week 3: HTTP client that wraps the Spring Boot backend REST API.

Every MCP tool delegates to the backend via this client rather than talking
directly to Postgres. This keeps the MCP service stateless and ensures that
ACL / multi-tenancy enforcement remains in the backend's Spring Security layer.

Usage::

    from companybrain.mcp.client import BackendClient

    client = BackendClient(base_url="http://localhost:8080", api_key="...")
    result = await client.get_blast_radius(workspace_id, node_id)
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urljoin

import httpx

log = logging.getLogger(__name__)

# Default timeout for backend calls (seconds).
_DEFAULT_TIMEOUT = 30.0


class BackendClient:
    """Async HTTP client for the company-brain Spring Boot backend.

    One instance is created at server start-up and shared across requests
    (httpx.AsyncClient is connection-pool-aware and safe for concurrent use).
    """

    def __init__(self, base_url: str, api_key: str, timeout: float = _DEFAULT_TIMEOUT):
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=timeout,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ── Structural tools ──────────────────────────────────────────────────────

    async def get_blast_radius(
        self,
        workspace_id: str,
        node_id: str,
        direction: str = "BOTH",
    ) -> dict[str, Any]:
        """GET /api/workspaces/{wid}/blast-radius/{nid}?direction=BOTH"""
        resp = await self._client.get(
            f"/api/workspaces/{workspace_id}/blast-radius/{node_id}",
            params={"direction": direction},
        )
        resp.raise_for_status()
        return resp.json()

    async def query_graph(
        self,
        workspace_id: str,
        node_id: str,
        relation: str,           # callers_of | callees_of | imports_of | imported_by
        depth: int = 2,
    ) -> dict[str, Any]:
        """GET /api/workspaces/{wid}/graph/query"""
        resp = await self._client.get(
            f"/api/workspaces/{workspace_id}/graph/query",
            params={"nodeId": node_id, "relation": relation, "depth": depth},
        )
        resp.raise_for_status()
        return resp.json()

    async def find_hubs(
        self,
        workspace_id: str,
        top_n: int = 20,
    ) -> dict[str, Any]:
        """GET /api/workspaces/{wid}/graph/hubs"""
        resp = await self._client.get(
            f"/api/workspaces/{workspace_id}/graph/hubs",
            params={"topN": top_n},
        )
        resp.raise_for_status()
        return resp.json()

    async def find_bridges(
        self,
        workspace_id: str,
        top_n: int = 10,
    ) -> dict[str, Any]:
        """GET /api/workspaces/{wid}/graph/bridges"""
        resp = await self._client.get(
            f"/api/workspaces/{workspace_id}/graph/bridges",
            params={"topN": top_n},
        )
        resp.raise_for_status()
        return resp.json()

    async def find_large_functions(
        self,
        workspace_id: str,
        min_lines: int = 50,
        top_n: int = 20,
    ) -> dict[str, Any]:
        """GET /api/workspaces/{wid}/graph/large-functions"""
        resp = await self._client.get(
            f"/api/workspaces/{workspace_id}/graph/large-functions",
            params={"minLines": min_lines, "topN": top_n},
        )
        resp.raise_for_status()
        return resp.json()

    # ── Semantic / business-context tools ─────────────────────────────────────

    async def get_business_context(
        self,
        workspace_id: str,
        node_id: str,
    ) -> dict[str, Any]:
        """GET /api/workspaces/{wid}/nodes/{nid}/context"""
        resp = await self._client.get(
            f"/api/workspaces/{workspace_id}/nodes/{node_id}/context",
        )
        resp.raise_for_status()
        return resp.json()

    async def semantic_search_nodes(
        self,
        workspace_id: str,
        query: str,
        top_k: int = 10,
        node_type: Optional[str] = None,
    ) -> dict[str, Any]:
        """POST /api/workspaces/{wid}/nodes/search"""
        body: dict[str, Any] = {"query": query, "topK": top_k}
        if node_type:
            body["nodeType"] = node_type
        resp = await self._client.post(
            f"/api/workspaces/{workspace_id}/nodes/search",
            json=body,
        )
        resp.raise_for_status()
        return resp.json()

    async def get_review_context(
        self,
        workspace_id: str,
        node_ids: list[str],
    ) -> dict[str, Any]:
        """POST /api/workspaces/{wid}/review-context"""
        resp = await self._client.post(
            f"/api/workspaces/{workspace_id}/review-context",
            json={"nodeIds": node_ids},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_workspace_summary(
        self,
        workspace_id: str,
    ) -> dict[str, Any]:
        """GET /api/workspaces/{wid}/summary — used by get_minimal_context."""
        resp = await self._client.get(f"/api/workspaces/{workspace_id}/summary")
        resp.raise_for_status()
        return resp.json()

    async def detect_changes(
        self,
        workspace_id: str,
        since_sha: Optional[str] = None,
    ) -> dict[str, Any]:
        """GET /api/workspaces/{wid}/changes"""
        params: dict[str, str] = {}
        if since_sha:
            params["sinceSha"] = since_sha
        resp = await self._client.get(
            f"/api/workspaces/{workspace_id}/changes",
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Flows ─────────────────────────────────────────────────────────────────

    async def list_flows(
        self,
        workspace_id: str,
        min_criticality: float = 0.0,
    ) -> dict[str, Any]:
        """GET /api/workspaces/{wid}/flows"""
        resp = await self._client.get(
            f"/api/workspaces/{workspace_id}/flows",
            params={"minCriticality": min_criticality},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_flow(
        self,
        workspace_id: str,
        flow_id: str,
    ) -> dict[str, Any]:
        """GET /api/workspaces/{wid}/flows/{fid}"""
        resp = await self._client.get(
            f"/api/workspaces/{workspace_id}/flows/{flow_id}",
        )
        resp.raise_for_status()
        return resp.json()

    async def get_node_by_qualified_name(
        self,
        workspace_id: str,
        qualified_name: str,
    ) -> dict[str, Any]:
        """GET /api/workspaces/{wid}/nodes?qualifiedName=..."""
        resp = await self._client.get(
            f"/api/workspaces/{workspace_id}/nodes",
            params={"qualifiedName": qualified_name},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_affected_flows(
        self,
        workspace_id: str,
        node_id: str,
    ) -> dict[str, Any]:
        """GET /api/workspaces/{wid}/nodes/{nid}/flows — flows containing this node."""
        resp = await self._client.get(
            f"/api/workspaces/{workspace_id}/nodes/{node_id}/flows",
        )
        resp.raise_for_status()
        return resp.json()
