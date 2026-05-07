/**
 * packages/extractors/core-ts/src/passes/call-edge-pass.ts
 *
 * Pass 2 — Call-edge resolution.
 *
 * After all files have been processed by the tree-sitter pass, we have:
 *   - A map of {qualifiedName → symbolId} for every known symbol
 *   - A list of call sites with callee text + optional caller name
 *
 * This pass tries to resolve calleeText → a known symbol URN, then emits
 * `calls` edges. Unresolved calls (e.g. to npm APIs) are dropped silently —
 * we never emit edges to non-existent nodes.
 *
 * Resolution strategy (in order):
 *   1. Exact match: callee is "SomeClass.method" → look up in symbol table
 *   2. Simple name match: callee is just "doThing" → find all symbols named "doThing"
 *      and emit edges to all candidates (ambiguous, confidence=0.7)
 *   3. Namespace-resolved: callee is "Utils.format" → namespace prefix lookup
 *
 * Call edges carry confidence = 0.85 (static analysis — not type-resolved).
 */

import type { EdgeEnvelope }    from "@company-brain/schema";
import type { ExtractedCallSite } from "../types.js";

export interface CallSiteRecord {
  callerFileUrn:  string;
  callerSymUrn?:  string;    // URN of the function/method containing this call
  calleeText:     string;
  sourceUri:      string;
  startLine:      number;
  commitSha:      string;
}

export interface SymbolTableEntry {
  urn:           string;
  simpleName:    string;
  qualifiedName: string;
  fileUrn:       string;
}

/** Build a symbol table from the accumulated extraction run. */
export function buildSymbolTable(
  scope:       string,
  fileResults: Array<{ filePath: string; symbols: Array<{ qualifiedName: string; name: string }> }>
): Map<string, SymbolTableEntry[]> {
  // Key: simpleName → entries[]  (many symbols may share a simple name)
  const bySimple = new Map<string, SymbolTableEntry[]>();

  for (const file of fileResults) {
    for (const sym of file.symbols) {
      const entry: SymbolTableEntry = {
        urn:           `urn:cb:symbol:${scope}:${file.filePath}:${sym.qualifiedName}`,
        simpleName:    sym.name,
        qualifiedName: sym.qualifiedName,
        fileUrn:       `urn:cb:file:${scope}:${file.filePath}`,
      };
      const existing = bySimple.get(sym.name) ?? [];
      existing.push(entry);
      bySimple.set(sym.name, existing);
    }
  }

  return bySimple;
}

/** Resolve a callee text to zero or more symbol URNs from the symbol table. */
export function resolveCallee(
  calleeText:  string,
  symbolTable: Map<string, SymbolTableEntry[]>,
): { urn: string; confidence: number }[] {
  // Strip trailing call syntax: "foo()" → "foo", "this.bar()" → "this.bar"
  const clean = calleeText
    .replace(/\(.*$/, "")        // remove args
    .replace(/^(this|self)\./, "") // strip "this."
    .trim();

  if (!clean) return [];

  // Try exact qualified name first
  const parts = clean.split(".");
  const lastName = parts[parts.length - 1]!;

  // Exact match by qualified name
  const exactMatches: { urn: string; confidence: number }[] = [];
  const simpleMatches: { urn: string; confidence: number }[] = [];

  const candidates = symbolTable.get(lastName) ?? [];
  for (const entry of candidates) {
    if (entry.qualifiedName === clean) {
      exactMatches.push({ urn: entry.urn, confidence: 0.95 });
    } else {
      simpleMatches.push({ urn: entry.urn, confidence: 0.70 });
    }
  }

  if (exactMatches.length > 0) return exactMatches;
  if (simpleMatches.length <= 3) return simpleMatches; // cap ambiguous matches
  return []; // too ambiguous — skip
}

/** Build `calls` EdgeEnvelopes from call site records + symbol table. */
export function buildCallEdges(
  callSites:   CallSiteRecord[],
  symbolTable: Map<string, SymbolTableEntry[]>,
  extractor:   { name: string; version: string },
): EdgeEnvelope[] {
  const edges: EdgeEnvelope[] = [];

  for (const cs of callSites) {
    const callerUrn = cs.callerSymUrn ?? cs.callerFileUrn;
    const resolved  = resolveCallee(cs.calleeText, symbolTable);

    for (const { urn: calleeUrn, confidence } of resolved) {
      if (calleeUrn === callerUrn) continue; // skip self-calls

      const id = `${callerUrn}>>calls>>${calleeUrn}`;
      edges.push({
        id:          id.slice(0, 511),
        type:        "calls",
        source_id:   callerUrn,
        target_id:   calleeUrn,
        cardinality: "n-n",
        source_uri:  cs.sourceUri,
        extractor,
        derivation:  "static_analysis",
        confidence,
        valid_from_commit: cs.commitSha,
        valid_to_commit:   null,
        attributes: {
          callee_text:  cs.calleeText.slice(0, 128),
          start_line:   cs.startLine,
        },
      });
    }
  }

  // Deduplicate by id
  const seen = new Set<string>();
  return edges.filter(e => {
    if (seen.has(e.id)) return false;
    seen.add(e.id);
    return true;
  });
}
