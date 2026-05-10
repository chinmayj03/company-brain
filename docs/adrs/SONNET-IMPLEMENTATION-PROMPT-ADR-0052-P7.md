# Implementation Prompt — ADR-0052 Phase 7 (VS Code IDE integration)

**Single-PR Claude Code session. ~5 days. Adds the VS Code extension that talks to the brain via the MCP server (built in P5). Scaffolds JetBrains plugin for later.**

---

## Pre-flight

1. Read ADR-0052 §"Phase 7".
2. Verify P5 is on `main` (the MCP server is the only hard prereq — P6 features are optional bonuses for the IDE):
   ```bash
   git log --oneline main | head -100 | grep -q "ADR-0052 P5" || exit 1
   ```
3. `git checkout -b feature/adr-0052-p7-ide-integration`.
4. Confirm Node.js 20+ and `npx vsce` are installed locally for packaging.

---

## File ownership for THIS PR

CREATE / MODIFY exclusively:

```
ide/vscode-extension/.gitignore
ide/vscode-extension/.vscodeignore
ide/vscode-extension/package.json
ide/vscode-extension/tsconfig.json
ide/vscode-extension/src/extension.ts
ide/vscode-extension/src/brain-client.ts
ide/vscode-extension/src/sidebar-provider.ts
ide/vscode-extension/src/hover-provider.ts
ide/vscode-extension/src/commands.ts
ide/vscode-extension/src/types.ts
ide/vscode-extension/test/headless-query.js
ide/vscode-extension/README.md
ide/jetbrains-plugin/                                  # skeleton only
ide/jetbrains-plugin/build.gradle.kts
ide/jetbrains-plugin/src/main/kotlin/com/companybrain/.../...kt
ide/jetbrains-plugin/README.md
docs/IDE-INTEGRATION.md
.github/workflows/vscode-extension.yml
tests/acceptance/test_harness_p7_ide.py
```

You do NOT touch any Python file in this PR (the entire backend is reused; the IDE talks to the existing MCP server from P5).

---

## Implementation steps

### 1. Extension manifest (`ide/vscode-extension/package.json`)

```json
{
  "name": "company-brain",
  "displayName": "Company Brain",
  "description": "Codebase context + extraction insights, inline.",
  "version": "1.0.0",
  "engines": { "vscode": "^1.85.0" },
  "categories": ["Other", "Education", "Programming Languages"],
  "main": "./out/extension.js",
  "contributes": {
    "configuration": {
      "title": "Company Brain",
      "properties": {
        "companyBrain.mcpUrl": {
          "type": "string",
          "default": "http://localhost:8765",
          "description": "URL of the brain MCP server."
        },
        "companyBrain.workspaceId": {
          "type": "string",
          "description": "Workspace UUID for this repo."
        }
      }
    },
    "commands": [
      { "command": "companyBrain.askBrain",     "title": "Ask Brain about this method" },
      { "command": "companyBrain.openSidebar",  "title": "Open Brain Sidebar" },
      { "command": "companyBrain.extractCurrentEndpoint", "title": "Extract endpoint at cursor" }
    ],
    "menus": {
      "editor/context": [
        { "command": "companyBrain.askBrain", "group": "navigation@1", "when": "editorTextFocus" }
      ]
    },
    "viewsContainers": {
      "activitybar": [
        { "id": "companyBrain", "title": "Company Brain", "icon": "media/brain.svg" }
      ]
    },
    "views": {
      "companyBrain": [
        { "id": "companyBrain.context", "name": "Context for current file" }
      ]
    }
  },
  "scripts": {
    "compile": "tsc -p ./",
    "watch":   "tsc -watch -p ./",
    "package": "vsce package",
    "test":    "node ./out/test/runTest.js"
  },
  "devDependencies": {
    "@types/vscode": "^1.85.0",
    "@types/node": "^20.0.0",
    "typescript": "^5.3.0",
    "@vscode/vsce": "^2.22.0"
  }
}
```

### 2. `src/brain-client.ts` — MCP wrapper

```typescript
import * as vscode from 'vscode';

export interface BrainQueryResult {
  summary_md: string;
  call_chain?: string[];
  sql_quotes?: string[];
  affected_entities?: string[];
  notes?: string[];
}

export class BrainClient {
  private mcpUrl: string;
  constructor() {
    this.mcpUrl = vscode.workspace.getConfiguration('companyBrain').get('mcpUrl', 'http://localhost:8765');
  }

  async query(question: string): Promise<BrainQueryResult> {
    const resp = await fetch(`${this.mcpUrl}/mcp/tools/call`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: 'query_brain', arguments: {question}}),
    });
    return (await resp.json()) as BrainQueryResult;
  }

  async readEntity(urn: string) {
    const resp = await fetch(`${this.mcpUrl}/mcp/tools/call`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: 'read_entity', arguments: {urn}}),
    });
    return await resp.json();
  }

  async listEntitiesByFile(file: string) { /* ... */ }
}
```

### 3. `src/extension.ts` — wire commands + providers

```typescript
import * as vscode from 'vscode';
import { BrainClient } from './brain-client';
import { SidebarProvider } from './sidebar-provider';
import { BrainHoverProvider } from './hover-provider';

export function activate(context: vscode.ExtensionContext) {
  const client = new BrainClient();

  // Right-click → Ask Brain
  context.subscriptions.push(vscode.commands.registerCommand('companyBrain.askBrain', async () => {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return;
    const selection = editor.document.getText(editor.selection) || editor.document.lineAt(editor.selection.active.line).text;
    const question = await vscode.window.showInputBox({
      prompt: 'Ask the brain', value: `What does this do?\n\n\`\`\`\n${selection}\n\`\`\``,
    });
    if (!question) return;

    const panel = vscode.window.createWebviewPanel('companyBrain.answer', 'Brain Answer', vscode.ViewColumn.Beside, {});
    panel.webview.html = '<p>Querying…</p>';
    const result = await client.query(question);
    panel.webview.html = renderAnswerHtml(result);
  }));

  // Sidebar
  vscode.window.registerTreeDataProvider('companyBrain.context', new SidebarProvider(client));

  // Hover for Spring annotations
  vscode.languages.registerHoverProvider({ language: 'java' }, new BrainHoverProvider(client));

  // Status bar
  const sb = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  sb.text = '🧠 Brain';
  sb.command = 'companyBrain.openSidebar';
  sb.show();
}

function renderAnswerHtml(r: any): string {
  return `<style>body{font-family:sans-serif;padding:1em}</style>
    <h2>Brain Answer</h2>
    <div>${markdownToHtml(r.summary_md ?? '')}</div>
    ${r.sql_quotes ? `<h3>SQL</h3><pre>${r.sql_quotes.join('\n\n')}</pre>` : ''}
    ${r.affected_entities ? `<h3>Affected entities</h3><ul>${r.affected_entities.map((e:string)=>`<li>${e}</li>`).join('')}</ul>` : ''}
    ${r.notes?.length ? `<h3>Notes</h3><ul>${r.notes.map((n:string)=>`<li>${n}</li>`).join('')}</ul>` : ''}`;
}

function markdownToHtml(md: string): string { /* tiny markdown-it wrapper */ return md; }
```

### 4. `src/sidebar-provider.ts` — context for current file

```typescript
import * as vscode from 'vscode';
import { BrainClient } from './brain-client';

export class SidebarProvider implements vscode.TreeDataProvider<EntityItem> {
  constructor(private client: BrainClient) {}
  private _onDidChange = new vscode.EventEmitter<EntityItem | undefined>();
  readonly onDidChangeTreeData = this._onDidChange.event;

  refresh() { this._onDidChange.fire(undefined); }
  getTreeItem(e: EntityItem) { return e; }

  async getChildren(): Promise<EntityItem[]> {
    const file = vscode.window.activeTextEditor?.document.fileName;
    if (!file) return [];
    const entities = await this.client.listEntitiesByFile(file);
    return entities.map((e: any) => new EntityItem(e.name, e.urn, e.entity_type));
  }
}

class EntityItem extends vscode.TreeItem {
  constructor(label: string, urn: string, type: string) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.tooltip = urn;
    this.description = type;
  }
}
```

### 5. `src/hover-provider.ts` — tooltips on Spring annotations

```typescript
import * as vscode from 'vscode';
import { BrainClient } from './brain-client';

export class BrainHoverProvider implements vscode.HoverProvider {
  constructor(private client: BrainClient) {}

  async provideHover(doc: vscode.TextDocument, pos: vscode.Position) {
    const range = doc.getWordRangeAtPosition(pos, /@\w+/);
    if (!range) return;
    const word = doc.getText(range);
    if (!['@Autowired', '@Repository', '@Service', '@Controller'].includes(word)) return;

    // Look up the field/class this annotation decorates and query the brain
    const line = doc.lineAt(pos.line + 1).text.trim();
    const result = await this.client.query(`Tell me about ${line} (annotated ${word})`);
    return new vscode.Hover([{ language: 'markdown', value: result.summary_md }]);
  }
}
```

### 6. JetBrains skeleton (`ide/jetbrains-plugin/`)

Just the Gradle scaffolding + a single placeholder action that opens a notification "JetBrains support coming soon — track at github.com/.../issues/N". Do NOT publish to JetBrains Marketplace in this PR; that comes after the VS Code version is battle-tested.

### 7. CI workflow

`.github/workflows/vscode-extension.yml`:

```yaml
name: VS Code Extension
on:
  pull_request:
    paths: ['ide/vscode-extension/**']
  push:
    branches: [main]
    paths: ['ide/vscode-extension/**']

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '20' }
      - working-directory: ide/vscode-extension
        run: |
          npm install
          npm run compile
          npx vsce package --no-yarn
      - uses: actions/upload-artifact@v4
        with:
          name: company-brain-vsix
          path: ide/vscode-extension/*.vsix
```

---

## Acceptance test

`tests/acceptance/test_harness_p7_ide.py`:

```python
import subprocess
from pathlib import Path


def test_vscode_extension_packages_cleanly():
    proc = subprocess.run(
        ["npm", "install", "--silent"],
        cwd="ide/vscode-extension",
    )
    assert proc.returncode == 0
    proc = subprocess.run(
        ["npx", "vsce", "package", "--no-yarn"],
        cwd="ide/vscode-extension", capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    vsix = list(Path("ide/vscode-extension").glob("*.vsix"))
    assert vsix, "No .vsix produced"


@pytest.mark.asyncio
async def test_extension_brain_client_calls_mcp_server():
    """Headless: extension's brain-client.ts can call MCP server tools."""
    async with start_mcp_server(repo="fixtures/...") as srv:
        proc = subprocess.run(
            ["node", "ide/vscode-extension/test/headless-query.js"],
            env={"MCP_URL": srv.url, "QUESTION": "what does Foo.bar do?"},
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        assert "summary_md" in proc.stdout
```

---

## PR description

```
feat(harness): VS Code IDE integration (ADR-0052 P7)

Adds VS Code extension that talks to the brain via the MCP server (P5).
Features:
- Right-click → Ask brain about selected text/method
- Sidebar: brain context for the open file (entities, edges, types)
- Hover: tooltip enrichment for @Autowired/@Service/@Repository/@Controller
- Status bar: 🧠 Brain quick-access
- Configurable MCP URL + workspace ID

JetBrains plugin scaffold included; defer Marketplace publishing until
VS Code version is battle-tested.

CI: GitHub Actions packages .vsix on every PR; uploads as artifact.
```
