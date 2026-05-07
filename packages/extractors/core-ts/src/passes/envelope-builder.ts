/**
 * packages/extractors/core-ts/src/passes/envelope-builder.ts
 *
 * Converts FilePassResult (raw CST extraction) into NodeEnvelope + EdgeEnvelope
 * arrays ready to be written to Neo4j via GraphClient.
 *
 * Each function here is pure (no I/O, no DB) — makes it easy to unit test.
 */

import crypto from "crypto";
import { Urn, buildUrn } from "@company-brain/schema";
import type { NodeEnvelope, EdgeEnvelope } from "@company-brain/schema";
import type {
  FilePassResult,
  ExtractedSymbol,
  ExtractedImport,
  WriteBatch,
} from "../types.js";

// ── Extractor ref ─────────────────────────────────────────────────────────────

export interface ExtractorRef {
  name:    string;
  version: string;
}

// ── Build envelopes from a single file's pass result ─────────────────────────

export function buildEnvelopes(
  result:     FilePassResult,
  scope:      string,
  commitSha:  string,
  extractor:  ExtractorRef,
  repoRoot:   string,
): WriteBatch {
  const nodes: NodeEnvelope[] = [];
  const edges: EdgeEnvelope[] = [];
  const now = new Date().toISOString();
  const sourceBase = `${repoRoot}/${result.file.filePath}`;

  // ── File node ─────────────────────────────────────────────────────────────

  const fileId = Urn.file(scope, result.file.filePath);
  const fileNode: NodeEnvelope = {
    id:           fileId,
    type:         "File",
    name:         result.file.filePath.split("/").pop() ?? result.file.filePath,
    qualified_name: result.file.filePath,
    source_uri:   sourceBase,
    source_checksum: result.file.checksum,
    extractor,
    extraction_timestamp: now,
    confidence:   1.0,
    derivation:   "ast",
    created_at_commit:    commitSha,
    last_modified_commit: commitSha,
    valid_from_commit:    commitSha,
    valid_to_commit:      null,
    status:       "active",
    attributes: {
      language:   result.file.language,
      line_count: result.file.lineCount,
      byte_size:  result.file.byteSize,
    },
  };
  nodes.push(fileNode);

  // ── Directory nodes + file→dir edges ──────────────────────────────────────

  const dirPath = parentDir(result.file.filePath);
  if (dirPath) {
    const dirId = buildUrn({ source: "file", scope, artifact: dirPath });
    const dirNode: NodeEnvelope = {
      id:           dirId,
      type:         "Directory",
      name:         dirPath.split("/").pop() ?? dirPath,
      qualified_name: dirPath,
      source_uri:   `${repoRoot}/${dirPath}`,
      source_checksum: md5(dirPath),
      extractor,
      extraction_timestamp: now,
      confidence:   1.0,
      derivation:   "ast",
      created_at_commit:    commitSha,
      last_modified_commit: commitSha,
      valid_from_commit:    commitSha,
      valid_to_commit:      null,
      status:       "active",
      attributes:   {},
    };
    nodes.push(dirNode);
    edges.push(makeEdge("contains", dirId, fileId, "1-n", sourceBase, commitSha, extractor));
  }

  // ── Symbol nodes ──────────────────────────────────────────────────────────

  for (const sym of result.symbols) {
    const symId = Urn.symbol(scope, result.file.filePath, sym.qualifiedName);

    const nodeType = kindToNodeType(sym.kind);
    if (!nodeType) continue; // skip unknown kinds

    const symNode: NodeEnvelope = {
      id:           symId,
      type:         nodeType,
      name:         sym.name,
      qualified_name: sym.qualifiedName,
      source_uri:   sourceBase,
      source_range: {
        start: { line: sym.range.startLine, column: sym.range.startColumn, offset: sym.range.startOffset },
        end:   { line: sym.range.endLine,   column: sym.range.endColumn,   offset: sym.range.endOffset },
      },
      source_checksum: md5(sym.qualifiedName + sym.range.startLine),
      extractor,
      extraction_timestamp: now,
      confidence:   1.0,
      derivation:   "ast",
      created_at_commit:    commitSha,
      last_modified_commit: commitSha,
      valid_from_commit:    commitSha,
      valid_to_commit:      null,
      status:       "active",
      attributes: buildSymbolAttrs(sym),
    };
    nodes.push(symNode);

    // file CONTAINS symbol
    edges.push(makeEdge("contains", fileId, symId, "1-n", sourceBase, commitSha, extractor));

    // symbol DECLARED_IN file
    edges.push(makeEdge("declared_in", symId, fileId, "n-1", sourceBase, commitSha, extractor));

    // method/property DECLARED_IN parent class
    if (sym.parentName) {
      const parentId = Urn.symbol(scope, result.file.filePath, sym.parentName);
      edges.push(makeEdge("declared_in", symId, parentId, "n-1", sourceBase, commitSha, extractor));
      edges.push(makeEdge("contains", parentId, symId, "1-n", sourceBase, commitSha, extractor));
    }

    // EXTENDS edges
    for (const ext of sym.extends ?? []) {
      // We don't have the target's full URN here (it might be in another file).
      // Emit a placeholder edge attribute — the bridge extractor will resolve it.
      // For now, emit edges to locally-known symbols only.
      const targetId = Urn.symbol(scope, result.file.filePath, ext);
      edges.push(makeEdge("extends", symId, targetId, "n-1", sourceBase, commitSha, extractor, { unresolved: true }));
    }

    // IMPLEMENTS edges
    for (const impl of sym.implements ?? []) {
      const targetId = Urn.symbol(scope, result.file.filePath, impl);
      edges.push(makeEdge("implements", symId, targetId, "n-1", sourceBase, commitSha, extractor, { unresolved: true }));
    }
  }

  // ── Import edges ──────────────────────────────────────────────────────────

  for (const imp of result.imports) {
    if (imp.sideEffect) continue; // side-effect imports don't produce graph edges

    const resolvedPath = resolveImportPath(result.file.filePath, imp.specifier);

    if (resolvedPath) {
      // Intra-repo import
      const targetFileId = Urn.file(scope, resolvedPath);
      edges.push(makeEdge("imports", fileId, targetFileId, "n-n", sourceBase, commitSha, extractor, {
        specifier:       imp.specifier,
        named_imports:   imp.namedImports.join(","),
        default_import:  imp.defaultImport,
        namespace_import: imp.namespaceImport,
      }));
    } else if (!imp.specifier.startsWith(".")) {
      // External package import — create/reference ExternalDependency node
      const pkgName = imp.specifier.startsWith("@")
        ? imp.specifier.split("/").slice(0, 2).join("/")  // scoped: @org/pkg
        : imp.specifier.split("/")[0]!;                    // regular: pkg or pkg/sub

      const extDepId = buildUrn({ source: "file", scope, artifact: `node_modules/${pkgName}` });
      // ExternalDependency node — upserted with low confidence (we only know it's used, not its version here)
      const extDepNode: NodeEnvelope = {
        id:           extDepId,
        type:         "ExternalDependency",
        name:         pkgName,
        qualified_name: pkgName,
        source_uri:   `https://www.npmjs.com/package/${pkgName}`,
        source_checksum: md5(pkgName),
        extractor,
        extraction_timestamp: now,
        confidence:   0.85,
        derivation:   "static_analysis",
        created_at_commit:    commitSha,
        last_modified_commit: commitSha,
        valid_from_commit:    commitSha,
        valid_to_commit:      null,
        status:       "active",
        attributes:   { package_name: pkgName },
      };
      nodes.push(extDepNode);
      edges.push(makeEdge("imports", fileId, extDepId, "n-n", sourceBase, commitSha, extractor, {
        specifier: imp.specifier,
      }));
    }
  }

  return { nodes, edges };
}

// ── Import path resolver ──────────────────────────────────────────────────────

/**
 * Resolve a relative import specifier to a repo-relative file path.
 * Returns null for non-relative (npm package) imports.
 *
 * e.g. resolveImportPath("src/billing/handler.ts", "./utils") → "src/billing/utils.ts"
 */
export function resolveImportPath(
  fromFile:   string,
  specifier:  string
): string | null {
  if (!specifier.startsWith(".")) return null; // npm package

  const fromDir = fromFile.split("/").slice(0, -1).join("/");
  const joined  = fromDir ? `${fromDir}/${specifier}` : specifier;
  const normalized = normalizePath(joined);

  // Try common extensions in order
  const EXTS = [".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx", "/index.js"];

  // If specifier already has an extension, use as-is
  if (/\.[a-z]+$/.test(specifier)) return normalized;

  // Otherwise try extensions (we return the first candidate — actual existence
  // check happens when the extractor reads the file system)
  return normalized + ".ts"; // optimistic: TypeScript is the common case
}

function normalizePath(p: string): string {
  const parts = p.split("/");
  const out: string[] = [];
  for (const part of parts) {
    if (part === "..") { out.pop(); }
    else if (part !== ".") { out.push(part); }
  }
  return out.join("/");
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function kindToNodeType(kind: ExtractedSymbol["kind"]): string | null {
  const MAP: Record<ExtractedSymbol["kind"], string | null> = {
    class:       "Class",
    interface:   "Interface",
    type_alias:  "TypeAlias",
    function:    "Function",
    method:      "Method",
    property:    null,         // skip — too noisy at Phase 1
    constant:    "Constant",
    decorator:   "Decorator",
    enum:        "TypeAlias",  // map to TypeAlias until Enum node type is added
    enum_member: null,         // skip members for now
    namespace:   "Module",
  };
  return MAP[kind] ?? null;
}

function buildSymbolAttrs(sym: ExtractedSymbol): Record<string, unknown> {
  const a: Record<string, unknown> = {};
  if (sym.exported   !== undefined) a["exported"]   = sym.exported;
  if (sym.isAsync    !== undefined) a["is_async"]    = sym.isAsync;
  if (sym.isStatic   !== undefined) a["is_static"]   = sym.isStatic;
  if (sym.isAbstract !== undefined) a["is_abstract"] = sym.isAbstract;
  if (sym.visibility !== undefined) a["visibility"]  = sym.visibility;
  if (sym.paramNames !== undefined) a["param_names"] = sym.paramNames.join(",");
  if (sym.returnType !== undefined) a["return_type"] = sym.returnType;
  if (sym.bodyHash   !== undefined) a["body_hash"]   = sym.bodyHash;
  if (sym.docstring  !== undefined) a["docstring"]   = sym.docstring;
  if (sym.extends    !== undefined) a["extends"]     = sym.extends.join(",");
  if (sym.implements !== undefined) a["implements"]  = sym.implements.join(",");
  return a;
}

function makeEdge(
  type:       string,
  sourceId:   string,
  targetId:   string,
  cardinality: "1-1" | "1-n" | "n-1" | "n-n",
  sourceUri:  string,
  commitSha:  string,
  extractor:  ExtractorRef,
  extra:      Record<string, unknown> = {},
): EdgeEnvelope {
  const id = `${sourceId}>>${type}>>${targetId}`;
  return {
    id:          id.slice(0, 511), // URN max 512 chars
    type:        type as any,
    source_id:   sourceId,
    target_id:   targetId,
    cardinality,
    source_uri:  sourceUri,
    extractor,
    derivation:  "ast",
    confidence:  1.0,
    valid_from_commit: commitSha,
    valid_to_commit:   null,
    attributes:  extra,
  };
}

function parentDir(filePath: string): string | null {
  const parts = filePath.split("/");
  if (parts.length <= 1) return null;
  return parts.slice(0, -1).join("/");
}

function md5(s: string): string {
  return crypto.createHash("md5").update(s).digest("hex");
}
