# Company Brain — VS Code extension

Inline codebase context backed by the company-brain MCP server (ADR-0052 P5).

## What it does

- **Right-click → Brain: Ask about this method.** Sends the selection / current
  line to the brain and renders the answer in a side panel.
- **Activity bar sidebar.** Lists the brain entities anchored to the file you
  have open (URNs from `list_entities_by_file`). Refreshes when you switch
  editors or run `Brain: Refresh Context`.
- **Hover tooltips.** Hover a `@Autowired` / `@Service` / `@Repository` /
  `@Controller` / `@RestController` / `@Component` annotation in a Java or
  Kotlin file and the brain returns related entities.
- **Status bar 🧠 Brain.** Click to open the sidebar.

The extension is read-only against the brain — it never mutates the JSON
store. Writes are owned by the CLI (`brain extract`, `brain mcp serve
--allow-writes`).

## Configuration

| Setting | Default | Notes |
| --- | --- | --- |
| `companyBrain.mcpUrl` | `http://localhost:8765` | URL of the brain MCP server (HTTP transport). |
| `companyBrain.workspaceId` | _(empty)_ | Optional workspace UUID. Sent as `X-Workspace-Id` when set. |
| `companyBrain.hoverEnabled` | `true` | Toggle the Spring-annotation hover provider. |

Start the server next to the repo you want to query:

```bash
brain mcp serve --http --port 8765
```

…or directly:

```bash
python -m companybrain.harness.mcp_server \
  --workspace <UUID> --repo /path/to/repo --http --port 8765
```

## Build & package

```bash
cd ide/vscode-extension
npm install
npm run compile
npm run package      # produces company-brain-1.0.0.vsix
```

`vsce package` runs in CI on every PR that touches `ide/vscode-extension/**`
and uploads the artifact (see [.github/workflows/vscode-extension.yml]).

## Headless smoke test

The `test/headless-query.js` script speaks the MCP wire format directly so the
Python acceptance test can drive it against a fixture brain:

```bash
MCP_URL=http://localhost:8765 \
QUESTION="what does Foo.bar do?" \
node test/headless-query.js
```

It prints a JSON object with at least `summary_md` and `matches` and exits 0
on success.

## Wire format

The extension calls `POST {mcpUrl}/mcp` with JSON-RPC 2.0 envelopes:

```json
{ "jsonrpc": "2.0", "id": 1, "method": "tools/call",
  "params": { "name": "query_brain", "arguments": { "question": "…" } } }
```

The server responds with
`{ result: { content: [{type:"text", text:"<json>"}], isError, structuredContent } }`.
We prefer `structuredContent` and fall back to parsing `content[0].text`.
Tools used: `query_brain`, `read_entity`, `list_entities_by_file`,
`find_callers`, `find_dependencies`. See
[mcp_server.py](../../company-brain-ai/src/companybrain/harness/mcp_server.py)
for the canonical schemas.
