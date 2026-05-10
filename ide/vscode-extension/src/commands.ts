import * as vscode from 'vscode';
import { BrainClient } from './brain-client';
import { BrainQueryResult } from './types';

export function registerCommands(
  context: vscode.ExtensionContext,
  client: BrainClient,
  refreshSidebar: () => void,
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand('companyBrain.askBrain', () => askBrain(client)),
    vscode.commands.registerCommand('companyBrain.openSidebar', () =>
      vscode.commands.executeCommand('workbench.view.extension.companyBrain'),
    ),
    vscode.commands.registerCommand('companyBrain.refreshContext', () => refreshSidebar()),
    vscode.commands.registerCommand('companyBrain.extractCurrentEndpoint', () =>
      extractEndpointAtCursor(client),
    ),
  );
}

async function askBrain(client: BrainClient): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  const selection = editor
    ? editor.document.getText(editor.selection) ||
      editor.document.lineAt(editor.selection.active.line).text
    : '';

  const placeholder = selection
    ? `What does this do?\n\n${selection.slice(0, 200)}`
    : 'Ask the brain anything about your codebase';

  const question = await vscode.window.showInputBox({
    prompt: 'Ask the brain',
    value: selection ? `What does this do?\n\n${selection}` : '',
    placeHolder: placeholder,
    ignoreFocusOut: true,
  });
  if (!question) {
    return;
  }

  const panel = vscode.window.createWebviewPanel(
    'companyBrain.answer',
    'Brain Answer',
    vscode.ViewColumn.Beside,
    { enableScripts: false },
  );
  panel.webview.html = renderHtml({ status: 'loading', question });

  try {
    const result = await client.query(question);
    panel.webview.html = renderHtml({ status: 'ok', question, result });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    panel.webview.html = renderHtml({ status: 'error', question, error: msg });
  }
}

async function extractEndpointAtCursor(client: BrainClient): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showInformationMessage('Brain: no active editor');
    return;
  }
  const file = editor.document.fileName;
  try {
    const result = await client.listEntitiesByFile(file);
    if (result.count === 0) {
      vscode.window.showInformationMessage('Brain: no entities indexed for this file yet');
      return;
    }
    const pick = await vscode.window.showQuickPick(result.urns, {
      title: `Brain entities in ${file}`,
    });
    if (pick) {
      await vscode.env.clipboard.writeText(pick);
      vscode.window.showInformationMessage(`Brain: copied URN to clipboard\n${pick}`);
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    vscode.window.showErrorMessage(`Brain: ${msg}`);
  }
}

type RenderInput =
  | { status: 'loading'; question: string }
  | { status: 'ok'; question: string; result: BrainQueryResult }
  | { status: 'error'; question: string; error: string };

function renderHtml(input: RenderInput): string {
  const head = `<style>
    body { font-family: var(--vscode-font-family); padding: 1em; color: var(--vscode-foreground); }
    h2 { margin-top: 0; }
    pre { white-space: pre-wrap; background: var(--vscode-textBlockQuote-background); padding: 0.5em; }
    .q { font-style: italic; opacity: 0.85; margin-bottom: 1em; }
    .match { border-left: 3px solid var(--vscode-textLink-foreground); padding: 0.25em 0.75em; margin: 0.5em 0; }
    .urn { font-family: var(--vscode-editor-font-family); font-size: 0.9em; opacity: 0.8; }
  </style>`;

  if (input.status === 'loading') {
    return `${head}<h2>Brain</h2><p class="q">${escapeHtml(input.question)}</p><p>Querying…</p>`;
  }
  if (input.status === 'error') {
    return `${head}<h2>Brain — error</h2><p class="q">${escapeHtml(input.question)}</p><pre>${escapeHtml(input.error)}</pre>`;
  }
  const r = input.result;
  if (r.match_count === 0) {
    return `${head}<h2>Brain</h2><p class="q">${escapeHtml(input.question)}</p><p>No matches.</p>`;
  }
  const matches = r.matches
    .slice(0, 10)
    .map(
      (m) => `<div class="match">
        <div><strong>${escapeHtml(m.qualified_name)}</strong> <span class="urn">(${escapeHtml(m.entity_type)})</span></div>
        <div class="urn">${escapeHtml(m.urn)}</div>
        ${m.file ? `<div class="urn">${escapeHtml(m.file)}</div>` : ''}
        ${m.summary ? `<div>${escapeHtml(m.summary)}</div>` : ''}
      </div>`,
    )
    .join('\n');
  return `${head}<h2>Brain — ${r.match_count} match${r.match_count === 1 ? '' : 'es'}</h2>
    <p class="q">${escapeHtml(input.question)}</p>
    ${matches}`;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
