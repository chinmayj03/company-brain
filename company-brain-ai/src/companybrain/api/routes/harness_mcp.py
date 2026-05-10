"""HTTP route exposing the brain-as-MCP server (ADR-0052 P5).

External clients hit ``POST /mcp/harness`` with standard JSON-RPC 2.0 envelopes
(``initialize`` / ``tools/list`` / ``tools/call``). The handler picks the
workspace + repo from query params (or settings), constructs a
:class:`BrainMCPServer` per request, and proxies the call.

This is deliberately stateless — IDEs reconnect frequently and constructing
the server is cheap. If a deployment wants a persistent in-process server
they can use ``brain mcp serve --http`` instead.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from companybrain.harness.mcp_server import build_server

router = APIRouter()


def _default_repo() -> str:
    return os.environ.get("BRAIN_REPO_ROOT") or os.getcwd()


def _default_workspace() -> str:
    return os.environ.get("BRAIN_WORKSPACE_ID", "00000000-0000-0000-0000-000000000001")


@router.get("")
async def root(
    repo: str = Query(default_factory=_default_repo),
    workspace: str = Query(default_factory=_default_workspace),
):
    """One-shot ``GET`` for IDE discovery — returns the server's tool list."""
    server = build_server(workspace_id=workspace, brain_root=Path(repo))
    return {
        "service":   "company-brain harness MCP",
        "workspace": workspace,
        "repo":      str(Path(repo).resolve()),
        "tools":     server.list_tools(),
    }


@router.post("")
async def jsonrpc(
    req: Request,
    repo: str = Query(default_factory=_default_repo),
    workspace: str = Query(default_factory=_default_workspace),
):
    """JSON-RPC 2.0 bridge for ``initialize`` / ``tools/list`` / ``tools/call``."""
    try:
        body = await req.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"parse: {exc}") from exc

    method = body.get("method")
    params = body.get("params") or {}
    rid    = body.get("id")
    server = build_server(workspace_id=workspace, brain_root=Path(repo))

    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0", "id": rid,
            "result": {
                "protocolVersion": server.PROTOCOL_VERSION,
                "capabilities":    {"tools": {}},
                "serverInfo":      {"name": server.SERVER_NAME,
                                      "version": server.SERVER_VERSION},
            },
        })
    if method == "tools/list":
        return JSONResponse({"jsonrpc": "2.0", "id": rid,
                              "result": {"tools": server.list_tools()}})
    if method == "tools/call":
        payload = await server.call_tool(
            params.get("name", ""), params.get("arguments") or {},
        )
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": payload})
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rid,
         "error": {"code": -32601, "message": f"method not found: {method}"}},
        status_code=404,
    )


__all__ = ["router"]
