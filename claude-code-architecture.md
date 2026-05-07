# Claude Code — Architecture & Knowledge Map

> **Purpose:** Structured knowledge capture of how Claude Code works, for use in the company-brain context store.  
> **Scope:** Hooks & Lifecycle · MCP Servers · Slash Commands & Skills · Agent SDK  
> **Last updated:** 2026-05-07  

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Hooks & Lifecycle](#2-hooks--lifecycle)
3. [MCP Server System](#3-mcp-server-system)
4. [Slash Commands & Skills](#4-slash-commands--skills)
5. [Agent SDK](#5-agent-sdk)
6. [Integration Points for company-brain](#6-integration-points-for-company-brain)
7. [Appendix: Config Schemas & API Contracts](#7-appendix-config-schemas--api-contracts)

---

## 1. System Overview

Claude Code is a CLI-based agentic coding tool. At its core it runs a **model-driven agent loop** where Claude receives a user prompt, decides which tools to call, runs those tools, observes results, and repeats until it produces a final response.

```
User Prompt
    │
    ▼
┌─────────────────────────────────────────────┐
│             Claude Code Runtime             │
│                                             │
│  CLAUDE.md / Settings ──► Context Window   │
│                                  │          │
│                          ┌───────▼───────┐  │
│                          │  Claude Model │  │
│                          └───────┬───────┘  │
│                                  │          │
│           ┌──────────────────────▼──────┐   │
│           │        Tool Router          │   │
│           └──┬──────────┬───────────┬───┘   │
│           Built-in    MCP Tools   Agent     │
│           Tools      (external)  (sub-agent)│
│                                             │
│  Hooks fire at: PreToolUse / PostToolUse /  │
│                 Stop / SessionStart / etc.  │
└─────────────────────────────────────────────┘
    │
    ▼
Final Response
```

### Core Concepts

| Concept | What it is |
|---|---|
| **Agent loop** | Repeating cycle: model call → tool use → result → next model call |
| **CLAUDE.md** | Persistent memory injected into every session's context window |
| **Hooks** | Scripts that fire at lifecycle points; can block, modify, or audit tool calls |
| **MCP server** | External process that extends Claude with new tools via JSON-RPC 2.0 |
| **Skill** | A `.claude/skills/<name>/SKILL.md` file that teaches Claude a repeatable workflow |
| **Sub-agent** | A separate Claude instance spawned via the `Agent` tool with an isolated context |

---

## 2. Hooks & Lifecycle

### 2.1 Hook Events Reference

| Hook | When it fires | Can block? | Typical use |
|---|---|---|---|
| `SessionStart` | Once at session begin | No | Initialize logging, load env |
| `UserPromptSubmit` | Once per user turn, before model sees it | Yes (exit 2) | Sanitize input, inject context |
| `PreToolUse` | Before every tool execution | **Yes** (exit 2) | Approve/deny/modify tool calls |
| `PostToolUse` | After tool completes, before Claude processes result | No (tool already ran) | Audit, cost tracking, enrichment |
| `Stop` | When Claude finishes and is about to stop | Yes (exit 2 forces continuation) | Verify task completion |
| `StopFailure` | When Claude stops due to an error | No | Error reporting |
| `Notification` | When Claude generates a notification | No | Route alerts to Slack/desktop |
| `SessionEnd` | Once at session end | No | Cleanup, write summaries |

### 2.2 Hook Lifecycle Order

```
SessionStart hook
    │
    ▼
User submits prompt → UserPromptSubmit hook
    │
    ▼
Claude evaluates
    │
    ├── For each tool call:
    │       PreToolUse hook ──(exit 2: BLOCK)──► skips tool
    │           │
    │           ▼ (exit 0: allow)
    │       Tool executes
    │           │
    │           ▼
    │       PostToolUse hook (audit only)
    │           │
    │           ▼
    │       Claude processes result
    │
    ▼
Claude finishes → Stop hook ──(exit 2: continue)──► loop restarts
    │
    ▼ (exit 0: accept)
SessionEnd hook
    │
    ▼
Session over
```

### 2.3 PreToolUse Event Payload (stdin)

```json
{
  "session_id": "string",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/path/to/project",
  "permission_mode": "ask | auto | plan | bypass_permissions | dont_ask | delegate",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash | Write | Edit | Read | Glob | Grep | WebFetch | WebSearch | Agent | AskUserQuestion | <MCP_TOOL_NAME>",
  "tool_input": {
    "command": "...",
    "...": "all original tool parameters"
  }
}
```

### 2.4 PostToolUse Event Payload (stdin)

```json
{
  "session_id": "string",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/path/to/project",
  "hook_event_name": "PostToolUse",
  "tool_name": "string",
  "tool_input": {
    "...": "original tool parameters"
  },
  "tool_response": {
    "output": "string — stdout from tool",
    "error": "string — stderr or null"
  }
}
```

### 2.5 Hook Stdout Contract (PreToolUse)

A hook can control execution by writing JSON to stdout (exit 0) or using exit codes:

```json
{
  "decision": "allow | block | deny | approve",
  "reason": "Human-readable explanation shown to Claude",
  "updatedInput": {
    "...": "modified tool parameters passed through instead of originals"
  }
}
```

Exit codes:
- `0` → proceed (optionally with JSON override)
- `1` → non-blocking hook error (execution continues)
- `2` → **block** the tool call (reason from stderr shown to Claude)

> **Rule:** Use either exit codes alone OR JSON stdout — not both.

### 2.6 Hook Registration Schema (`.claude/settings.json` or `~/.claude/settings.json`)

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/hook-script.sh"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "python /path/to/audit.py"
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/verify-complete.sh"
          }
        ]
      }
    ]
  }
}
```

**Matcher syntax:** Regex matching tool names. Examples:
- `"Bash"` — only Bash
- `"Write|Edit"` — Write or Edit
- `"Bash(git *)"` — Bash calls running git subcommands (advanced, uses permission rule syntax)
- `"Edit(*.ts)"` — Edit calls on TypeScript files

**Execution:** Hooks run synchronously (blocking), sequentially (not parallel). Hook process does NOT inherit shell env — use absolute paths or explicitly set PATH.

### 2.7 Hook Environment Variables

| Variable | Value |
|---|---|
| `$CLAUDE_PROJECT_DIR` | Absolute path to project root |
| `$CLAUDE_ENV_FILE` | Path to a file for persisting env vars across session (available in SessionStart) |
| `$CLAUDE_CODE_REMOTE` | `"true"` if running remotely, empty string if local |

### 2.8 Config File Locations

| Scope | Path | Notes |
|---|---|---|
| Project (team) | `.claude/settings.json` | Committed to repo, overrides user |
| User (personal) | `~/.claude/settings.json` | Applies to all projects |

---

## 3. MCP Server System

### 3.1 What is MCP

Model Context Protocol (MCP) is a JSON-RPC 2.0 based protocol. Claude Code acts as an **MCP client**; external processes are **MCP servers** that expose tools. This is how Claude Code's toolset is extended without changing the core binary.

### 3.2 MCP Server Discovery

Claude Code reads MCP server configs from:

| Scope | File | Registered via |
|---|---|---|
| User (global) | `~/.claude.json` | `claude mcp add --scope user` |
| Project (team) | `.mcp.json` (project root) | `claude mcp add --scope project` |

> **Note:** `~/.claude/settings.json` is for hooks, NOT MCP. MCP lives in `~/.claude.json` / `.mcp.json`.

### 3.3 MCP Config Schema

#### STDIO (local process)

```json
{
  "mcpServers": {
    "my-local-tool": {
      "command": "python",
      "args": ["/path/to/mcp_server.py"],
      "env": {
        "API_KEY": "${env:API_KEY}",
        "DEBUG": "true"
      },
      "cwd": "/optional/working/directory"
    }
  }
}
```

#### HTTP / Streamable HTTP (remote)

```json
{
  "mcpServers": {
    "remote-api": {
      "type": "streamable-http",
      "url": "https://your-server.com/api/mcp",
      "headers": {
        "Authorization": "Bearer ${env:API_TOKEN}"
      }
    }
  }
}
```

#### SSE (legacy, deprecated)

Same structure as HTTP but with `"type": "sse"`. Prefer `streamable-http`.

### 3.4 MCP Tool Definition Schema

Every tool exposed by an MCP server must conform to this shape:

```json
{
  "name": "unique_tool_identifier",
  "description": "Plain-text explanation of what the tool does and when to use it",
  "inputSchema": {
    "type": "object",
    "properties": {
      "param1": {
        "type": "string",
        "description": "What this param does"
      },
      "param2": {
        "type": "number",
        "minimum": 0,
        "maximum": 100
      }
    },
    "required": ["param1"]
  }
}
```

Optional fields:
- `outputSchema` — describes expected output structure (informational only)
- `title` — human-readable tool name
- `cacheControl` — hints for definition caching

### 3.5 MCP JSON-RPC Protocol

**Tool list request (Claude Code → MCP server):**
```json
{ "jsonrpc": "2.0", "id": 1, "method": "tools/list" }
```

**Tool list response:**
```json
{
  "jsonrpc": "2.0", "id": 1,
  "result": {
    "tools": [
      { "name": "...", "description": "...", "inputSchema": { ... } }
    ]
  }
}
```

**Tool call request:**
```json
{
  "jsonrpc": "2.0", "id": 2,
  "method": "tools/call",
  "params": {
    "name": "tool_name",
    "arguments": { "param1": "value", "param2": 42 }
  }
}
```

**Tool call response (success):**
```json
{
  "jsonrpc": "2.0", "id": 2,
  "result": {
    "content": [{ "type": "text", "text": "Tool output here" }],
    "isError": false
  }
}
```

**Tool call response (error):**
```json
{
  "jsonrpc": "2.0", "id": 2,
  "error": {
    "code": -32602,
    "message": "Invalid parameters",
    "data": { "details": "Missing required argument 'param1'" }
  }
}
```

### 3.6 Tool Loading Strategy (Deferred)

Claude Code uses a two-phase deferred loading model to keep the context window small:

1. **Startup:** Only tool `name` + `description` are loaded into context (lightweight)
2. **On demand:** When Claude needs a tool, the full definition (including `inputSchema`) is fetched via `ToolSearch`

This means: putting good, specific descriptions on MCP tools is critical — the description is what Claude uses to decide whether to load and use the tool.

### 3.7 Minimal Custom MCP Server (Python STDIO)

```python
import json, sys
from typing import Any

TOOLS = {
    "get_company_context": {
        "description": "Retrieve stored company-brain context for a component",
        "inputSchema": {
            "type": "object",
            "properties": {
                "component_id": {"type": "string", "description": "ID of the component"}
            },
            "required": ["component_id"]
        }
    }
}

def handle(req: dict[str, Any]) -> dict[str, Any]:
    method = req.get("method")
    rid = req.get("id")

    if method == "tools/list":
        return {"jsonrpc":"2.0","id":rid,"result":{"tools":[{"name":k,**v} for k,v in TOOLS.items()]}}

    if method == "tools/call":
        name = req["params"]["name"]
        args = req["params"]["arguments"]
        if name == "get_company_context":
            component_id = args["component_id"]
            # --- fetch from your company-brain store here ---
            return {"jsonrpc":"2.0","id":rid,"result":{"content":[{"type":"text","text":f"Context for {component_id}: ..."}],"isError":False}}

    return {"jsonrpc":"2.0","id":rid,"error":{"code":-32601,"message":"Method not found"}}

if __name__ == "__main__":
    for line in sys.stdin:
        line = line.strip()
        if line:
            print(json.dumps(handle(json.loads(line))), flush=True)
```

Register in `.mcp.json`:
```json
{
  "mcpServers": {
    "company-brain": {
      "command": "python",
      "args": ["/path/to/company_brain_mcp.py"]
    }
  }
}
```

### 3.8 Authentication Model

| Transport | Auth mechanism |
|---|---|
| STDIO | File system permissions (no network exposure) |
| HTTP/SSE | `headers` field in config (Bearer tokens, API keys) |
| Env vars | `"${env:VAR_NAME}"` syntax in config — resolves from shell env at startup |

---

## 4. Slash Commands & Skills

### 4.1 CLAUDE.md — Persistent Memory

CLAUDE.md is read at session start and injected into Claude's system context. It is the primary mechanism for making Claude Code "aware" of project conventions, architecture decisions, and company-brain metadata.

#### File locations & precedence (earlier = lower priority)

| Priority | Location | Scope |
|---|---|---|
| 1 (lowest) | `~/.claude/CLAUDE.md` | Global, all projects |
| 2 | `CLAUDE.md` (project root) | This project |
| 3 | `.claude/CLAUDE.md` (project root) | This project (alternate) |
| 4 | `apps/web/CLAUDE.md`, `src/CLAUDE.md` | Subdirectory-specific |
| 5 (highest) | `.claude/CLAUDE.md` (gitignored) | Personal override |

Later files override earlier ones.

#### Recommended CLAUDE.md structure

```markdown
# Project: <name>

## Description
<Tech stack, business domain, key architectural decisions>

## Key Commands
- `npm run dev`  — start dev server (port 3000)
- `npm test`     — run Jest tests

## Conventions
- ESM imports only (no require())
- All async functions must be awaited
- Tests live in `__tests__/` mirroring `src/`

## Architecture Notes
- API contracts live in `contracts/`
- Component metadata: see `company-brain/components.json`
- Screen registry: `company-brain/screens.json`

## Do Not
- No hardcoded secrets
- Never commit `.env.local`
- Don't bypass the company-brain MCP for context
```

Claude reads CLAUDE.md:
- At session start (into context window)
- When the `/memory` command is run (opens for editing)
- Periodically during long sessions to refresh knowledge

### 4.2 Skills — Repeatable Workflows

Skills are SKILL.md files that teach Claude a specific, repeatable task. They are the company-brain equivalent of saved prompts with structure.

#### Folder structure

```
.claude/
└── skills/
    ├── extract-component-context/
    │   ├── SKILL.md         ← required
    │   └── schema.json      ← optional helper file
    ├── map-api-contracts/
    │   └── SKILL.md
    └── generate-screen-doc/
        └── SKILL.md
```

**Scopes:**
- `.claude/skills/` — project-level (committed to repo)
- `~/.claude/skills/` — user-level (available across all projects)

#### SKILL.md Format

```markdown
---
name: extract-component-context
description: |
  Extract and store structured metadata about a React component into
  company-brain. Use when a user says "map this component", "add component
  to brain", or when analyzing a new component file.
disable-model-invocation: false
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
version: "1.0"
---

You are extracting component context for company-brain.

Steps:
1. Read the component file provided
2. Identify: props interface, state shape, child components used, API calls made
3. Check `company-brain/components.json` for existing entry
4. Write or update the entry with the structured metadata
5. Confirm to the user what was saved

Output format for components.json entry:
{
  "id": "<ComponentName>",
  "file": "<relative path>",
  "props": [...],
  "state": [...],
  "childComponents": [...],
  "apiContracts": [...],
  "screens": [...],
  "businessContext": "<plain-text summary>",
  "assumptions": [...]
}
```

#### SKILL.md Frontmatter Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | Yes | Unique skill identifier; used as slash command `/name` |
| `description` | string | Yes | Tells Claude when to auto-invoke this skill |
| `disable-model-invocation` | boolean | No (default: false) | If true, skill only runs via explicit `/name` — never auto |
| `allowed-tools` | array | No | Restricts tools available while skill runs |
| `version` | string | No | Version tag for tracking |

#### Skill Discovery Mechanism

1. At startup, Claude Code scans skill directories and loads only `name` + `description` from each SKILL.md frontmatter into the system prompt (deferred loading)
2. Full SKILL.md content is read on demand when the skill is invoked
3. Invocation: explicit via `/skill-name`, or automatic when Claude detects the description matches the user's intent

### 4.3 Built-in Slash Commands

| Command | What it does |
|---|---|
| `/clear` | Fresh session (clears conversation history) |
| `/compact` | Summarize & compress conversation to save context |
| `/context` | Show context usage as colored token grid |
| `/memory` | Open CLAUDE.md in editor |
| `/exit` | Exit Claude Code |
| `/status` | Show version and connectivity info |
| `/diff` | View file diffs |
| `/help` | List all commands |
| `/effort` | Set effort/token level for this session |
| `/voice` | Enable voice input mode |
| `/color` | Toggle color output |
| `/<skill-name>` | Invoke a custom skill |

> There are 60+ built-in commands. Type `/` to see the full live list.

---

## 5. Agent SDK

### 5.1 The Agent Loop

The SDK runs a deterministic loop until Claude returns a final answer:

```
query() called
    │
    ▼
[1] Send messages + tools to Claude model
    │
    ▼
[2] Claude response received
    ├── stop_reason = "tool_use"
    │       │
    │       ▼
    │   [3] Execute tools
    │       ├── Read-only tools (Read, Glob, Grep, read-only MCPs) → concurrent
    │       └── Stateful tools (Write, Edit, Bash, Agent) → sequential
    │       │
    │       ▼
    │   [4] Append tool results as ToolResultMessage → goto [1]
    │
    └── stop_reason = "end_turn"
            │
            ▼
        [5] Return final AssistantMessage (loop terminates)
```

**Message types in loop:**

| Type | Subtype | When |
|---|---|---|
| `SystemMessage` | `"init"` | Loop initialization |
| `AssistantMessage` | — | Claude's response, may have tool-use blocks |
| `ToolResultMessage` | — | Tool execution results |
| `ErrorMessage` | `"error_max_turns"` | Max turns exceeded |
| `ErrorMessage` | `"error_during_execution"` | Tool execution failed |

### 5.2 Sub-Agents

Sub-agents are separate Claude instances, each with their own isolated context window.

**Key architecture rules:**
- Sub-agent does NOT see parent conversation history
- Data flows in only via the `prompt` parameter of the `Agent` tool call
- Sub-agent's intermediate tool calls are invisible to parent
- Only the sub-agent's final message returns to the parent

**Agent tool call shape:**
```json
{
  "type": "tool_use",
  "id": "call_xyz",
  "name": "Agent",
  "input": {
    "prompt": "Your task: analyze the code at /path and report top 3 issues"
  }
}
```

**Python SDK example:**
```python
from claude_agent_sdk import query, Session

result = await query(
    model="claude-opus-4-20250805",
    tools=[Agent],
    messages=[{
        "role": "user",
        "content": """
        Spawn a sub-agent to:
        Task: Extract component metadata from /src/components/UserCard.tsx
        Output: Structured JSON conforming to company-brain component schema
        """
    }]
)
```

### 5.3 Custom Tool Definition

#### Python
```python
from claude_agent_sdk import tool
from typing import Any

@tool(
    name="get_company_brain_context",
    description="Retrieve stored metadata about a screen, component, or API contract from company-brain",
    input_schema={
        "entity_type": str,   # "screen" | "component" | "api"
        "entity_id": str
    },
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        openWorldHint=False
    )
)
async def get_company_brain_context(args: dict[str, Any]) -> dict[str, Any]:
    entity_type = args["entity_type"]
    entity_id = args["entity_id"]
    # --- lookup in company-brain store ---
    return { "entity_type": entity_type, "id": entity_id, "metadata": { ... } }
```

#### TypeScript
```typescript
import { tool } from "@anthropic-ai/claude-agent-sdk";
import { z } from "zod";

const getCompanyBrainContext = tool(
  "get_company_brain_context",
  "Retrieve stored metadata about a screen, component, or API from company-brain",
  z.object({
    entity_type: z.enum(["screen", "component", "api"]).describe("Type of entity"),
    entity_id: z.string().describe("Unique entity identifier")
  }),
  async ({ entity_type, entity_id }) => {
    // --- lookup in company-brain store ---
    return { entity_type, entity_id, metadata: { ... } };
  },
  { readOnlyHint: true, destructiveHint: false, openWorldHint: false }
);
```

**Tool annotation fields:**

| Annotation | Type | Meaning |
|---|---|---|
| `readOnlyHint` | boolean | Tool doesn't modify state → safe to run concurrently |
| `destructiveHint` | boolean | Tool performs irreversible action → prompts user approval |
| `openWorldHint` | boolean | Tool makes external/network calls |

### 5.4 Permission Modes

| Mode | Behavior |
|---|---|
| `default` | SDK asks user before each tool execution |
| `accept_edits` | Auto-approve file edits (Write, Edit tools) |
| `plan` | No tool execution — Claude analyzes only |
| `bypass_permissions` | Auto-approve all tools (use with extreme caution) |
| `dont_ask` | Auto-deny anything not in `allowed_tools` or hook approvals |
| `delegate` | Custom callback function controls approvals per tool |

**Python:**
```python
result = await query(
    model="claude-opus-4-20250805",
    tools=[...],
    messages=[...],
    permission_mode="accept_edits"
)
```

**Layered permission system (precedence order):**
1. `bypass_permissions` mode — overrides everything
2. `allowed_tools` parameter — explicit allowlist
3. `canUseTool` callback — custom per-tool approval function
4. `PreToolUse` hooks — script-based approval logic
5. `settings.json` rules — file-based permission rules
6. Default interactive prompt

### 5.5 Stop Reasons

| Stop Reason | Meaning |
|---|---|
| `"end_turn"` | Claude finished normally, has final answer |
| `"tool_use"` | Claude called tool(s) — loop continues |
| `"max_tokens"` | Hit token limit — response incomplete |
| `"refusal"` | Claude declined (safety/policy) |
| `"stop_sequence"` | Hit configured stop sequence |

### 5.6 Model Selection

```python
result = await query(
    model="claude-sonnet-4-20250514",  # or opus/haiku variant
    tools=[...],
    messages=[...]
)
```

Available model families (as of 2026-05):
- `claude-opus-4-*` — largest, most capable (200K context)
- `claude-sonnet-4-*` — balanced speed/capability (200K context)
- `claude-haiku-4-*` — fastest, cheapest (200K context)

### 5.7 Context Management & Compaction

**Auto-compaction trigger:** When input tokens reach ~85-90% of the context limit.

**Process:**
1. SDK generates a structured summary of conversation history
2. Summary replaces full history (lossy — peripheral detail is dropped)
3. Key decisions and outputs are preserved

```python
result = await query(
    model="claude-opus-4-20250805",
    tools=[...],
    messages=[...],
    compaction_control="auto"  # or "manual"
)
```

**Note:** Previous thinking blocks are automatically stripped from context window calculation — they don't count against token limits.

### 5.8 Worktree Isolation

```python
result = await query(
    model="claude-opus-4-20250805",
    tools=[...],
    messages=[...],
    worktree=True  # Creates temporary git worktree
)
```

Behavior:
- Creates temporary worktree from current branch
- All file changes are isolated to the worktree
- Parent repo is unaffected
- Worktree is automatically cleaned up after task completes (if no changes were made)

### 5.9 Error Handling

```python
for message in result.messages:
    if message.type == "error":
        if message.subtype == "error_max_turns":
            print(f"Hit turn limit. Last stop reason: {message.stop_reason}")
        elif message.subtype == "error_during_execution":
            print(f"Tool execution failed: {message.error}")
    elif message.type == "assistant":
        if message.stop_reason == "refusal":
            print("Claude refused the request")
```

**Tool timeout:** Tools that exceed the timeout return `Error("Tool execution timed out")`. A timeout on one tool doesn't abort others in the same turn.

---

## 6. Integration Points for company-brain

This section maps the four Claude Code pillars to concrete integration strategies for the company-brain system.

### 6.1 CLAUDE.md as company-brain Entry Point

Every project using company-brain should have a CLAUDE.md that references the brain:

```markdown
## company-brain
- Component registry: `company-brain/components.json`
- Screen registry: `company-brain/screens.json`
- API contracts: `company-brain/api-contracts/`
- Data assumptions: `company-brain/assumptions.json`
- Business context: `company-brain/context.md`

Use the `company-brain` MCP server to read/write brain entries rather than
editing JSON files directly.
```

### 6.2 MCP Server as company-brain Read/Write API

Expose the company-brain data store as an MCP server so Claude (and all sub-agents) can query and update it mid-session.

**Suggested MCP tools:**

| Tool name | Description | Input |
|---|---|---|
| `brain_get_component` | Get metadata for a component | `{ component_id: string }` |
| `brain_set_component` | Write/update component metadata | `{ component_id, metadata }` |
| `brain_get_screen` | Get metadata for a screen | `{ screen_id: string }` |
| `brain_set_screen` | Write/update screen metadata | `{ screen_id, metadata }` |
| `brain_get_api_contract` | Get an API contract | `{ endpoint: string }` |
| `brain_set_api_contract` | Write/update an API contract | `{ endpoint, contract }` |
| `brain_search` | Full-text search across brain | `{ query: string }` |
| `brain_get_assumptions` | List data assumptions for a domain | `{ domain: string }` |

### 6.3 Hooks as company-brain Automation Layer

Use hooks to automatically update company-brain when code changes:

| Hook | Trigger | Action |
|---|---|---|
| `PostToolUse` on `Write|Edit` | File modified | Re-extract metadata for affected component |
| `PostToolUse` on `Bash` | Git commit detected | Trigger brain snapshot |
| `Stop` | Session ends | Write session summary to `company-brain/sessions/` |
| `SessionStart` | Session begins | Load relevant brain context into a temp file for Claude |

**Example hook: auto-update brain on file edit**

```bash
#!/bin/bash
# .claude/hooks/post-edit-brain-update.sh
INPUT=$(cat)
TOOL=$(echo $INPUT | jq -r '.tool_name')
FILE=$(echo $INPUT | jq -r '.tool_input.file_path')

if [[ "$TOOL" == "Write" || "$TOOL" == "Edit" ]]; then
  if [[ "$FILE" == *"/components/"* ]]; then
    python /path/to/brain-extractor.py --file "$FILE"
  fi
fi
exit 0
```

### 6.4 Skills as company-brain Workflows

Define skills for each brain operation:

| Skill | Trigger phrase | What it does |
|---|---|---|
| `map-component` | "map this component" | Extracts + stores component metadata |
| `map-screen` | "map this screen" | Extracts + stores screen metadata |
| `extract-api-contracts` | "extract API contracts" | Scans files for fetch/axios calls → stores contracts |
| `capture-assumptions` | "capture assumptions" | Identifies data assumptions from code → stores them |
| `brain-search` | "what do we know about X" | Queries brain and summarizes findings |
| `generate-context-doc` | "generate context doc" | Produces human-readable doc from brain data |

### 6.5 Sub-Agents as Parallel Brain Builders

For large codebases, spawn parallel sub-agents to map different parts of the brain simultaneously:

```python
# Orchestrator pattern
tasks = [
    "Map all components in /src/components/ to company-brain",
    "Extract all API contracts from /src/api/ to company-brain",
    "Identify all data assumptions in /src/models/ to company-brain",
    "Map all screens in /src/screens/ to company-brain"
]

# Spawn one sub-agent per task in parallel (read-only tools → safe to run concurrently)
results = await asyncio.gather(*[
    query(model="claude-sonnet-4-20250514", tools=[Agent, brain_set_component, Read, Glob, Grep],
          messages=[{"role":"user","content":task}])
    for task in tasks
])
```

---

## 7. Appendix: Config Schemas & API Contracts

### A1. settings.json Full Schema

```json
{
  "hooks": {
    "<HookEventName>": [
      {
        "matcher": "<regex matching tool names>",
        "hooks": [
          {
            "type": "command",
            "command": "<shell command or script path>",
            "if": "<optional permission rule syntax>"
          }
        ]
      }
    ]
  },
  "permissions": {
    "allow": ["<tool patterns>"],
    "deny": ["<tool patterns>"]
  }
}
```

### A2. .mcp.json / ~/.claude.json Full Schema

```json
{
  "mcpServers": {
    "<server-name>": {
      "command": "<executable>",
      "args": ["<arg1>", "<arg2>"],
      "env": {
        "<KEY>": "<value or ${env:SHELL_VAR}>"
      },
      "cwd": "<optional working directory>",
      "type": "stdio | streamable-http | sse",
      "url": "<URL for HTTP/SSE transports>",
      "headers": {
        "<Header-Name>": "<value>"
      }
    }
  }
}
```

### A3. SKILL.md Frontmatter Schema

```yaml
---
name: string                      # required, unique identifier
description: string               # required, used for auto-discovery
disable-model-invocation: boolean # optional, default false
allowed-tools:                    # optional
  - Read
  - Grep
  - Glob
  - Bash
  - Write
  - Edit
  - WebFetch
  - WebSearch
  - Agent
  - AskUserQuestion
version: string                   # optional
---
```

### A4. company-brain Component Schema (Proposed)

```json
{
  "id": "ComponentName",
  "file": "src/components/ComponentName.tsx",
  "type": "component",
  "props": [
    { "name": "userId", "type": "string", "required": true, "description": "..." }
  ],
  "state": [
    { "name": "isLoading", "type": "boolean", "initial": false }
  ],
  "childComponents": ["Button", "Avatar"],
  "apiContracts": [
    { "endpoint": "/api/users/{id}", "method": "GET", "ref": "api-contracts/users.json" }
  ],
  "screens": ["UserProfile", "Settings"],
  "businessContext": "Displays user card with avatar and role. Used in the dashboard header.",
  "assumptions": [
    "userId is always a valid UUID",
    "User always has at least one role"
  ],
  "dataModels": ["User", "Role"],
  "lastUpdated": "2026-05-07T00:00:00Z",
  "extractedBy": "claude-code-map-component-skill"
}
```

### A5. MCP JSON-RPC Error Codes

| Code | Meaning |
|---|---|
| `-32700` | Parse error |
| `-32600` | Invalid request |
| `-32601` | Method not found |
| `-32602` | Invalid params |
| `-32603` | Internal error |
| `-32000` to `-32099` | Server-defined errors |

### A6. SDK Installation

```bash
# Python (requires Python 3.10+)
pip install claude-agent-sdk

# TypeScript / Node.js
npm install @anthropic-ai/claude-agent-sdk
```

---

*Sources: docs.claude.com, code.claude.com, platform.claude.com, modelcontextprotocol.io — verified 2026-05-07*
