"""brain-as-MCP server (ADR-0052 P5).

Exposes the *agent-facing* slice of the brain over the Model Context Protocol
so external tools (IDEs, Claude Code, ChatGPT, internal automations) can
query it without going through the FastAPI HTTP API.

Design choices:

* **Stateless** — each tool call reopens the JSON store. The server holds no
  caches of its own; restarts and reloads are cheap.
* **Read-only by default** — write tools require the ``mcp_writes`` capability
  flag, which the server itself enforces. Production deployments leave that
  off; only short-lived author tools turn it on.
* **Two transports** — ``run_sse(port=...)`` for the standard HTTP+SSE wire
  format used by Claude Code, and ``run_stdio()`` for embedded clients.

The existing :mod:`companybrain.mcp.server` is the *backend-facing* MCP
(Java REST + tRPC clients). This new server is independent — it talks
directly to the per-repo ``.brain/`` JSON store so it can be spun up against
any workspace without the backend stack running.

Tools exposed (read-only):
  * ``query_brain(question)``       — natural-language query
  * ``read_entity(urn)``            — fetch one entity
  * ``list_entities_by_file(file)`` — entities anchored to a file
  * ``find_callers(urn)``           — incoming CALLS edges
  * ``find_dependencies(urn)``      — outgoing CALLS / READS / WRITES edges

Tools exposed when ``mcp_writes`` is set:
  * ``upsert_entity(payload)``      — write one entity to the JSON store
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from companybrain.harness.workspace import Workspace
from companybrain.store.base import BrainEntity
from companybrain.store.identity import workspace_slug_for
from companybrain.store.json_store import JsonFileBrainStore

log = structlog.get_logger(__name__)


# ── Tool descriptors ────────────────────────────────────────────────────────


@dataclass
class _BrainTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]


def _empty_object_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "additionalProperties": False}


# ── Server ──────────────────────────────────────────────────────────────────


class BrainMCPServer:
    """Brain-as-MCP server with both SSE-HTTP and stdio transports.

    Construct with a :class:`Workspace`; call :meth:`run_sse` for HTTP, or
    :meth:`run_stdio` for line-delimited JSON-RPC over stdin/stdout.
    """

    PROTOCOL_VERSION = "2024-11-05"
    SERVER_NAME      = "company-brain"
    SERVER_VERSION   = "0.1.0"

    def __init__(self, workspace: Workspace, *, allow_writes: bool = False):
        self.workspace = workspace
        self.allow_writes = bool(allow_writes)
        self._store = JsonFileBrainStore(workspace.repo_path / ".brain")
        self._tools: dict[str, _BrainTool] = self._build_tools()

    # ── tool surface ───────────────────────────────────────────────────────

    def list_tools(self) -> list[dict[str, Any]]:
        """MCP-compatible tools/list payload."""
        return [
            {
                "name":        t.name,
                "description": t.description,
                "inputSchema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """MCP-compatible tools/call dispatch. Wraps the handler's return into a
        standard ``{content, isError}`` payload."""
        tool = self._tools.get(name)
        if tool is None:
            return _error_payload(f"Unknown tool: {name!r}")
        try:
            result = tool.handler(arguments or {})
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as exc:  # noqa: BLE001
            log.exception("brain_mcp.tool_error", tool=name)
            return _error_payload(f"{type(exc).__name__}: {exc}")
        return _ok_payload(result)

    # ── transport: stdio ──────────────────────────────────────────────────

    async def run_stdio(self) -> None:
        """Line-delimited JSON-RPC 2.0 over stdin/stdout."""
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        log.info("brain_mcp.stdio.start", workspace=self.workspace.id)
        while True:
            line = await reader.readline()
            if not line:
                break
            line_str = line.decode().strip()
            if not line_str:
                continue
            try:
                req = json.loads(line_str)
            except json.JSONDecodeError:
                _stdio_emit_error(None, -32700, "Parse error")
                continue
            await self._handle_stdio(req)
        log.info("brain_mcp.stdio.stop")

    async def _handle_stdio(self, req: dict[str, Any]) -> None:
        rid    = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        if method == "initialize":
            _stdio_emit_result(rid, {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities":    {"tools": {}},
                "serverInfo":      {"name": self.SERVER_NAME, "version": self.SERVER_VERSION},
            })
        elif method == "tools/list":
            _stdio_emit_result(rid, {"tools": self.list_tools()})
        elif method == "tools/call":
            payload = await self.call_tool(params.get("name", ""), params.get("arguments") or {})
            _stdio_emit_result(rid, payload)
        elif method == "shutdown":
            _stdio_emit_result(rid, None)
        else:
            _stdio_emit_error(rid, -32601, f"Method not found: {method}")

    # ── transport: SSE / HTTP ─────────────────────────────────────────────

    async def run_sse(self, *, host: str = "127.0.0.1", port: int = 8765) -> None:
        """Mount on FastAPI and serve until cancelled.

        We expose three routes — the JSON-RPC bridge at ``/mcp`` for tools/list
        and tools/call, ``/health``, and a friendly ``GET /`` summary.
        """
        try:
            import uvicorn
        except ImportError as exc:
            raise RuntimeError(
                "uvicorn is required to run the brain-as-MCP HTTP transport"
            ) from exc

        app = self.build_asgi_app()
        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    def build_asgi_app(self) -> FastAPI:
        """Build a FastAPI ASGI app exposing this MCP server."""
        app = FastAPI(title="company-brain MCP (harness)", version=self.SERVER_VERSION)

        # Return type annotations are intentionally omitted on these handlers:
        # `from __future__ import annotations` turns them into forward refs,
        # which FastAPI's response-model machinery then tries (and fails) to
        # resolve via Pydantic. The actual runtime payloads are unchanged.

        @app.get("/")
        async def root() -> dict[str, Any]:
            return {
                "service":   "company-brain harness MCP",
                "version":   self.SERVER_VERSION,
                "workspace": self.workspace.id,
                "repo_path": str(self.workspace.repo_path),
                "tools":     [t.name for t in self._tools.values()],
            }

        @app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        @app.post("/mcp")
        async def jsonrpc(req: Request) -> JSONResponse:
            try:
                body = await req.json()
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"jsonrpc": "2.0", "error":
                    {"code": -32700, "message": f"parse: {exc}"}}, status_code=400)
            method = body.get("method")
            params = body.get("params") or {}
            rid    = body.get("id")
            if method == "initialize":
                return JSONResponse({
                    "jsonrpc": "2.0", "id": rid,
                    "result": {
                        "protocolVersion": self.PROTOCOL_VERSION,
                        "capabilities":    {"tools": {}},
                        "serverInfo":      {"name": self.SERVER_NAME,
                                              "version": self.SERVER_VERSION},
                    },
                })
            if method == "tools/list":
                return JSONResponse({"jsonrpc": "2.0", "id": rid,
                                      "result": {"tools": self.list_tools()}})
            if method == "tools/call":
                payload = await self.call_tool(params.get("name", ""),
                                                params.get("arguments") or {})
                return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": payload})
            return JSONResponse(
                {"jsonrpc": "2.0", "id": rid,
                 "error": {"code": -32601, "message": f"method not found: {method}"}},
                status_code=404,
            )

        return app

    # ── tool implementations ──────────────────────────────────────────────

    def _build_tools(self) -> dict[str, _BrainTool]:
        tools: list[_BrainTool] = [
            _BrainTool(
                name="query_brain",
                description="Natural-language query against the brain's stored entities.",
                input_schema={
                    "type": "object",
                    "properties": {"question": {"type": "string"}},
                    "required": ["question"],
                },
                handler=self._tool_query_brain,
            ),
            _BrainTool(
                name="read_entity",
                description="Read one entity by URN or qualified name.",
                input_schema={
                    "type": "object",
                    "properties": {"urn": {"type": "string"}},
                    "required": ["urn"],
                },
                handler=self._tool_read_entity,
            ),
            _BrainTool(
                name="list_entities_by_file",
                description="Return URNs for all entities anchored to one file path.",
                input_schema={
                    "type": "object",
                    "properties": {"file": {"type": "string"}},
                    "required": ["file"],
                },
                handler=self._tool_list_entities_by_file,
            ),
            _BrainTool(
                name="find_callers",
                description="Return URNs of entities with a CALLS edge into the given URN.",
                input_schema={
                    "type": "object",
                    "properties": {"urn": {"type": "string"}},
                    "required": ["urn"],
                },
                handler=self._tool_find_callers,
            ),
            _BrainTool(
                name="find_dependencies",
                description="Return outgoing edges (CALLS / READS / WRITES) of one entity.",
                input_schema={
                    "type": "object",
                    "properties": {"urn": {"type": "string"}},
                    "required": ["urn"],
                },
                handler=self._tool_find_dependencies,
            ),
        ]
        if self.allow_writes:
            tools.append(_BrainTool(
                name="upsert_entity",
                description="Write one entity (capability: mcp_writes — guarded).",
                input_schema={
                    "type": "object",
                    "properties": {"entity": {"type": "object"}},
                    "required": ["entity"],
                },
                handler=self._tool_upsert_entity,
            ))
        return {t.name: t for t in tools}

    # The tool handlers all take a dict and return JSON-serialisable Python.

    async def _tool_query_brain(self, args: dict[str, Any]) -> dict[str, Any]:
        question = str(args.get("question") or "").strip()
        if not question:
            raise ValueError("query_brain requires 'question'")
        # Cheap surface-level retrieval: scan known entity types and pick out
        # those whose t1_summary or qualified_name contains a token from the
        # question. Heavy retrieval (Qdrant/Neo4j) is optional and lives in
        # the larger backend MCP — we keep this server cold-start friendly
        # so an IDE can launch it against a fresh `.brain/` directory.
        tokens = [t.lower() for t in question.split() if len(t) >= 4]
        hits: list[dict[str, Any]] = []
        async for urn in self._store.list_ids():
            ent = await self._store.read(urn)
            if not ent:
                continue
            if not tokens:
                hits.append(_summarise_entity(ent))
                continue
            query_text = ""
            if isinstance(ent.metadata, dict):
                query_text = (ent.metadata.get("query_text") or "").lower()
            haystack = " ".join([
                ent.qualified_name.lower(),
                (ent.t1_summary or "").lower(),
                query_text,
            ])
            if any(tok in haystack for tok in tokens):
                hits.append(_summarise_entity(ent))
            if len(hits) >= 50:
                break
        return {
            "question": question,
            "matches":  hits,
            "match_count": len(hits),
        }

    async def _tool_read_entity(self, args: dict[str, Any]) -> dict[str, Any]:
        urn = str(args.get("urn") or "").strip()
        if not urn:
            raise ValueError("read_entity requires 'urn'")
        ent = await self._store.read(urn)
        if ent is None:
            return {"urn": urn, "found": False}
        return {"urn": urn, "found": True, "entity": ent.to_dict()}

    async def _tool_list_entities_by_file(self, args: dict[str, Any]) -> dict[str, Any]:
        file_q = str(args.get("file") or "").strip()
        if not file_q:
            raise ValueError("list_entities_by_file requires 'file'")
        out: list[str] = []
        async for urn in self._store.list_ids():
            ent = await self._store.read(urn)
            if ent and ent.file and (ent.file == file_q or ent.file.endswith(file_q)):
                out.append(ent.id)
        return {"file": file_q, "urns": out, "count": len(out)}

    async def _tool_find_callers(self, args: dict[str, Any]) -> dict[str, Any]:
        target_urn = str(args.get("urn") or "").strip()
        if not target_urn:
            raise ValueError("find_callers requires 'urn'")
        target_qname = _qname_from_urn(target_urn)
        callers: list[str] = []
        async for urn in self._store.list_ids():
            ent = await self._store.read(urn)
            if not ent:
                continue
            for rel in ent.relationships or []:
                if rel.get("edge_type") not in {"CALLS", "INVOKES", "DELEGATES_TO"}:
                    continue
                tgt = str(rel.get("target_id") or "")
                if tgt == target_urn or tgt.endswith(target_qname):
                    callers.append(ent.id)
                    break
        return {"target": target_urn, "callers": callers, "count": len(callers)}

    async def _tool_find_dependencies(self, args: dict[str, Any]) -> dict[str, Any]:
        urn = str(args.get("urn") or "").strip()
        if not urn:
            raise ValueError("find_dependencies requires 'urn'")
        ent = await self._store.read(urn)
        if ent is None:
            return {"urn": urn, "found": False, "edges": []}
        edges = [
            {
                "target":     rel.get("target_id"),
                "edge_type":  rel.get("edge_type"),
                "confidence": rel.get("confidence"),
            }
            for rel in ent.relationships or []
        ]
        return {"urn": urn, "found": True, "edges": edges, "count": len(edges)}

    async def _tool_upsert_entity(self, args: dict[str, Any]) -> dict[str, Any]:
        if not self.allow_writes:
            raise RuntimeError("upsert_entity disabled — start server with allow_writes=True")
        payload = args.get("entity") or {}
        if not isinstance(payload, dict) or "id" not in payload:
            raise ValueError("upsert_entity 'entity' must be a dict with at least 'id'")
        ent = BrainEntity.from_dict(payload)
        await self._store.write(ent, run_id="mcp", workspace_id=self.workspace.id)
        return {"id": ent.id, "ok": True}


# ── helpers ────────────────────────────────────────────────────────────────


def build_server(
    *,
    workspace_id: str,
    brain_root: str | Path,
    allow_writes: bool = False,
) -> BrainMCPServer:
    """Convenience constructor used by the CLI.

    `brain_root` is the workspace's repo root (the parent of ``.brain``). The
    server is tightly bound to that directory.
    """
    repo_path = Path(brain_root).resolve()
    ws = Workspace.load(repo_path, workspace_id=workspace_id,
                        workspace_slug=workspace_slug_for(workspace_id))
    return BrainMCPServer(ws, allow_writes=allow_writes)


def _ok_payload(result: Any) -> dict[str, Any]:
    return {
        "content":  [{"type": "text", "text": json.dumps(result, default=str)}],
        "isError":  False,
        "structuredContent": result if isinstance(result, dict) else {"value": result},
    }


def _error_payload(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def _summarise_entity(ent: BrainEntity) -> dict[str, Any]:
    return {
        "urn":           ent.id,
        "entity_type":   ent.entity_type,
        "qualified_name": ent.qualified_name,
        "file":          ent.file,
        "summary":       ent.t1_summary[:280] if ent.t1_summary else "",
    }


def _qname_from_urn(urn: str) -> str:
    """Best-effort qname extraction so callers can pass either a URN or a qname."""
    if "::" in urn:
        return urn.rsplit("::", 1)[-1]
    if ":" in urn:
        # Canonical urn:cb:...:qname
        return urn.split(":")[-1]
    return urn


def _stdio_emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _stdio_emit_result(rid: Any, result: Any) -> None:
    _stdio_emit({"jsonrpc": "2.0", "id": rid, "result": result})


def _stdio_emit_error(rid: Any, code: int, message: str) -> None:
    _stdio_emit({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


__all__ = ["BrainMCPServer", "build_server"]


# ── module entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(prog="brain-mcp")
    parser.add_argument("--workspace", required=True, help="Workspace UUID")
    parser.add_argument("--repo", required=True, help="Path to the repo root")
    parser.add_argument("--http", action="store_true",
                         help="Serve over HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--allow-writes", action="store_true",
                         help="Expose mutating tools (mcp_writes capability).")
    ns = parser.parse_args()

    srv = build_server(
        workspace_id=ns.workspace,
        brain_root=ns.repo,
        allow_writes=ns.allow_writes,
    )
    if ns.http:
        asyncio.run(srv.run_sse(host=ns.host, port=ns.port))
    else:
        asyncio.run(srv.run_stdio())
