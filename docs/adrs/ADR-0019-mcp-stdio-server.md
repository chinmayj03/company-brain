# ADR-0019: MCP stdio server entry point for Claude Code

**Status:** Proposed
**Date:** 2026-05-07
**Effort:** 3 days
**Depends on:** ADR-0013 (URN), ADR-0015 (HybridSearcher), ADR-0017 (assumption / business_context as nodes), ADR-0018 (SmartZoneAssembler)
**Unblocks:** Claude Code integration; the harness's CLAUDE.md auto-context flow.

---

## Context

`companybrain/mcp/{client,tools/{context,flows}}.py` exists as a skeleton. There is no MCP **stdio server** entry point that Claude Code can attach to via `claude mcp add company-brain ...`. The `apps/api/` Bun service exposes tRPC over HTTP — useful, but Claude Code natively prefers JSON-RPC stdio for local workflows.

The harness §8.1 prescribes a tool inventory: `brain_query`, `brain_get`, `brain_search`, `brain_blast_radius`, `brain_set_*`, `brain_rebuild`. Stage 1 ships the read-side tools; write-side tools follow once the read path is proven.

## Decision

Implement a Python MCP stdio server at `companybrain/mcp/server.py`. JSON-RPC 2.0 over stdin/stdout per the MCP spec. Tools delegate to:
- `SmartZoneAssembler.assemble()` for `brain_query`
- `JsonFileBrainStore.read()` for `brain_get`
- `HybridSearcher.search()` for `brain_search`
- Neo4j Cypher for `brain_blast_radius`
- `BrainStore` write paths for `brain_set_*` (post-Stage-1 writes optional in v0.1)
- `cli_helpers.brain_rebuild.rebuild_from_json` for `brain_rebuild`

Register the server via `~/.claude.json` so any Claude Code session in the pilot repo gets the brain MCP automatically.

## Implementation

### Files to create

#### `companybrain/mcp/server.py`

```python
"""
MCP stdio server for the company-brain.

Spec: JSON-RPC 2.0 over stdin/stdout. Methods we implement:
  - tools/list       — return tool schemas
  - tools/call       — invoke a tool by name with arguments
  - shutdown         — clean exit

Run as:
    python -m companybrain.mcp.server

Register in Claude Code's user-scope config (~/.claude.json):
    {
      "mcpServers": {
        "company-brain": {
          "command": "python",
          "args": ["-m", "companybrain.mcp.server"],
          "env": {
            "BRAIN_REPO_ROOT": "/path/to/pilot",
            "BRAIN_WORKSPACE_ID": "00000000-0000-0000-0000-000000000001",
            "NEO4J_URI": "bolt://localhost:7687",
            "QDRANT_URL": "http://localhost:6333"
          }
        }
      }
    }
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

import structlog

from companybrain.mcp.tools import TOOL_REGISTRY

log = structlog.get_logger(__name__)

JSONRPC_VERSION = "2.0"


async def main() -> None:
    """Read JSON-RPC requests from stdin, write responses to stdout, line-delimited."""
    log.info("mcp_server.start")
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            break  # EOF — Claude Code closed the pipe
        line = line.decode().strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _emit_error(rid=None, code=-32700, message="Parse error")
            continue
        await _handle(request)
    log.info("mcp_server.stop")


async def _handle(request: dict[str, Any]) -> None:
    rid    = request.get("id")
    method = request.get("method")
    params = request.get("params", {}) or {}
    try:
        if method == "tools/list":
            _emit_result(rid, {"tools": [t.schema for t in TOOL_REGISTRY.values()]})
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments", {}) or {}
            tool = TOOL_REGISTRY.get(name)
            if tool is None:
                _emit_error(rid, -32601, f"Unknown tool: {name}")
                return
            result = await tool.handler(args)
            _emit_result(rid, {"content": [{"type": "text", "text": result}], "isError": False})
        elif method == "initialize":
            _emit_result(rid, {"protocolVersion": "2024-11-05",
                                "capabilities": {"tools": {}},
                                "serverInfo": {"name": "company-brain", "version": "0.1.0"}})
        elif method == "shutdown":
            _emit_result(rid, None)
            sys.exit(0)
        else:
            _emit_error(rid, -32601, f"Method not found: {method}")
    except Exception as exc:
        log.error("mcp_server.handler_error", method=method, error=str(exc),
                  traceback=traceback.format_exc())
        _emit_error(rid, -32603, f"Internal error: {exc}")


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _emit_result(rid, result):
    _emit({"jsonrpc": JSONRPC_VERSION, "id": rid, "result": result})


def _emit_error(rid, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    _emit({"jsonrpc": JSONRPC_VERSION, "id": rid, "error": err})


if __name__ == "__main__":
    asyncio.run(main())
```

#### `companybrain/mcp/tools/__init__.py`

```python
"""Tool registry — every tool registers itself here at import time."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Awaitable, Callable, Any


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[[dict], Awaitable[str]]


TOOL_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    TOOL_REGISTRY[tool.name] = tool


# Import each tool module so it registers
from companybrain.mcp.tools import query as _q          # noqa: F401
from companybrain.mcp.tools import get as _g            # noqa: F401
from companybrain.mcp.tools import search as _s         # noqa: F401
from companybrain.mcp.tools import blast_radius as _br  # noqa: F401
from companybrain.mcp.tools import rebuild as _rb       # noqa: F401
```

#### `companybrain/mcp/tools/query.py`

```python
"""brain_query — main entry point for LLM consumers."""
from __future__ import annotations
import json
import os
from pathlib import Path

from neo4j import AsyncGraphDatabase

from companybrain.assembly.smart_zone import SmartZoneAssembler
from companybrain.assembly.types import TokenBudget
from companybrain.mcp.tools import Tool, register
from companybrain.store import JsonFileBrainStore


_SCHEMA = {
    "name": "brain_query",
    "description": (
        "Assemble company-brain context for a task. "
        "Returns T0/T1/T2 tiered context within a token budget, "
        "plus blast radius and business context for the relevant entities. "
        "Use this BEFORE making any significant code change to learn what "
        "components, APIs, data models, and assumptions are involved."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "task":         {"type": "string", "description": "Natural-language task description"},
            "entities":     {"type": "array", "items": {"type": "string"},
                              "description": "Optional URN seeds; default is hybrid retrieval"},
            "token_budget": {"type": "integer", "default": 6000},
            "repo":         {"type": "string", "description": "Repo path; defaults to BRAIN_REPO_ROOT env"},
        },
        "required": ["task"],
    },
}


async def _handle(args: dict) -> str:
    repo = Path(args.get("repo") or os.getenv("BRAIN_REPO_ROOT", "."))
    workspace_id = os.getenv("BRAIN_WORKSPACE_ID", "00000000-0000-0000-0000-000000000001")

    store = JsonFileBrainStore(repo / ".brain")
    driver = AsyncGraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"),
              os.getenv("NEO4J_PASSWORD", "password")),
    )
    try:
        assembler = SmartZoneAssembler(
            brain_root=repo / ".brain", workspace_id=workspace_id,
            store=store, neo4j_driver=driver,
        )
        budget = TokenBudget(total=int(args.get("token_budget", 6000)))
        payload = await assembler.assemble(
            task=args["task"],
            entities=args.get("entities"),
            budget=budget,
        )
        return payload.rendered
    finally:
        await driver.close()


register(Tool(
    name=_SCHEMA["name"],
    description=_SCHEMA["description"],
    schema=_SCHEMA,
    handler=_handle,
))
```

#### `companybrain/mcp/tools/get.py`

```python
"""brain_get — return one entity's full JSON."""
import json
import os
from pathlib import Path

from companybrain.mcp.tools import Tool, register
from companybrain.store import JsonFileBrainStore


_SCHEMA = {
    "name": "brain_get",
    "description": "Get a single brain entity by its URN. Returns the full JSON.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "entity_id": {"type": "string", "description": "URN of the entity"},
            "repo":      {"type": "string"},
        },
        "required": ["entity_id"],
    },
}


async def _handle(args: dict) -> str:
    repo = Path(args.get("repo") or os.getenv("BRAIN_REPO_ROOT", "."))
    store = JsonFileBrainStore(repo / ".brain")
    entity = await store.read(args["entity_id"])
    if entity is None:
        return f"(not found: {args['entity_id']})"
    return json.dumps(entity.to_dict(), indent=2)


register(Tool(
    name=_SCHEMA["name"],
    description=_SCHEMA["description"],
    schema=_SCHEMA,
    handler=_handle,
))
```

#### `companybrain/mcp/tools/search.py`

```python
"""brain_search — hybrid keyword + semantic search."""
import os
from pathlib import Path

from companybrain.mcp.tools import Tool, register
from companybrain.retrieval.hybrid_search import HybridSearcher
from companybrain.store.identity import workspace_slug_for


_SCHEMA = {
    "name": "brain_search",
    "description": "Hybrid (BM25 + semantic) search across all brain entities.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query":        {"type": "string"},
            "top_k":        {"type": "integer", "default": 10},
            "entity_types": {"type": "array", "items": {"type": "string"}},
            "repo":         {"type": "string"},
        },
        "required": ["query"],
    },
}


async def _handle(args: dict) -> str:
    repo = Path(args.get("repo") or os.getenv("BRAIN_REPO_ROOT", "."))
    workspace_id = os.getenv("BRAIN_WORKSPACE_ID", "")
    searcher = HybridSearcher(
        brain_root=repo,
        workspace_slug=workspace_slug_for(workspace_id),
    )
    hits = searcher.search(
        args["query"],
        top_k=args.get("top_k", 10),
        entity_types=args.get("entity_types"),
    )
    if not hits:
        return "(no results)"
    lines = []
    for h in hits:
        lines.append(f"{h.score:6.4f}  {h.urn}")
        if h.payload.get("t1_summary"):
            lines.append(f"        → {h.payload['t1_summary']}")
    return "\n".join(lines)


register(Tool(
    name=_SCHEMA["name"],
    description=_SCHEMA["description"],
    schema=_SCHEMA,
    handler=_handle,
))
```

#### `companybrain/mcp/tools/blast_radius.py`

```python
"""brain_blast_radius — BFS over Neo4j returning the impact set."""
import json
import os

from neo4j import AsyncGraphDatabase

from companybrain.mcp.tools import Tool, register


_SCHEMA = {
    "name": "brain_blast_radius",
    "description": (
        "Compute the blast radius of an entity — what would be affected if it "
        "changed (upstream) or what it depends on (downstream). Returns up to 50 "
        "neighbour URNs grouped by direction."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "entity_id": {"type": "string"},
            "hops":      {"type": "integer", "default": 2},
            "direction": {"type": "string", "enum": ["upstream", "downstream", "both"], "default": "both"},
        },
        "required": ["entity_id"],
    },
}


async def _handle(args: dict) -> str:
    urn = args["entity_id"]
    hops = int(args.get("hops", 2))
    direction = args.get("direction", "both")
    clause = {
        "upstream":   f"<-[*1..{hops}]-",
        "downstream": f"-[*1..{hops}]->",
        "both":       f"-[*1..{hops}]-",
    }[direction]
    driver = AsyncGraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"),
              os.getenv("NEO4J_PASSWORD", "password")),
    )
    try:
        async with driver.session() as session:
            result = await session.run(
                f"MATCH (n {{id: $urn}}){clause}(m) "
                f"RETURN DISTINCT m.id AS id, labels(m) AS labels LIMIT 50",
                urn=urn,
            )
            rows = await result.data()
    finally:
        await driver.close()

    if not rows:
        return f"(no neighbours for {urn})"
    lines = [f"Blast radius for {urn} ({direction}, {hops} hops):"]
    for r in rows:
        lbl = r["labels"][0] if r["labels"] else "?"
        lines.append(f"  {lbl:18s}  {r['id']}")
    return "\n".join(lines)


register(Tool(
    name=_SCHEMA["name"],
    description=_SCHEMA["description"],
    schema=_SCHEMA,
    handler=_handle,
))
```

#### `companybrain/mcp/tools/rebuild.py`

```python
"""brain_rebuild — force-rebuild Postgres + Neo4j + Qdrant from .brain/ JSONs."""
import os
from pathlib import Path

from companybrain.cli_helpers.brain_rebuild import rebuild_from_json
from companybrain.mcp.tools import Tool, register


_SCHEMA = {
    "name": "brain_rebuild",
    "description": "Replay every .brain/ JSON into Postgres + Neo4j + Qdrant. "
                    "Use after wiping any of the projection stores.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "repo": {"type": "string"},
        },
    },
}


async def _handle(args: dict) -> str:
    repo = Path(args.get("repo") or os.getenv("BRAIN_REPO_ROOT", "."))
    workspace_id = os.getenv("BRAIN_WORKSPACE_ID", "")
    await rebuild_from_json(repo, workspace_id)
    return f"rebuilt brain from {repo}/.brain/"


register(Tool(
    name=_SCHEMA["name"],
    description=_SCHEMA["description"],
    schema=_SCHEMA,
    handler=_handle,
))
```

### CLAUDE.md template for the pilot repo

After installing the MCP server, drop this `CLAUDE.md` at the pilot repo root:

```markdown
# Project: <pilot repo name>

## company-brain
Before making any non-trivial code change, call:
  brain_query(task="<description>", entities=["<EntityName>"])

This returns a tiered context payload (T0 summaries + T1 detail + business
context + blast radius) within a 6,000-token budget — much cheaper than
reading source files yourself, and includes context that isn't in the code.

For a single-entity lookup: brain_get(entity_id="<urn>").
For free-form search:      brain_search(query="<keywords>").
For impact analysis:       brain_blast_radius(entity_id="<urn>", hops=2).
```

## Test plan

`tests/integration/test_mcp_server.py`:

```python
import asyncio, json, os, subprocess
from pathlib import Path

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_list(tmp_path: Path):
    """Run the MCP server as a subprocess; send tools/list; expect 5 tools."""
    env = {**os.environ,
           "BRAIN_REPO_ROOT": str(tmp_path),
           "BRAIN_WORKSPACE_ID": "00000000-0000-0000-0000-000000000001"}
    proc = await asyncio.create_subprocess_exec(
        "python", "-m", "companybrain.mcp.server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    proc.stdin.write((json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/list"
    }) + "\n").encode())
    await proc.stdin.drain()
    line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
    response = json.loads(line)
    assert response["id"] == 1
    tools = response["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"brain_query", "brain_get", "brain_search",
            "brain_blast_radius", "brain_rebuild"}.issubset(names)
    proc.stdin.write((json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "shutdown"
    }) + "\n").encode())
    await proc.stdin.drain()
    await proc.wait()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_brain_get_returns_not_found(tmp_path):
    # ... similar pattern, call brain_get with an invalid URN
    pass
```

## Acceptance criteria

- [ ] `companybrain/mcp/server.py` and `companybrain/mcp/tools/{query,get,search,blast_radius,rebuild}.py` exist.
- [ ] `python -m companybrain.mcp.server` starts and responds to `tools/list` with 5 tools.
- [ ] Each tool's `inputSchema` is valid JSON Schema and matches the harness §8.1 spec.
- [ ] `tools/call` for `brain_query` returns the rendered SmartZonePayload.
- [ ] `tools/call` for `brain_get` returns one entity's JSON or `(not found: ...)`.
- [ ] `tools/call` for `brain_search` returns ranked URN list.
- [ ] `tools/call` for `brain_blast_radius` returns Cypher BFS results.
- [ ] `tools/call` for `brain_rebuild` repopulates the projection stores from `.brain/`.
- [ ] `~/.claude.json` `mcpServers.company-brain` registration brings up the server when Claude Code starts in the pilot repo.
- [ ] Claude Code's `/mcp` command shows `company-brain` connected with all 5 tools.
- [ ] Integration test `test_tools_list` passes.
- [ ] Server exits cleanly on `shutdown` and on stdin EOF.

## Verification commands

```bash
# 1. Manual smoke test
python -m companybrain.mcp.server <<EOF
{"jsonrpc":"2.0","id":1,"method":"tools/list"}
EOF
# Expect a JSON line with "tools": [...] containing 5 entries.

# 2. Full call
python -m companybrain.mcp.server <<EOF
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"brain_search","arguments":{"query":"payment","top_k":3,"repo":"./pilot"}}}
EOF

# 3. Register and test from Claude Code
claude mcp add company-brain --scope user \
  --command "python" --args "-m,companybrain.mcp.server" \
  --env "BRAIN_REPO_ROOT=$(pwd)/pilot,BRAIN_WORKSPACE_ID=00000000-0000-0000-0000-000000000001"
# Then in Claude Code: /mcp  → expect "company-brain — connected"
```

## Rollback

```bash
# Remove the server registration:
claude mcp remove company-brain --scope user
# Revert code changes:
git revert <commit-sha>
```

## Out of scope

- **Write-side tools** (`brain_set_component`, `brain_set_screen`, etc.). Stage 1 ships read-only. Write-side tools follow once edit-via-LLM-and-write-to-brain proves valuable.
- **HTTP/SSE transports.** This ADR is stdio only. v2 adds streamable-HTTP for remote MCP clients.
- **Authentication.** stdio runs as the user's local process; no auth needed. Network transports require shared secrets (covered in v2).
- **Tool-level permission gates.** Today every tool is invocable. v2 ADR can add a permission policy file (`.brain/mcp-policy.yaml`).
- **Streaming tool output.** Today the response is buffered. Streaming for long blast-radius queries is a v2 perf concern.
