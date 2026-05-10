import * as vscode from 'vscode';
import { BrainClient } from './brain-client';
import { registerCommands } from './commands';
import { BrainHoverProvider } from './hover-provider';
import { SidebarProvider } from './sidebar-provider';

export function activate(context: vscode.ExtensionContext): void {
  const client = new BrainClient();
  const sidebar = new SidebarProvider(client);

  context.subscriptions.push(
    vscode.window.registerTreeDataProvider('companyBrain.context', sidebar),
  );

  registerCommands(context, client, () => sidebar.refresh());

  // Hover enrichment for Spring annotations in Java files.
  context.subscriptions.push(
    vscode.languages.registerHoverProvider(
      [{ language: 'java' }, { language: 'kotlin' }],
      new BrainHoverProvider(client),
    ),
  );

  // Status bar quick-access — clicking opens the sidebar.
  const item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  item.text = '🧠 Brain';
  item.tooltip = 'Open the Company Brain sidebar';
  item.command = 'companyBrain.openSidebar';
  item.show();
  context.subscriptions.push(item);

  // Refresh sidebar when the workspace config changes (mcpUrl / workspaceId).
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration('companyBrain')) {
        sidebar.refresh();
      }
    }),
  );
}

export function deactivate(): void {
  // nothing to tear down — the client is HTTP-only.
}
