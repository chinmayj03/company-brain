"""Unit tests for the brain-as-MCP server (ADR-0052 P5)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from companybrain.harness.mcp_server import (
    BrainMCPServer,
    build_server,
)
from companybrain.harness.workspace import Workspace
from companybrain.store.base import BrainEntity
from companybrain.store.json_store import JsonFileBrainStore


async def _seed_brain(repo: Path) -> None:
    """Drop one entity into the json store so tools/call has data to find."""
    store = JsonFileBrainstore_factory(repo)
    ent = BrainEntity(
        id="urn:cb:dev:code:demo:method:Foo.bar",
        entity_type="function_node",
        repo="demo",
        file="src/Foo.java",
        qualified_name="Foo.bar",
        t1_summary="Returns the bar value for getPayerCompetitors.",
        relationships=[{
            "target_id": "urn:cb:dev:code:demo:method:Bar.baz",
            "edge_type": "CALLS",
            "confidence": 0.9,
        }],
    )
    await store.write(ent, run_id="r1", workspace_id="ws-test")


def JsonFileBrainstore_factory(repo: Path) -> JsonFileBrainStore:
    return JsonFileBrainStore(repo / ".brain")


def _make_server(tmp_path: Path) -> BrainMCPServer:
    ws = Workspace.load(tmp_path, workspace_id="ws-test", workspace_slug="dev")
    return BrainMCPServer(ws)


async def test_list_tools_includes_query_brain(tmp_path: Path):
    server = _make_server(tmp_path)
    names = {t["name"] for t in server.list_tools()}
    assert "query_brain" in names
    assert "read_entity" in names
    assert "find_callers" in names
    assert "find_dependencies" in names
    assert "list_entities_by_file" in names


async def test_query_brain_returns_match(tmp_path: Path):
    await _seed_brain(tmp_path)
    server = _make_server(tmp_path)
    payload = await server.call_tool("query_brain", {"question": "payer competitors"})
    assert payload["isError"] is False
    structured = payload["structuredContent"]
    assert structured["match_count"] >= 1
    assert any("Foo.bar" in m["qualified_name"] for m in structured["matches"])


async def test_read_entity_finds_seeded(tmp_path: Path):
    await _seed_brain(tmp_path)
    server = _make_server(tmp_path)
    payload = await server.call_tool("read_entity",
                                       {"urn": "urn:cb:dev:code:demo:method:Foo.bar"})
    assert payload["isError"] is False
    assert payload["structuredContent"]["found"] is True
    assert payload["structuredContent"]["entity"]["qualified_name"] == "Foo.bar"


async def test_find_dependencies_returns_edges(tmp_path: Path):
    await _seed_brain(tmp_path)
    server = _make_server(tmp_path)
    payload = await server.call_tool("find_dependencies",
                                       {"urn": "urn:cb:dev:code:demo:method:Foo.bar"})
    structured = payload["structuredContent"]
    assert structured["count"] == 1
    assert structured["edges"][0]["edge_type"] == "CALLS"


async def test_unknown_tool_returns_error(tmp_path: Path):
    server = _make_server(tmp_path)
    payload = await server.call_tool("nope", {})
    assert payload["isError"] is True


async def test_upsert_disabled_by_default(tmp_path: Path):
    server = _make_server(tmp_path)  # allow_writes=False
    payload = await server.call_tool("upsert_entity", {})
    # Tool not registered when writes are off.
    assert payload["isError"] is True


async def test_upsert_enabled_writes_to_store(tmp_path: Path):
    ws = Workspace.load(tmp_path, workspace_id="ws-test")
    server = BrainMCPServer(ws, allow_writes=True)
    payload = await server.call_tool("upsert_entity", {
        "entity": {
            "id":             "urn:cb:dev:code:demo:method:Z.q",
            "entity_type":    "function_node",
            "repo":           "demo",
            "file":           "src/Z.java",
            "qualified_name": "Z.q",
        },
    })
    assert payload["isError"] is False
    # Round-trip via read_entity to verify it landed.
    out = await server.call_tool("read_entity", {"urn": "urn:cb:dev:code:demo:method:Z.q"})
    assert out["structuredContent"]["found"] is True


def test_http_initialize_returns_capabilities(tmp_path: Path):
    server = _make_server(tmp_path)
    client = TestClient(server.build_asgi_app())
    resp = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["serverInfo"]["name"] == "company-brain"


def test_http_tools_call_query_brain(tmp_path: Path):
    """End-to-end: tools/call over HTTP returns the same payload as the in-process call."""
    import asyncio
    asyncio.get_event_loop().run_until_complete(_seed_brain(tmp_path))

    server = _make_server(tmp_path)
    client = TestClient(server.build_asgi_app())
    resp = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": "x",
        "method": "tools/call",
        "params": {"name": "query_brain", "arguments": {"question": "payer"}},
    })
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["isError"] is False
    # Structured content rolled into the bridge — check the match landed.
    inner = json.loads(result["content"][0]["text"])
    assert inner["match_count"] >= 1


def test_build_server_factory(tmp_path: Path):
    """build_server constructs a server bound to the given repo + workspace."""
    server = build_server(workspace_id="ws-test", brain_root=tmp_path)
    assert server.workspace.id == "ws-test"
    assert server.workspace.repo_path == tmp_path.resolve()
