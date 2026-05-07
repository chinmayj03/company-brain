# MCP Integration Guide — company-brain

Connect Claude Code, Cursor, Windsurf, or any MCP-compatible AI assistant to a
company-brain workspace. After connection the assistant can navigate your codebase
structurally (blast radius, call graph, hubs, flows) and semantically (business
context, review bundles) — without uploading all your source code.

---

## Quick start (Claude Code)

### 1 — Start the MCP server

```bash
# Start the MCP server alongside the backend
make backend   # terminal 1 — Spring Boot on :8080
make mcp       # terminal 2 — MCP server on :9000
```

### 2 — Add to Claude Code config

Add a server entry to `~/.claude/claude_desktop_config.json` (or wherever your
MCP client reads config from):

```json
{
  "mcpServers": {
    "company-brain": {
      "url": "http://localhost:9000/mcp/sse",
      "headers": {
        "Authorization": "Bearer <your-api-token>"
      }
    }
  }
}
```

Replace `<your-api-token>` with a JWT issued by the backend
(`POST /api/auth/token`). For local dev you can use the dev token printed
on backend startup.

### 3 — Verify the connection

In Claude Code, run:

```
/mcp
```

You should see `company-brain` listed with 11 tools available.

---

## Cursor / Windsurf

Cursor and Windsurf support MCP via their settings panels:

1. Open **Settings → Model Context Protocol**.
2. Add a new server:
   - **Name:** `company-brain`
   - **URL:** `http://localhost:9000/mcp/sse`
   - **Auth:** Bearer `<your-api-token>`
3. Restart the editor to pick up the new server.

---

## On-prem agent mode (stdio)

For on-prem deployments where HTTP is not available, run the server over stdio:

```bash
make mcp-stdio
```

Or in your MCP client config:

```json
{
  "mcpServers": {
    "company-brain": {
      "command": "python",
      "args": ["-m", "companybrain.mcp.server", "--stdio"],
      "env": {
        "BACKEND_URL": "http://localhost:8080",
        "BACKEND_API_KEY": "<service-account-key>"
      }
    }
  }
}
```

---

## Environment variables

| Variable            | Default                              | Description                             |
|---------------------|--------------------------------------|-----------------------------------------|
| `BACKEND_URL`       | `http://company-brain-backend:8080`  | Internal URL of the Spring Boot backend |
| `BACKEND_API_KEY`   | *(empty)*                            | Service-to-service auth key             |

Set these in `company-brain-ai/.env` or export them before running `make mcp`.

---

## Available tools

All tools accept `workspace_id` (UUID) and return a `_hints` field that
suggests which tool to call next. Start with `get_minimal_context` — it
gives you workspace stats + top hubs + top flows in under 100 tokens.

### Orientation
| Tool | What it does |
|------|-------------|
| `get_minimal_context` | ~100-token workspace overview. **Start here.** |

### Structural (free — no LLM calls)
| Tool | What it does |
|------|-------------|
| `get_impact_radius` | Bidirectional blast radius. `direction`: FORWARD \| REVERSE \| BOTH |
| `query_graph` | Call/import graph traversal. `relation`: callers_of \| callees_of \| imports_of \| imported_by |
| `find_hubs` | Top-N nodes by degree (structural chokepoints) |
| `find_bridges` | Top-N nodes by betweenness centrality (critical single points of failure) |
| `find_large_functions` | Functions with > `min_lines` lines of code |
| `detect_changes` | Nodes changed since last commit, sorted by risk score |

### Flows
| Tool | What it does |
|------|-------------|
| `list_flows` | Execution flows from entry-points, sorted by criticality |
| `get_flow` | Full node sequence of a specific flow |

### Semantic (retrieval + LLM-backed)
| Tool | What it does |
|------|-------------|
| `get_business_context` | LLM-extracted purpose, data reads/writes, risk flags, business rules |
| `semantic_search_nodes` | Full-text + embedding search across nodes |
| `get_review_context` | Review-ready bundle (structural + semantic) for a set of nodes |

---

## Recommended workflow: code review

```
get_minimal_context(workspace_id)        # orient — see top risk nodes
↓ follow _hints
detect_changes(workspace_id)             # what changed? sorted by risk
↓ pick the riskiest changed node_id
get_impact_radius(workspace_id, node_id) # who is affected?
↓
get_review_context(workspace_id, node_ids=[...affected...])  # full review bundle
↓ for any unclear node
get_business_context(workspace_id, node_id)  # what does it do?
```

You can also just type `/skill:review-pr` in Claude Code once the skills
folder is wired up (Week 4).

---

## Recommended workflow: onboarding

```
get_minimal_context(workspace_id)        # get the lay of the land
↓
list_flows(workspace_id)                 # understand execution paths
↓ pick an interesting flow
get_flow(workspace_id, flow_id)          # trace the full path
↓ for any node you don't understand
get_business_context(workspace_id, node_id)
↓
find_hubs(workspace_id)                  # find the most important nodes
```

---

## Hints system

Every tool response includes a `_hints` array:

```json
{
  "result": { "affectedNodes": [...] },
  "_hints": [
    { "tool": "get_review_context", "suggestion": "Assemble review context for the affected nodes" },
    { "tool": "get_business_context", "suggestion": "Get business context for the origin node" },
    { "tool": "list_flows", "suggestion": "See which execution flows are affected" }
  ]
}
```

AI assistants are instructed (via `server.py` instructions) to read `_hints`
before choosing the next tool. This makes the MCP surface self-guiding —
the assistant doesn't need to know the full tool list upfront.

---

## Authentication

The MCP server is a pass-through: it attaches the caller's Bearer token to
every backend request and lets Spring Security handle authorization. Each
workspace enforces Row-Level Security (ADR-003) — users can only see nodes
belonging to workspaces they have access to.

For on-prem / stdio mode, a service-account API key (`BACKEND_API_KEY`) is
used instead of a per-user token.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `connection refused :9000` | MCP server not started | `make mcp` |
| `401 Unauthorized` | Bad or expired token | Re-generate token: `POST /api/auth/token` |
| `404` on a tool call | Backend not started | `make backend` |
| Empty `affectedNodes` | Structural index not run yet | Trigger indexing: `make ai` then push a commit |
| Empty `flows` | flows.py indexer not run (Week 4) | Coming in Week 4 |
| Tool returns `error: Node not found` | qualified_name not in index | Try `semantic_search_nodes` instead |

---

## Links

- [ADR-006: Adopt CRG Structural + MCP Layer](./ADR-006-adopt-crg-structural-and-mcp-layer.md)
- [PROVIDER-SETUP.md](../PROVIDER-SETUP.md) — LLM provider configuration
- [code-review-graph (upstream reference)](https://github.com/tirth8205/code-review-graph) — MIT License
