/**
 * packages/extractors/core-ts/src/types.ts
 *
 * Internal types used across the core-ts extractor passes.
 * These are NOT part of the public API — consumers use NodeEnvelope/EdgeEnvelope from @company-brain/schema.
 */

import type { NodeEnvelope, EdgeEnvelope } from "@company-brain/schema";

// ── Source range (compact form before building the full envelope) ─────────────

export interface SourceRange {
  startLine:   number;
  startColumn: number;
  startOffset: number;
  endLine:     number;
  endColumn:   number;
  endOffset:   number;
}

// ── Intermediate representations produced by the tree-sitter pass ─────────────

export interface ExtractedFile {
  filePath:  string;   // repo-relative path, e.g. "src/billing/handler.ts"
  language:  "typescript" | "tsx" | "javascript" | "jsx";
  lineCount: number;
  byteSize:  number;
  checksum:  string;   // SHA-256 of file contents (hex)
}

export interface ExtractedSymbol {
  /** Fully-qualified name within the file, e.g. "BillingService.charge" */
  qualifiedName: string;
  /** Simple name, e.g. "charge" */
  name:          string;
  kind:
    | "class"
    | "interface"
    | "type_alias"
    | "function"
    | "method"
    | "property"
    | "constant"
    | "decorator"
    | "enum"
    | "enum_member"
    | "namespace";
  range:         SourceRange;
  /** True if the symbol is exported at the module level. */
  exported:      boolean;
  /** For methods/properties: the containing class/interface name. */
  parentName?:   string;
  /** For classes: names of interfaces they implement. */
  implements?:   string[];
  /** For classes/interfaces: names of parent types they extend. */
  extends?:      string[];
  /** For functions/methods: parameter names (positional). */
  paramNames?:   string[];
  /** For functions/methods: the text of the return type annotation (if present). */
  returnType?:   string;
  /** Visibility modifier: public|protected|private (methods/properties only). */
  visibility?:   "public" | "protected" | "private";
  /** True if async. */
  isAsync?:      boolean;
  /** True if static (method/property). */
  isStatic?:     boolean;
  /** True if abstract. */
  isAbstract?:   boolean;
  /** Hash of the function/method body (for change detection). */
  bodyHash?:     string;
  /** JSDoc / leading comment text. */
  docstring?:    string;
}

export interface ExtractedImport {
  /** The raw import specifier: "./utils", "react", "@/components/Button", etc. */
  specifier:      string;
  /** Names imported: for `import { foo, bar }` → ["foo", "bar"]. */
  namedImports:   string[];
  /** Default import name if present. */
  defaultImport?:  string;
  /** Namespace import: `import * as X` → "X". */
  namespaceImport?: string;
  /** True if this is a side-effect import (`import "./style.css"`). */
  sideEffect:     boolean;
  /** True if dynamic import (`import("...")`). */
  dynamic:        boolean;
  range:          SourceRange;
}

export interface ExtractedCallSite {
  /** The callee expression text, e.g. "BillingService.charge", "fetch". */
  calleeText:  string;
  range:       SourceRange;
  /** The containing function/method qualified name (if determinable). */
  callerName?: string;
}

export interface ExtractedDecorator {
  name:       string;
  argsText?:  string;
  targetKind: "class" | "method" | "property" | "parameter";
  /** Qualified name of the symbol being decorated. */
  targetName: string;
  range:      SourceRange;
}

// ── Collected results from one file pass ─────────────────────────────────────

export interface FilePassResult {
  file:       ExtractedFile;
  symbols:    ExtractedSymbol[];
  imports:    ExtractedImport[];
  callSites:  ExtractedCallSite[];
  decorators: ExtractedDecorator[];
}

// ── Write batch accumulated across all files ──────────────────────────────────

export interface WriteBatch {
  nodes: NodeEnvelope[];
  edges: EdgeEnvelope[];
}
