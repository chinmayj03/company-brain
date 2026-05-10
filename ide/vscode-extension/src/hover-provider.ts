import * as vscode from 'vscode';
import { BrainClient } from './brain-client';

const SPRING_ANNOTATIONS = new Set([
  '@Autowired',
  '@Repository',
  '@Service',
  '@Controller',
  '@RestController',
  '@Component',
]);

export class BrainHoverProvider implements vscode.HoverProvider {
  constructor(private readonly client: BrainClient) {}

  async provideHover(
    doc: vscode.TextDocument,
    pos: vscode.Position,
    token: vscode.CancellationToken,
  ): Promise<vscode.Hover | undefined> {
    const cfg = vscode.workspace.getConfiguration('companyBrain');
    if (!cfg.get<boolean>('hoverEnabled', true)) {
      return undefined;
    }

    const range = doc.getWordRangeAtPosition(pos, /@\w+/);
    if (!range) {
      return undefined;
    }
    const word = doc.getText(range);
    if (!SPRING_ANNOTATIONS.has(word)) {
      return undefined;
    }

    // The line below an annotation is usually the field/class it decorates;
    // fall back to the same line if we're at end-of-file.
    const targetLine = pos.line + 1 < doc.lineCount ? pos.line + 1 : pos.line;
    const decorated = doc.lineAt(targetLine).text.trim();
    if (!decorated) {
      return undefined;
    }

    if (token.isCancellationRequested) {
      return undefined;
    }

    try {
      const result = await this.client.query(
        `Tell me about "${decorated}" (annotated ${word})`,
      );
      const md = new vscode.MarkdownString(buildHoverMarkdown(word, decorated, result.matches));
      md.isTrusted = false;
      md.supportHtml = false;
      return new vscode.Hover(md, range);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      const md = new vscode.MarkdownString(`**Brain**: unable to fetch (${msg})`);
      return new vscode.Hover(md, range);
    }
  }
}

function buildHoverMarkdown(
  annotation: string,
  decorated: string,
  matches: { qualified_name: string; summary: string; urn: string }[],
): string {
  const head = `**Brain** — ${annotation} on \`${decorated}\``;
  if (matches.length === 0) {
    return `${head}\n\n_no entities matched_`;
  }
  const lines = matches.slice(0, 3).map((m) => {
    const summary = m.summary?.trim() || '(no summary yet)';
    return `- \`${m.qualified_name}\` — ${summary}`;
  });
  return `${head}\n\n${lines.join('\n')}`;
}
