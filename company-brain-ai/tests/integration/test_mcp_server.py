"""Integration tests for the ADR-0019 MCP stdio server entry point.

Run with:
    pytest tests/integration/test_mcp_server.py -v -m integration
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio


# ── helpers ───────────────────────────────────────────────────────────────────

async def _call_server(
    tmp_path: Path,
    messages: list[dict],
    timeout: float = 10.0,
) -> list[dict]:
    """Spawn the MCP stdio server, send messages, collect responses, then shutdown."""
    env = {
        **os.environ,
        "BRAIN_REPO_ROOT": str(tmp_path),
        "BRAIN_WORKSPACE_ID": "00000000-0000-0000-0000-000000000001",
        # Prevent the server from trying to reach real services during import
        "NEO4J_URI": "bolt://localhost:7687",
        "QDRANT_URL": "http://localhost:6333",
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "companybrain.mcp.server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None

    responses: list[dict] = []
    for msg in messages:
        proc.stdin.write((json.dumps(msg) + "\n").encode())
    await proc.stdin.drain()

    # Read one response per non-shutdown message
    non_shutdown = [m for m in messages if m.get("method") != "shutdown"]
    for _ in non_shutdown:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
        responses.append(json.loads(line))

    # Send shutdown
    shutdown = {"jsonrpc": "2.0", "id": 9999, "method": "shutdown"}
    proc.stdin.write((json.dumps(shutdown) + "\n").encode())
    await proc.stdin.drain()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()

    return responses


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_list(tmp_path: Path):
    """Server responds to tools/list with all 5 expected tools."""
    responses = await _call_server(tmp_path, [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ])
    assert len(responses) == 1
    resp = responses[0]
    assert resp.get("id") == 1
    assert "result" in resp, f"Expected result, got: {resp}"
    tools = resp["result"]["tools"]
    names = {t["name"] for t in tools}
    expected = {"brain_query", "brain_get", "brain_search", "brain_blast_radius", "brain_rebuild"}
    assert expected.issubset(names), f"Missing tools: {expected - names}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_initialize(tmp_path: Path):
    """Server responds to initialize with protocol version and server info."""
    responses = await _call_server(tmp_path, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    ])
    assert len(responses) == 1
    result = responses[0]["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "company-brain"
    assert "tools" in result["capabilities"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_brain_get_not_found(tmp_path: Path):
    """brain_get returns (not found: ...) for an unknown URN."""
    urn = "urn:cb:0001:test-repo:src/foo.py:Bar"
    responses = await _call_server(tmp_path, [
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "brain_get", "arguments": {"entity_id": urn}}},
    ])
    assert len(responses) == 1
    resp = responses[0]
    assert "result" in resp, f"Expected result, got: {resp}"
    content = resp["result"]["content"][0]["text"]
    assert content.startswith("(not found:"), f"Expected not-found message, got: {content!r}"
    assert urn in content


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unknown_tool_returns_error(tmp_path: Path):
    """Calling an unknown tool returns a JSON-RPC error -32601."""
    responses = await _call_server(tmp_path, [
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "does_not_exist", "arguments": {}}},
    ])
    assert len(responses) == 1
    resp = responses[0]
    assert "error" in resp
    assert resp["error"]["code"] == -32601


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unknown_method_returns_error(tmp_path: Path):
    """An unrecognised JSON-RPC method returns -32601."""
    responses = await _call_server(tmp_path, [
        {"jsonrpc": "2.0", "id": 4, "method": "something/unknown"},
    ])
    assert len(responses) == 1
    assert responses[0]["error"]["code"] == -32601


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tool_schemas_have_required_inputschema(tmp_path: Path):
    """Every tool schema has a name, description, and inputSchema."""
    responses = await _call_server(tmp_path, [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ])
    tools = responses[0]["result"]["tools"]
    for t in tools:
        assert "name" in t,        f"Tool missing 'name': {t}"
        assert "description" in t, f"Tool {t.get('name')} missing 'description'"
        assert "inputSchema" in t, f"Tool {t.get('name')} missing 'inputSchema'"
        assert t["inputSchema"].get("type") == "object", \
            f"Tool {t.get('name')} inputSchema.type must be 'object'"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_server_exits_on_eof(tmp_path: Path):
    """Server exits cleanly when stdin is closed (EOF)."""
    env = {
        **os.environ,
        "BRAIN_REPO_ROOT": str(tmp_path),
        "BRAIN_WORKSPACE_ID": "00000000-0000-0000-0000-000000000001",
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "companybrain.mcp.server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    assert proc.stdin is not None
    proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        pytest.fail("Server did not exit on stdin EOF within 5 seconds")
    assert proc.returncode == 0, f"Non-zero exit: {proc.returncode}"
