/**
 * packages/extractors/core-ts/src/passes/tree-sitter-pass.ts
 *
 * Pass 1 — Fast structural extraction using tree-sitter.
 *
 * What this does:
 *   - Parses every .ts/.tsx/.js/.jsx file with tree-sitter (native, no TS compiler)
 *   - Visits the CST to collect: classes, interfaces, type aliases, functions,
 *     methods, properties, constants, decorators, imports, call sites
 *   - Returns FilePassResult (intermediate) — NOT yet NodeEnvelope
 *
 * What this does NOT do (left for the envelope builder):
 *   - Cross-file type resolution
 *   - Call graph resolution beyond simple text matching
 *   - Writing to Neo4j (that's the job of the extractor)
 *
 * Design notes:
 *   - tree-sitter grammar nodes are matched by type string, which is stable
 *     across grammar versions for the features we use.
 *   - We deliberately avoid ts-morph in this pass to keep it fast and
 *     dependency-free from a tsconfig. ts-morph is a potential Phase 1.5 addition.
 */

import fs from "fs";
import crypto from "crypto";
import type {
  FilePassResult,
  ExtractedFile,
  ExtractedSymbol,
  ExtractedImport,
  ExtractedCallSite,
  ExtractedDecorator,
  SourceRange,
} from "../types.js";

// ── tree-sitter lazy-loaded to avoid hard dependency at module load time ──────
// This lets tests mock the module without requiring native bindings.

let _Parser: any = null;
let _tsLang: any = null;
let _tsxLang: any = null;
let _jsLang: any = null;

async function getParser(lang: "typescript" | "tsx" | "javascript" | "jsx"): Promise<any> {
  if (!_Parser) {
    const Parser = (await import("tree-sitter")).default;
    _Parser = Parser;
  }

  if (!_tsLang) {
    const TSLang = (await import("tree-sitter-typescript")).default;
    _tsLang  = TSLang.typescript;
    _tsxLang = TSLang.tsx;
  }
  if (!_jsLang) {
    _jsLang = (await import("tree-sitter-javascript")).default;
  }

  const parser = new _Parser();
  if (lang === "tsx")        parser.setLanguage(_tsxLang);
  else if (lang === "typescript") parser.setLanguage(_tsLang);
  else                       parser.setLanguage(_jsLang);
  return parser;
}

// ── Language detection ────────────────────────────────────────────────────────

export function detectLanguage(
  filePath: string
): "typescript" | "tsx" | "javascript" | "jsx" | null {
  if (filePath.endsWith(".tsx"))  return "tsx";
  if (filePath.endsWith(".ts"))   return "typescript";
  if (filePath.endsWith(".jsx"))  return "jsx";
  if (filePath.endsWith(".js") || filePath.endsWith(".mjs") || filePath.endsWith(".cjs"))
    return "javascript";
  return null;
}

// ── Main entry point ──────────────────────────────────────────────────────────

/**
 * Parse a single source file and return structured extraction results.
 *
 * @param absolutePath  Full filesystem path to the file
 * @param repoRelPath   Repo-relative path (e.g. "src/billing/handler.ts")
 */
export async function runTreeSitterPass(
  absolutePath: string,
  repoRelPath:  string
): Promise<FilePassResult | null> {
  const lang = detectLanguage(absolutePath);
  if (!lang) return null;

  let source: string;
  let stat: fs.Stats;
  try {
    source = fs.readFileSync(absolutePath, "utf8");
    stat   = fs.statSync(absolutePath);
  } catch {
    return null;
  }

  const checksum  = crypto.createHash("sha256").update(source).digest("hex");
  const lineCount = source.split("\n").length;

  const file: ExtractedFile = {
    filePath: repoRelPath,
    language: lang,
    lineCount,
    byteSize: stat.size,
    checksum,
  };

  let parser: any;
  let tree:   any;
  try {
    parser = await getParser(lang);
    tree   = parser.parse(source);
  } catch (e) {
    // Tree-sitter parse failure — return skeleton with file only
    return { file, symbols: [], imports: [], callSites: [], decorators: [] };
  }

  const visitor = new CSTVisitor(source, repoRelPath);
  visitor.visitRoot(tree.rootNode);

  return {
    file,
    symbols:    visitor.symbols,
    imports:    visitor.imports,
    callSites:  visitor.callSites,
    decorators: visitor.decorators,
  };
}

// ── CST visitor ───────────────────────────────────────────────────────────────

class CSTVisitor {
  symbols:    ExtractedSymbol[]   = [];
  imports:    ExtractedImport[]   = [];
  callSites:  ExtractedCallSite[] = [];
  decorators: ExtractedDecorator[] = [];

  private source:   string;
  private filePath: string;
  /** Stack of enclosing class/namespace names for qualified names. */
  private scopeStack: string[] = [];

  constructor(source: string, filePath: string) {
    this.source   = source;
    this.filePath = filePath;
  }

  visitRoot(node: any): void {
    this.visitChildren(node);
  }

  private visitChildren(node: any): void {
    for (let i = 0; i < node.childCount; i++) {
      this.visitNode(node.child(i));
    }
  }

  private visitNode(node: any): void {
    if (!node) return;
    switch (node.type) {
      case "import_statement":
        this.visitImport(node);
        break;
      case "class_declaration":
      case "abstract_class_declaration":
        this.visitClass(node);
        break;
      case "interface_declaration":
        this.visitInterface(node);
        break;
      case "type_alias_declaration":
        this.visitTypeAlias(node);
        break;
      case "function_declaration":
      case "generator_function_declaration":
        this.visitFunctionDecl(node);
        break;
      case "lexical_declaration":
      case "variable_declaration":
        this.visitVariableDecl(node);
        break;
      case "export_statement":
        this.visitExportStatement(node);
        break;
      case "call_expression":
      case "new_expression":
        this.visitCallExpression(node);
        this.visitChildren(node); // recurse into args
        break;
      case "enum_declaration":
        this.visitEnum(node);
        break;
      case "module":
      case "namespace_declaration":
        this.visitNamespace(node);
        break;
      default:
        this.visitChildren(node);
    }
  }

  // ── Imports ───────────────────────────────────────────────────────────────

  private visitImport(node: any): void {
    // import "specifier"   — side-effect
    // import foo from "specifier"
    // import { a, b } from "specifier"
    // import * as ns from "specifier"
    const specNode = this.findChild(node, "string");
    if (!specNode) return;
    const specifier = this.stringValue(specNode);
    const range     = this.nodeRange(node);

    let defaultImport:   string | undefined;
    let namespaceImport: string | undefined;
    const namedImports:  string[] = [];
    let sideEffect = true;

    const importClause = this.findChild(node, "import_clause");
    if (importClause) {
      sideEffect = false;
      for (let i = 0; i < importClause.childCount; i++) {
        const child = importClause.child(i);
        if (child.type === "identifier")              defaultImport   = this.text(child);
        if (child.type === "namespace_import")        namespaceImport = this.identifierIn(child);
        if (child.type === "named_imports")           this.collectNamedImports(child, namedImports);
      }
    }

    this.imports.push({
      specifier,
      namedImports,
      defaultImport,
      namespaceImport,
      sideEffect,
      dynamic: false,
      range,
    });
  }

  private collectNamedImports(namedNode: any, out: string[]): void {
    for (let i = 0; i < namedNode.childCount; i++) {
      const child = namedNode.child(i);
      if (child.type === "import_specifier") {
        // import_specifier: either "name" or "name as alias"
        const id = this.findChild(child, "identifier");
        if (id) out.push(this.text(id));
      }
    }
  }

  // ── Classes ───────────────────────────────────────────────────────────────

  private visitClass(node: any): void {
    const nameNode = this.findChild(node, "type_identifier") ?? this.findChild(node, "identifier");
    const name = nameNode ? this.text(nameNode) : "<anonymous>";
    const qname = this.qualify(name);
    const exported = this.isExported(node);
    const isAbstract = node.type === "abstract_class_declaration";

    const extendsClause = this.findChild(node, "class_heritage");
    const extendsNames: string[] = [];
    const implementsNames: string[] = [];
    if (extendsClause) {
      this.collectHeritageNames(extendsClause, extendsNames, implementsNames);
    }

    const docstring = this.leadingComment(node);

    this.symbols.push({
      qualifiedName: qname,
      name,
      kind:       "class",
      range:      this.nodeRange(node),
      exported,
      isAbstract,
      extends:    extendsNames.length    > 0 ? extendsNames    : undefined,
      implements: implementsNames.length > 0 ? implementsNames : undefined,
      docstring:  docstring ?? undefined,
    });

    // Visit class body members
    const body = this.findChild(node, "class_body");
    if (body) {
      this.scopeStack.push(name);
      this.visitClassBody(body);
      this.scopeStack.pop();
    }
  }

  private visitClassBody(body: any): void {
    for (let i = 0; i < body.childCount; i++) {
      const child = body.child(i);
      switch (child.type) {
        case "method_definition":
        case "abstract_method_signature":
          this.visitMethod(child);
          break;
        case "public_field_definition":
        case "private_field_definition":
          this.visitProperty(child);
          break;
        case "decorator":
          this.visitDecorator(child, "class");
          break;
      }
    }
  }

  // ── Interfaces ────────────────────────────────────────────────────────────

  private visitInterface(node: any): void {
    const nameNode = this.findChild(node, "type_identifier");
    const name  = nameNode ? this.text(nameNode) : "<anon>";
    const qname = this.qualify(name);

    const extendsNames: string[] = [];
    const extendsClause = this.findChild(node, "extends_type_clause");
    if (extendsClause) {
      for (let i = 0; i < extendsClause.childCount; i++) {
        const c = extendsClause.child(i);
        if (c.type === "type_identifier" || c.type === "generic_type") {
          const id = c.type === "generic_type" ? this.findChild(c, "type_identifier") : c;
          if (id) extendsNames.push(this.text(id));
        }
      }
    }

    this.symbols.push({
      qualifiedName: qname,
      name,
      kind:     "interface",
      range:    this.nodeRange(node),
      exported: this.isExported(node),
      extends:  extendsNames.length > 0 ? extendsNames : undefined,
      docstring: this.leadingComment(node) ?? undefined,
    });
  }

  // ── Type aliases ──────────────────────────────────────────────────────────

  private visitTypeAlias(node: any): void {
    const nameNode = this.findChild(node, "type_identifier");
    const name  = nameNode ? this.text(nameNode) : "<anon>";

    this.symbols.push({
      qualifiedName: this.qualify(name),
      name,
      kind:     "type_alias",
      range:    this.nodeRange(node),
      exported: this.isExported(node),
    });
  }

  // ── Functions ─────────────────────────────────────────────────────────────

  private visitFunctionDecl(node: any): void {
    const nameNode = this.findChild(node, "identifier");
    const name  = nameNode ? this.text(nameNode) : "<anon>";

    const params  = this.extractParamNames(node);
    const retType = this.extractReturnType(node);
    const body    = this.findChild(node, "statement_block");
    const bodyHash = body ? this.hashText(this.text(body)) : undefined;

    this.symbols.push({
      qualifiedName: this.qualify(name),
      name,
      kind:       "function",
      range:      this.nodeRange(node),
      exported:   this.isExported(node),
      paramNames: params,
      returnType: retType ?? undefined,
      isAsync:    this.hasModifier(node, "async"),
      bodyHash:   bodyHash ?? undefined,
      docstring:  this.leadingComment(node) ?? undefined,
    });

    // Recurse into function body for call sites
    if (body) this.visitChildren(body);
  }

  // ── Methods ───────────────────────────────────────────────────────────────

  private visitMethod(node: any): void {
    const nameNode = this.findChild(node, "property_identifier")
                  ?? this.findChild(node, "identifier");
    const name  = nameNode ? this.text(nameNode) : "<anon>";
    const qname = this.qualify(name);

    const visibility = this.extractVisibility(node);
    const params     = this.extractParamNames(node);
    const retType    = this.extractReturnType(node);
    const body       = this.findChild(node, "statement_block");
    const bodyHash   = body ? this.hashText(this.text(body)) : undefined;

    this.symbols.push({
      qualifiedName: qname,
      name,
      kind:       "method",
      range:      this.nodeRange(node),
      exported:   false,
      parentName: this.scopeStack[this.scopeStack.length - 1],
      paramNames: params,
      returnType: retType ?? undefined,
      visibility,
      isAsync:    this.hasModifier(node, "async"),
      isStatic:   this.hasModifier(node, "static"),
      isAbstract: this.hasModifier(node, "abstract"),
      bodyHash:   bodyHash ?? undefined,
    });

    // Recurse into method body for call sites
    if (body) {
      const callerName = qname;
      this.visitMethodBody(body, callerName);
    }
  }

  private visitMethodBody(body: any, callerName: string): void {
    for (let i = 0; i < body.childCount; i++) {
      this.collectCallSitesIn(body.child(i), callerName);
    }
  }

  private collectCallSitesIn(node: any, callerName: string): void {
    if (!node) return;
    if (node.type === "call_expression" || node.type === "new_expression") {
      const fnNode = node.child(0);
      if (fnNode) {
        const calleeText = this.text(fnNode).slice(0, 256); // cap length
        this.callSites.push({
          calleeText,
          range: this.nodeRange(node),
          callerName,
        });
      }
    }
    for (let i = 0; i < node.childCount; i++) {
      this.collectCallSitesIn(node.child(i), callerName);
    }
  }

  // ── Properties ────────────────────────────────────────────────────────────

  private visitProperty(node: any): void {
    const nameNode = this.findChild(node, "property_identifier")
                  ?? this.findChild(node, "identifier");
    const name = nameNode ? this.text(nameNode) : "<anon>";

    this.symbols.push({
      qualifiedName: this.qualify(name),
      name,
      kind:       "property",
      range:      this.nodeRange(node),
      exported:   false,
      parentName: this.scopeStack[this.scopeStack.length - 1],
      visibility: this.extractVisibility(node),
      isStatic:   this.hasModifier(node, "static"),
    });
  }

  // ── Variable declarations → Constant ─────────────────────────────────────

  private visitVariableDecl(node: any): void {
    // Only top-level const declarations → Constant nodes
    const isConst = this.findChild(node, "const") !== null ||
                    node.text.startsWith("const ");
    if (!isConst) return;

    for (let i = 0; i < node.childCount; i++) {
      const child = node.child(i);
      if (child.type === "variable_declarator") {
        const nameNode = child.child(0);
        if (!nameNode) continue;
        const name = this.text(nameNode);
        if (!name || name === "{" || name === "[") continue; // skip destructuring

        this.symbols.push({
          qualifiedName: this.qualify(name),
          name,
          kind:     "constant",
          range:    this.nodeRange(child),
          exported: this.isExported(node),
        });
      }
    }

    // Recurse to find any call sites inside initializers
    this.visitChildren(node);
  }

  // ── Export statements ─────────────────────────────────────────────────────

  private visitExportStatement(node: any): void {
    // export { foo, bar } from './mod'  — re-export
    // export default function ...
    // export class ...  (handled by class/function visitors via isExported)
    for (let i = 0; i < node.childCount; i++) {
      this.visitNode(node.child(i));
    }
  }

  // ── Enums ─────────────────────────────────────────────────────────────────

  private visitEnum(node: any): void {
    const nameNode = this.findChild(node, "identifier");
    const name = nameNode ? this.text(nameNode) : "<anon>";

    this.symbols.push({
      qualifiedName: this.qualify(name),
      name,
      kind:     "enum",
      range:    this.nodeRange(node),
      exported: this.isExported(node),
    });

    // Add enum members
    const body = this.findChild(node, "enum_body");
    if (body) {
      this.scopeStack.push(name);
      for (let i = 0; i < body.childCount; i++) {
        const child = body.child(i);
        if (child.type === "enum_assignment" || child.type === "property_identifier" || child.type === "identifier") {
          const memberNameNode = child.type === "enum_assignment"
            ? child.child(0)
            : child;
          if (memberNameNode) {
            const memberName = this.text(memberNameNode);
            this.symbols.push({
              qualifiedName: this.qualify(memberName),
              name:         memberName,
              kind:         "enum_member",
              range:        this.nodeRange(child),
              exported:     false,
              parentName:   name,
            });
          }
        }
      }
      this.scopeStack.pop();
    }
  }

  // ── Namespaces ────────────────────────────────────────────────────────────

  private visitNamespace(node: any): void {
    const nameNode = this.findChild(node, "identifier");
    const name = nameNode ? this.text(nameNode) : "<anon>";

    this.symbols.push({
      qualifiedName: this.qualify(name),
      name,
      kind:     "namespace",
      range:    this.nodeRange(node),
      exported: this.isExported(node),
    });

    const body = this.findChild(node, "statement_block");
    if (body) {
      this.scopeStack.push(name);
      this.visitChildren(body);
      this.scopeStack.pop();
    }
  }

  // ── Call expressions (top-level, outside classes) ─────────────────────────

  private visitCallExpression(node: any): void {
    const fnNode = node.child(0);
    if (!fnNode) return;
    const calleeText = this.text(fnNode).slice(0, 256);
    this.callSites.push({
      calleeText,
      range:      this.nodeRange(node),
      callerName: this.scopeStack.length > 0
        ? this.scopeStack.join(".")
        : undefined,
    });
  }

  // ── Decorators ───────────────────────────────────────────────────────────

  private visitDecorator(node: any, targetKind: "class" | "method" | "property" | "parameter"): void {
    const callNode = this.findChild(node, "call_expression")
                  ?? this.findChild(node, "identifier");
    if (!callNode) return;

    let name = "";
    let argsText: string | undefined;
    if (callNode.type === "call_expression") {
      name     = this.text(callNode.child(0)!);
      const args = this.findChild(callNode, "arguments");
      if (args) argsText = this.text(args).slice(0, 256);
    } else {
      name = this.text(callNode);
    }

    this.decorators.push({
      name,
      argsText,
      targetKind,
      targetName: this.scopeStack.join("."),
      range: this.nodeRange(node),
    });
  }

  // ── Heritage parsing ──────────────────────────────────────────────────────

  private collectHeritageNames(heritage: any, extendsOut: string[], implementsOut: string[]): void {
    let mode: "extends" | "implements" | null = null;
    for (let i = 0; i < heritage.childCount; i++) {
      const child = heritage.child(i);
      if (child.type === "extends")    { mode = "extends";    continue; }
      if (child.type === "implements") { mode = "implements"; continue; }
      if (child.type === "type_identifier" || child.type === "generic_type") {
        const id = child.type === "generic_type" ? this.findChild(child, "type_identifier") : child;
        if (!id) continue;
        const name = this.text(id);
        if (mode === "extends")    extendsOut.push(name);
        if (mode === "implements") implementsOut.push(name);
      }
    }
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  private qualify(name: string): string {
    return this.scopeStack.length > 0
      ? `${this.scopeStack.join(".")}.${name}`
      : name;
  }

  private text(node: any): string {
    if (!node) return "";
    return this.source.slice(node.startIndex, node.endIndex);
  }

  private stringValue(node: any): string {
    const t = this.text(node);
    return t.slice(1, -1); // strip surrounding quotes
  }

  private identifierIn(node: any): string {
    const id = this.findChild(node, "identifier");
    return id ? this.text(id) : "";
  }

  private findChild(node: any, type: string): any | null {
    for (let i = 0; i < node.childCount; i++) {
      if (node.child(i).type === type) return node.child(i);
    }
    return null;
  }

  private nodeRange(node: any): SourceRange {
    return {
      startLine:   node.startPosition.row,
      startColumn: node.startPosition.column,
      startOffset: node.startIndex,
      endLine:     node.endPosition.row,
      endColumn:   node.endPosition.column,
      endOffset:   node.endIndex,
    };
  }

  private isExported(node: any): boolean {
    // Walk up one level — if parent is export_statement, it's exported
    const parent = node.parent;
    if (!parent) return false;
    return parent.type === "export_statement" ||
           parent.type === "export_default_declaration";
  }

  private hasModifier(node: any, modifier: string): boolean {
    for (let i = 0; i < node.childCount; i++) {
      if (node.child(i).type === modifier) return true;
    }
    return false;
  }

  private extractVisibility(node: any): "public" | "protected" | "private" {
    for (let i = 0; i < node.childCount; i++) {
      const t = node.child(i).type;
      if (t === "private")   return "private";
      if (t === "protected") return "protected";
    }
    return "public";
  }

  private extractParamNames(node: any): string[] {
    const params = this.findChild(node, "formal_parameters");
    if (!params) return [];
    const names: string[] = [];
    for (let i = 0; i < params.childCount; i++) {
      const c = params.child(i);
      if (c.type === "required_parameter" || c.type === "optional_parameter") {
        const id = this.findChild(c, "identifier");
        if (id) names.push(this.text(id));
      } else if (c.type === "identifier") {
        names.push(this.text(c));
      }
    }
    return names;
  }

  private extractReturnType(node: any): string | null {
    const ta = this.findChild(node, "type_annotation");
    if (!ta) return null;
    return this.text(ta).replace(/^:\s*/, "").slice(0, 128);
  }

  private leadingComment(node: any): string | null {
    const prev = node.previousSibling;
    if (prev && (prev.type === "comment" || prev.type === "block_comment")) {
      return this.text(prev)
        .replace(/^\/\*\*?/, "").replace(/\*\/$/, "")
        .replace(/^\s*\*\s?/gm, "")
        .trim()
        .slice(0, 512);
    }
    return null;
  }

  private hashText(text: string): string {
    return crypto.createHash("md5").update(text).digest("hex").slice(0, 16);
  }
}
