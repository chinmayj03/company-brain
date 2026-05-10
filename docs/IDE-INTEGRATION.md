# IDE Integration (ADR-0052 P7)

This doc covers how the VS Code extension and JetBrains plugin scaffold talk
to the company-brain MCP server. The backend is **not** changed — both IDEs
reuse the harness MCP server built in [ADR-0052 P5](adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0052-P5.md).

## Architecture

```
┌────────────────────────┐        JSON-RPC 2.0          ┌────────────────────────────────┐
│  VS Code extension     │────── POST /mcp ────────────▶│ harness MCP server             │
│  (ide/vscode-extension)│                              │ (companybrain.harness.mcp_     │
│                        │◀───── result payload ────────│  server, FastAPI / SSE)        │
└────────────────────────┘                              └────────────────────────────────┘
        ▲                                                              │
        │ commands, hover, sidebar                                     │ reads only
        │                                                              ▼
   developer typing in editor                                  .brain/  (per-repo JSON store)
```

The extension is a **read-only MCP client**. It never writes to the brain;
mutations stay with the CLI (`brain extract`, `brain mcp serve --allow-writes`).

## Tools used

All five read-only tools that the harness MCP server exposes:

| Tool | Used for |
| --- | --- |
| `query_brain` | Right-click → Ask Brain, Spring annotation hover |
| `read_entity` | (reserved for future drill-down panels) |
| `list_entities_by_file` | Sidebar tree, "Extract endpoint at cursor" picker |
| `find_callers` | (reserved for upcoming "who calls this?" view) |
| `find_dependencies` | (reserved for upcoming dependency graph view) |

Schemas live in
[`mcp_server.py`](../company-brain-ai/src/companybrain/harness/mcp_server.py#L241-L305).

## Wire format

```json
POST {mcpUrl}/mcp
{ "jsonrpc": "2.0", "id": 1, "method": "tools/call",
  "params": { "name": "query_brain",
              "arguments": { "question": "what does Foo.bar do?" } } }
```

Response:

```json
{ "jsonrpc": "2.0", "id": 1,
  "result": {
    "content": [{ "type": "text", "text": "{...}" }],
    "isError": false,
    "structuredContent": { "question": "...", "matches": [...], "match_count": 3 }
  } }
```

The TypeScript client (`brain-client.ts`) prefers `structuredContent` and
falls back to parsing `content[0].text`. The headless smoke test
(`test/headless-query.js`) speaks the same wire format and is what the Python
acceptance test exercises.

## Configuration

| Setting | Default | Notes |
| --- | --- | --- |
| `companyBrain.mcpUrl` | `http://localhost:8765` | Where the brain MCP server is listening. |
| `companyBrain.workspaceId` | _(empty)_ | Optional — sent as `X-Workspace-Id`. |
| `companyBrain.hoverEnabled` | `true` | Java/Kotlin Spring-annotation hover provider. |

## Running locally

1. Start the brain MCP server next to the repo you want to query:
   ```bash
   brain mcp serve --http --port 8765
   ```
2. Open VS Code in `ide/vscode-extension`, run `npm install && npm run compile`.
3. Press F5 to launch an Extension Development Host.
4. Open the activity bar's 🧠 Brain icon → the sidebar shows entities for the
   current file. Right-click in the editor → "Brain: Ask about this method".

## Packaging

`npm run package` (a thin wrapper around `vsce package`) produces
`company-brain-1.0.0.vsix`. CI runs the same step on every PR that touches
`ide/vscode-extension/**` and uploads the artifact (see
[.github/workflows/vscode-extension.yml](../.github/workflows/vscode-extension.yml)).

## JetBrains scaffold

`ide/jetbrains-plugin/` is a Gradle scaffold only. It registers a single
`AskBrainAction` on the editor popup menu that surfaces a "coming soon"
notification. No MCP client yet — the real implementation lands after the VS
Code extension proves out the UX. Marketplace publishing is intentionally
deferred.

## Testing

- **Headless smoke test:**
  `MCP_URL=… QUESTION=… node ide/vscode-extension/test/headless-query.js`
  exits 0 on success and prints a JSON object with at least `summary_md`
  plus the raw `query_brain` payload.
- **Acceptance test:**
  [`test_harness_p7_ide.py`](../company-brain-ai/tests/acceptance/test_harness_p7_ide.py)
  asserts (a) the extension packages cleanly via `vsce`, and (b) the headless
  client gets a structured response from a live MCP server seeded with
  fixture entities.
