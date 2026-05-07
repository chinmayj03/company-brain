/**
 * packages/repo-map/src/formatter.ts
 *
 * Converts FileEntry[] into the final repo-map text.
 *
 * Output format (aider-inspired):
 *
 *   src/billing/handler.ts:
 *     export class BillingService extends EventEmitter
 *       async charge(req: ChargeRequest): Promise<ChargeResult>
 *       async refund(transactionId: string): Promise<boolean>
 *     export const MAX_RETRY_ATTEMPTS: number
 *     export interface ChargeRequest
 *
 *   src/utils.ts:
 *     export function slugify(text: string): string
 *     export function truncate(text: string, maxLen: number): string
 *
 * Rules:
 *   - Files sorted by totalInDegree desc (most-connected first)
 *   - Within a file: classes first (with methods indented), then interfaces,
 *     then functions, then constants
 *   - Methods are shown indented under their parent class
 *   - Non-exported symbols omitted (unless exportedOnly=false)
 */

import type { FileEntry, SymbolEntry } from "./types.js";
import { countTokens, fitToBudget }    from "./token-budget.js";

const INDENT = "  ";

export interface FormatOptions {
  exportedOnly:  boolean;
  tokenBudget:   number;
  /** If true, include a header line showing the scope and token stats. */
  includeHeader: boolean;
  scope:         string;
}

export interface FormatResult {
  text:            string;
  tokenCount:      number;
  truncated:       boolean;
  filesIncluded:   number;
  symbolsIncluded: number;
}

export function formatRepoMap(
  files:   FileEntry[],
  options: FormatOptions,
): FormatResult {
  // Sort files: most-referenced first
  const sorted = [...files].sort((a, b) => b.totalInDegree - a.totalInDegree);

  const lines: string[] = [];
  if (options.includeHeader) {
    lines.push(`# Repo map — ${options.scope}`);
    lines.push(`# ${files.length} files · sorted by call-graph centrality`);
    lines.push("");
  }

  let symbolsIncluded = 0;
  let filesIncluded   = 0;
  let anyTruncated    = false;

  // Reserve ~10% of budget for the header and file-path lines
  const bodyBudget = Math.floor(options.tokenBudget * 0.9);
  const perFileBudget = sorted.length > 0
    ? Math.max(Math.floor(bodyBudget / sorted.length), 30)
    : bodyBudget;

  for (const file of sorted) {
    const fileLines: string[] = [];
    fileLines.push(`${file.path}:`);

    const syms = filterAndSortSymbols(file.symbols, options.exportedOnly);
    const rendered = renderSymbols(syms);
    symbolsIncluded += rendered.count;

    for (const line of rendered.lines) {
      fileLines.push(line);
    }

    const fileText = fileLines.join("\n");
    const { text: fittedText, truncated } = fitToBudget(fileText, perFileBudget);
    if (truncated) anyTruncated = true;

    lines.push(fittedText);
    lines.push(""); // blank line between files
    filesIncluded++;
  }

  const text       = lines.join("\n");
  const tokenCount = countTokens(text);

  // Final safety: if we somehow still exceed the budget, hard-truncate
  const { text: finalText, truncated: finalTruncated } = fitToBudget(text, options.tokenBudget);

  return {
    text:           finalText,
    tokenCount:     countTokens(finalText),
    truncated:      anyTruncated || finalTruncated,
    filesIncluded,
    symbolsIncluded,
  };
}

// ── Symbol rendering ──────────────────────────────────────────────────────────

interface RenderResult {
  lines: string[];
  count: number;
}

function filterAndSortSymbols(
  symbols:      SymbolEntry[],
  exportedOnly: boolean,
): SymbolEntry[] {
  const filtered = exportedOnly ? symbols.filter(s => s.exported) : symbols;

  // Sort: classes > interfaces > functions > constants > methods
  const PRIORITY: Record<string, number> = {
    Class:     0,
    Interface: 1,
    Function:  2,
    Constant:  3,
    TypeAlias: 4,
    Decorator: 5,
    Method:    6,  // rendered under their class, not top-level
    Module:    7,
  };

  return filtered.sort((a, b) => {
    const pa = PRIORITY[a.kind] ?? 99;
    const pb = PRIORITY[b.kind] ?? 99;
    if (pa !== pb) return pa - pb;
    return b.inDegree - a.inDegree; // within same kind: more-referenced first
  });
}

function renderSymbols(symbols: SymbolEntry[]): RenderResult {
  const lines: string[] = [];
  let count = 0;

  // Group methods under their parent class
  const classNames = new Set(
    symbols.filter(s => s.kind === "Class").map(s => s.name)
  );
  const methodsByClass = new Map<string, SymbolEntry[]>();
  const topLevel: SymbolEntry[] = [];

  for (const sym of symbols) {
    if (sym.kind === "Method" && sym.qualifiedName.includes(".")) {
      const parentName = sym.qualifiedName.split(".").slice(0, -1).join(".");
      if (classNames.has(parentName)) {
        const arr = methodsByClass.get(parentName) ?? [];
        arr.push(sym);
        methodsByClass.set(parentName, arr);
        continue;
      }
    }
    topLevel.push(sym);
  }

  for (const sym of topLevel) {
    const prefix = sym.exported ? "export " : "";
    lines.push(`${INDENT}${prefix}${sym.signature}`);
    count++;

    // Render methods for this class
    const methods = methodsByClass.get(sym.name);
    if (methods) {
      for (const m of methods.sort((a, b) => b.inDegree - a.inDegree)) {
        lines.push(`${INDENT}${INDENT}${m.signature}`);
        count++;
      }
    }
  }

  return { lines, count };
}

// ── Signature builder ─────────────────────────────────────────────────────────

/**
 * Build a compact one-line signature for a symbol node.
 * These are the lines shown in the repo map.
 */
export function buildSignature(
  kind:         string,
  name:         string,
  attributes:   Record<string, unknown>,
): string {
  const paramNames  = splitAttr(attributes["attr_param_names"] as string | undefined);
  const returnType  = attributes["attr_return_type"] as string | undefined;
  const isAsync     = attributes["attr_is_async"]    as boolean | undefined;
  const isAbstract  = attributes["attr_is_abstract"] as boolean | undefined;
  const isStatic    = attributes["attr_is_static"]   as boolean | undefined;
  const visibility  = attributes["attr_visibility"]  as string | undefined;
  const extendsStr  = attributes["attr_extends"]     as string | undefined;

  const asyncPfx    = isAsync    ? "async " : "";
  const abstractPfx = isAbstract ? "abstract " : "";
  const staticPfx   = isStatic   ? "static " : "";
  const visPfx      = (visibility && visibility !== "public") ? `${visibility} ` : "";
  const params      = paramNames.length > 0 ? paramNames.join(", ") : "";
  const ret         = returnType ? `: ${returnType}` : "";
  const ext         = extendsStr ? ` extends ${extendsStr.split(",")[0]}` : "";

  switch (kind) {
    case "Class":
      return `${abstractPfx}class ${name}${ext}`;
    case "Interface":
      return `interface ${name}${ext}`;
    case "TypeAlias":
      return `type ${name}`;
    case "Function":
      return `${asyncPfx}function ${name}(${params})${ret}`;
    case "Method":
      return `${visPfx}${staticPfx}${asyncPfx}${abstractPfx}${name}(${params})${ret}`;
    case "Constant":
      return `const ${name}`;
    case "Decorator":
      return `@${name}`;
    default:
      return name;
  }
}

function splitAttr(s: string | undefined): string[] {
  if (!s) return [];
  return s.split(",").map(x => x.trim()).filter(Boolean);
}
