# brain-as-MCP server (ADR-0052 P5)

The harness ships a self-contained MCP server that exposes the per-repo
`.brain/` JSON store via the standard
[Model Context Protocol](https://modelcontextprotocol.io). External clients
(IDEs, Claude Code, ChatGPT, internal automations) connect over stdio or
HTTP+JSON-RPC and call tools that read brain entities directly.

This is independent of the existing `companybrain.mcp.server`, which fronts
the Java REST + tRPC backend. The harness server is cold-start friendly: no
backend services required, no database connection, just a `.brain/` directory.

## Tools exposed

Read-only by default:

| Tool                       | Args              | Returns |
|----------------------------|-------------------|---------|
| `query_brain`              | `question`        | Token-matched entity hits with summaries. |
| `read_entity`              | `urn`             | Full entity JSON (or `{found:false}`). |
| `list_entities_by_file`    | `file`            | URNs anchored to that file path. |
| `find_callers`             | `urn`             | URNs with a CALLS edge into the target. |
| `find_dependencies`        | `urn`             | Outgoing CALLS / READS / WRITES edges. |

Mutating tools (only when `--allow-writes`):

| Tool             | Args     | Returns |
|------------------|----------|---------|
| `upsert_entity`  | `entity` | `{id, ok}` after the JSON store accepts the write. |

## Running it

### CLI

```bash
# Stdio (Claude Code / IDE clients)
brain mcp serve --repo /path/to/repo --workspace ${BRAIN_WORKSPACE_ID}

# HTTP+JSON-RPC at http://127.0.0.1:8765/mcp
brain mcp serve --http --port 8765 --repo /path/to/repo

# Expose mutating tools (auditable; use only for short-lived author tools)
brain mcp serve --http --allow-writes --repo /path/to/repo
```

### Embedded in the FastAPI service

When `BRAIN_HARNESS_MCP_ENABLED=true`, the same FastAPI process that backs
the pipeline mounts `/mcp/harness` (alongside `/mcp/`). IDE clients can hit
the running service directly without spawning a separate process.

### Programmatic (Python)

```python
from companybrain.harness.mcp_server import build_server

server = build_server(workspace_id="ws-abc", brain_root="/path/to/repo")
asgi = server.build_asgi_app()  # mount under your app
```

## Wire format

JSON-RPC 2.0 envelope; methods follow the MCP spec:

```jsonc
// initialize
{"jsonrpc": "2.0", "id": 1, "method": "initialize"}
// → result.serverInfo, result.protocolVersion, result.capabilities

// list tools
{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
// → result.tools[]

// call a tool
{"jsonrpc": "2.0", "id": 3, "method": "tools/call",
 "params": {"name": "query_brain", "arguments": {"question": "lob payer"}}}
// → result.{content[], isError, structuredContent}
```

## Registering with Claude Code

```json
{
  "mcpServers": {
    "company-brain-harness": {
      "command": "python",
      "args": ["-m", "companybrain.harness.mcp_server",
               "--workspace", "${BRAIN_WORKSPACE_ID}",
               "--repo",      "${BRAIN_REPO_ROOT}"],
      "env": {
        "BRAIN_WORKSPACE_ID": "00000000-0000-0000-0000-000000000001",
        "BRAIN_REPO_ROOT":    "/path/to/your/repo"
      }
    }
  }
}
```

## Permissions

The harness MCP server enforces capability gates at the tool level — write
tools only register when the operator passes `--allow-writes`. The HTTP
route inherits the FastAPI app's auth middleware; nothing in the MCP layer
opens a new authentication channel.
