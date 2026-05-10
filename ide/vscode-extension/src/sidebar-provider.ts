import * as vscode from 'vscode';
import { BrainClient } from './brain-client';

class EntityItem extends vscode.TreeItem {
  constructor(
    label: string,
    public readonly urn: string,
    description?: string,
  ) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.tooltip = urn;
    this.description = description;
    this.contextValue = 'companyBrain.entity';
  }
}

export class SidebarProvider implements vscode.TreeDataProvider<EntityItem> {
  private readonly _onDidChange = new vscode.EventEmitter<EntityItem | undefined>();
  readonly onDidChangeTreeData = this._onDidChange.event;

  constructor(private readonly client: BrainClient) {
    vscode.window.onDidChangeActiveTextEditor(() => this.refresh());
  }

  refresh(): void {
    this._onDidChange.fire(undefined);
  }

  getTreeItem(element: EntityItem): vscode.TreeItem {
    return element;
  }

  async getChildren(): Promise<EntityItem[]> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      return [
        new EntityItem('(no active editor)', '', 'open a file to see context'),
      ];
    }
    const file = editor.document.fileName;
    try {
      const result = await this.client.listEntitiesByFile(file);
      if (result.count === 0) {
        return [new EntityItem('(no brain context yet)', '', file)];
      }
      return result.urns.map((urn) => {
        const tail = urn.split(':').pop() ?? urn;
        return new EntityItem(tail, urn, urn);
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return [new EntityItem('(error)', '', msg)];
    }
  }
}
