/**
 * Company Brain VS Code Extension — entry point
 *
 * Activates on startup. Registers hover providers, commands, and the
 * sidebar webview. All data comes from the Company Brain API (backend + AI).
 *
 * The extension is the thin surface layer — it knows how to:
 *  1. Identify what symbol the cursor is on
 *  2. Call the API to get blast radius / context
 *  3. Render the result in a hover or sidebar panel
 *
 * All graph intelligence lives in the backend + AI services.
 */

const vscode = require('vscode');
const axios = require('axios');

let apiClient;
let aiClient;

function activate(context) {
  console.log('Company Brain extension activated');

  initClients();

  // Re-init clients when config changes (user updates API URL or token)
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration('companyBrain')) initClients();
    })
  );

  // ── Hover provider: show context on symbol hover ──────────────────
  const hoverProvider = vscode.languages.registerHoverProvider(
    ['javascript', 'typescript', 'typescriptreact', 'javascriptreact', 'java', 'python'],
    {
      async provideHover(document, position) {
        const wordRange = document.getWordRangeAtPosition(position);
        if (!wordRange) return null;

        const symbol = document.getText(wordRange);
        if (symbol.length < 3) return null; // skip short tokens

        try {
          const results = await apiClient.get('/search', {
            params: { q: symbol, limit: 1 },
          });

          const nodes = results.data?.nodes;
          if (!nodes?.length) return null;

          const node = nodes[0];
          const context = await apiClient.get(`/nodes/${node.id}/context`, {
            params: { size: 3 },
          });

          return buildHoverContent(node, context.data);
        } catch {
          return null; // fail silently — don't disrupt the editor
        }
      },
    }
  );
  context.subscriptions.push(hoverProvider);

  // ── Command: Show Blast Radius ────────────────────────────────────
  const showBlastRadius = vscode.commands.registerCommand(
    'companyBrain.showBlastRadius',
    async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;

      const symbol = getSymbolAtCursor(editor);
      if (!symbol) {
        vscode.window.showInformationMessage('Place your cursor on a symbol to see its blast radius');
        return;
      }

      vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: `Finding blast radius for "${symbol}"...` },
        async () => {
          try {
            const searchResult = await apiClient.get('/search', { params: { q: symbol, limit: 1 } });
            const node = searchResult.data?.nodes?.[0];
            if (!node) {
              vscode.window.showWarningMessage(`"${symbol}" not found in the dependency graph`);
              return;
            }

            const blastRadius = await apiClient.get(`/nodes/${node.id}/blast-radius`);
            showBlastRadiusPanel(symbol, node, blastRadius.data, context);
          } catch (err) {
            vscode.window.showErrorMessage(`Company Brain: ${err.message}`);
          }
        }
      );
    }
  );
  context.subscriptions.push(showBlastRadius);

  // ── Command: Ask a Question ───────────────────────────────────────
  const askCommand = vscode.commands.registerCommand('companyBrain.ask', async () => {
    const question = await vscode.window.showInputBox({
      prompt: 'Ask Company Brain anything about this codebase',
      placeHolder: 'e.g. What breaks if I rename the amount field?',
    });
    if (!question) return;

    const editor = vscode.window.activeTextEditor;
    const symbol = editor ? getSymbolAtCursor(editor) : null;

    vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: 'Company Brain is thinking...' },
      async () => {
        try {
          const result = await aiClient.post('/query', {
            question,
            context_symbol: symbol,
            file_path: editor?.document.fileName,
          });
          showAnswerPanel(question, result.data, context);
        } catch (err) {
          vscode.window.showErrorMessage(`Company Brain: ${err.message}`);
        }
      }
    );
  });
  context.subscriptions.push(askCommand);
}

// ── Helpers ───────────────────────────────────────────────────────────────

function initClients() {
  const config = vscode.workspace.getConfiguration('companyBrain');
  const token = config.get('token');
  const headers = token ? { Authorization: `Bearer ${token}` } : {};

  apiClient = axios.create({
    baseURL: `${config.get('apiUrl')}/v1`,
    headers,
    timeout: 5000,
  });

  aiClient = axios.create({
    baseURL: config.get('aiApiUrl'),
    headers,
    timeout: 30000,
  });
}

function getSymbolAtCursor(editor) {
  const position = editor.selection.active;
  const wordRange = editor.document.getWordRangeAtPosition(position);
  return wordRange ? editor.document.getText(wordRange) : null;
}

function buildHoverContent(node, contextData) {
  const md = new vscode.MarkdownString();
  md.isTrusted = true;

  md.appendMarkdown(`**${node.name}** \`${node.nodeType}\`\n\n`);

  // Latest business context
  const synthesis = contextData?.entries?.find((e) => e.contextType === 'llm_synthesis');
  if (synthesis?.body) {
    md.appendMarkdown(`${synthesis.body}\n\n`);
  }

  // Risk flags
  const risks = contextData?.entries?.filter((e) => e.contextType === 'risk_flag');
  if (risks?.length) {
    md.appendMarkdown(`⚠️ **Risk:** ${risks[0].body}\n\n`);
  }

  // Owner
  if (node.metadata?.owner_team) {
    md.appendMarkdown(`👤 Owner: **${node.metadata.owner_team}**\n\n`);
  }

  // Actions
  md.appendMarkdown(
    `[Show blast radius](command:companyBrain.showBlastRadius) · ` +
    `[Ask a question](command:companyBrain.ask)`
  );

  return new vscode.Hover(md);
}

function showBlastRadiusPanel(symbol, originNode, blastRadius, context) {
  const panel = vscode.window.createWebviewPanel(
    'companyBrainBlastRadius',
    `Blast Radius: ${symbol}`,
    vscode.ViewColumn.Beside,
    { enableScripts: true }
  );

  const affected = blastRadius.affectedNodes || [];
  const byDepth = affected.reduce((acc, n) => {
    (acc[n.depth] = acc[n.depth] || []).push(n);
    return acc;
  }, {});

  panel.webview.html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); padding: 16px; }
    h2 { font-size: 14px; margin-bottom: 16px; }
    .depth { margin-bottom: 12px; }
    .depth-label { font-size: 11px; color: var(--vscode-descriptionForeground); margin-bottom: 4px; }
    .node { padding: 6px 10px; background: var(--vscode-editor-inactiveSelectionBackground);
            border-radius: 4px; margin-bottom: 4px; font-size: 12px; }
    .node .type { color: var(--vscode-descriptionForeground); margin-left: 6px; }
    .node .team { float: right; color: var(--vscode-descriptionForeground); }
    .warning { color: var(--vscode-editorWarning-foreground); font-size: 12px; margin-top: 16px; }
  </style>
</head>
<body>
  <h2>⚡ Blast radius of changing <strong>${symbol}</strong></h2>
  <p style="font-size:11px; color:var(--vscode-descriptionForeground)">
    ${affected.length} affected nodes · ${blastRadius.queryDurationMs}ms
  </p>
  ${Object.entries(byDepth).map(([depth, nodes]) => `
    <div class="depth">
      <div class="depth-label">Hop ${depth}</div>
      ${nodes.map((n) => `
        <div class="node">
          ${n.nodeName}
          <span class="type">${n.nodeType}</span>
          ${n.owningTeam ? `<span class="team">${n.owningTeam}</span>` : ''}
        </div>
      `).join('')}
    </div>
  `).join('')}
  ${affected.length === 0 ? '<p class="warning">No dependents found in the graph yet. Run the context pipeline first.</p>' : ''}
</body>
</html>`;
}

function showAnswerPanel(question, result, context) {
  const panel = vscode.window.createWebviewPanel(
    'companyBrainAnswer',
    'Company Brain',
    vscode.ViewColumn.Beside,
    {}
  );
  panel.webview.html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); padding: 16px; }
    .question { font-size: 13px; font-weight: bold; margin-bottom: 12px; }
    .answer { font-size: 12px; line-height: 1.6; white-space: pre-wrap; }
    .source { font-size: 11px; color: var(--vscode-descriptionForeground); margin-top: 12px; }
  </style>
</head>
<body>
  <div class="question">Q: ${question}</div>
  <div class="answer">${result.answer}</div>
  ${result.sources?.length ? `
    <div class="source">Sources: ${result.sources.map((s) => s.label).join(', ')}</div>
  ` : ''}
</body>
</html>`;
}

function deactivate() {}

module.exports = { activate, deactivate };
