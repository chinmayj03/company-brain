/**
 * packages/extractors/core-ts/src/__tests__/envelope-builder.test.ts
 *
 * Tests for the pure envelope-builder pass (no tree-sitter, no Neo4j).
 *
 * Covers:
 *   - determinism: same input → identical node/edge sets
 *   - idempotency: all generated node IDs are unique within a file
 *   - provenance: every node has all required provenance fields
 *   - import resolution: relative imports → file URNs
 *   - external imports → ExternalDependency nodes
 */

import { describe, it, expect } from "vitest";
import { buildEnvelopes, resolveImportPath } from "../passes/envelope-builder.js";
import type { FilePassResult } from "../types.js";

// ── Fixture ────────────────────────────────────────────────────────────────────

const EXTRACTOR = { name: "core-ts", version: "0.1.0" };
const SCOPE     = "acme/web";
const COMMIT    = "abc1234567890";
const REPO_ROOT = "/repo";

function makeRange(line: number) {
  return { startLine: line, startColumn: 0, startOffset: 0, endLine: line + 5, endColumn: 0, endOffset: 100 };
}

const FIXTURE: FilePassResult = {
  file: {
    filePath:  "src/billing.ts",
    language:  "typescript",
    lineCount: 60,
    byteSize:  1200,
    checksum:  "deadbeef",
  },
  symbols: [
    { qualifiedName: "BillingService",        name: "BillingService",  kind: "class",    range: makeRange(10), exported: true,  extends: ["EventEmitter"] },
    { qualifiedName: "BillingService.charge", name: "charge",          kind: "method",   range: makeRange(20), exported: false, parentName: "BillingService", isAsync: true },
    { qualifiedName: "BillingService.refund", name: "refund",          kind: "method",   range: makeRange(30), exported: false, parentName: "BillingService" },
    { qualifiedName: "MAX_RETRY_ATTEMPTS",    name: "MAX_RETRY_ATTEMPTS", kind: "constant", range: makeRange(5),  exported: true },
    { qualifiedName: "ChargeRequest",         name: "ChargeRequest",   kind: "interface", range: makeRange(12), exported: true },
  ],
  imports: [
    { specifier: "events",   namedImports: ["EventEmitter"], sideEffect: false, dynamic: false, range: makeRange(1) },
    { specifier: "./types",  namedImports: ["PaymentProvider"], sideEffect: false, dynamic: false, range: makeRange(2) },
  ],
  callSites: [
    { calleeText: "this.provider.processPayment", range: makeRange(22), callerName: "BillingService.charge" },
    { calleeText: "this.emit",                    range: makeRange(21), callerName: "BillingService.charge" },
  ],
  decorators: [],
};

// ── Tests ──────────────────────────────────────────────────────────────────────

describe("buildEnvelopes", () => {
  it("is deterministic: two calls with same input produce identical output", () => {
    const a = buildEnvelopes(FIXTURE, SCOPE, COMMIT, EXTRACTOR, REPO_ROOT);
    const b = buildEnvelopes(FIXTURE, SCOPE, COMMIT, EXTRACTOR, REPO_ROOT);

    const aIds = a.nodes.map(n => n.id).sort();
    const bIds = b.nodes.map(n => n.id).sort();
    expect(aIds).toEqual(bIds);

    const aEdgeIds = a.edges.map(e => e.id).sort();
    const bEdgeIds = b.edges.map(e => e.id).sort();
    expect(aEdgeIds).toEqual(bEdgeIds);
  });

  it("emits a File node with correct type and attributes", () => {
    const { nodes } = buildEnvelopes(FIXTURE, SCOPE, COMMIT, EXTRACTOR, REPO_ROOT);
    const fileNode = nodes.find(n => n.type === "File");
    expect(fileNode).toBeDefined();
    expect(fileNode?.id).toBe("urn:cb:file:acme/web:src/billing.ts");
    expect(fileNode?.attributes["language"]).toBe("typescript");
    expect(fileNode?.attributes["line_count"]).toBe(60);
  });

  it("emits a Directory node for the parent directory", () => {
    const { nodes } = buildEnvelopes(FIXTURE, SCOPE, COMMIT, EXTRACTOR, REPO_ROOT);
    const dirNode = nodes.find(n => n.type === "Directory");
    expect(dirNode).toBeDefined();
    expect(dirNode?.id).toContain("src");
  });

  it("emits Class, Method, Interface, Constant nodes", () => {
    const { nodes } = buildEnvelopes(FIXTURE, SCOPE, COMMIT, EXTRACTOR, REPO_ROOT);
    const types = new Set(nodes.map(n => n.type));
    expect(types.has("Class")).toBe(true);
    expect(types.has("Method")).toBe(true);
    expect(types.has("Interface")).toBe(true);
    expect(types.has("Constant")).toBe(true);
  });

  it("all node IDs are unique within a single file extraction", () => {
    const { nodes } = buildEnvelopes(FIXTURE, SCOPE, COMMIT, EXTRACTOR, REPO_ROOT);
    const ids = nodes.map(n => n.id);
    const unique = new Set(ids);
    expect(unique.size).toBe(ids.length);
  });

  it("all nodes carry required provenance fields", () => {
    const { nodes } = buildEnvelopes(FIXTURE, SCOPE, COMMIT, EXTRACTOR, REPO_ROOT);
    for (const node of nodes) {
      expect(node.extractor.name).toBe("core-ts");
      expect(node.extractor.version).toBe("0.1.0");
      expect(node.confidence).toBeGreaterThan(0);
      expect(node.derivation).toBeTruthy();
      expect(node.valid_from_commit).toBe(COMMIT);
      expect(node.source_uri).toBeTruthy();
      expect(node.source_checksum).toBeTruthy();
    }
  });

  it("emits 'contains' edges from File to each symbol", () => {
    const { edges } = buildEnvelopes(FIXTURE, SCOPE, COMMIT, EXTRACTOR, REPO_ROOT);
    const containsEdges = edges.filter(e => e.type === "contains" && e.source_id.includes(":file:"));
    // At minimum: File contains each of the 5 symbols minus properties
    expect(containsEdges.length).toBeGreaterThanOrEqual(4);
  });

  it("emits 'imports' edge to external package (EventEmitter)", () => {
    const { nodes, edges } = buildEnvelopes(FIXTURE, SCOPE, COMMIT, EXTRACTOR, REPO_ROOT);
    const extDep = nodes.find(n => n.type === "ExternalDependency" && n.name === "events");
    expect(extDep).toBeDefined();
    const importEdge = edges.find(e => e.type === "imports" && e.target_id === extDep?.id);
    expect(importEdge).toBeDefined();
  });

  it("emits 'imports' edge to relative module (./types)", () => {
    const { edges } = buildEnvelopes(FIXTURE, SCOPE, COMMIT, EXTRACTOR, REPO_ROOT);
    const relImport = edges.find(e =>
      e.type === "imports" && e.target_id === "urn:cb:file:acme/web:src/types.ts"
    );
    expect(relImport).toBeDefined();
  });

  it("emits 'extends' edge from BillingService to EventEmitter (unresolved)", () => {
    const { edges } = buildEnvelopes(FIXTURE, SCOPE, COMMIT, EXTRACTOR, REPO_ROOT);
    const extendsEdge = edges.find(e => e.type === "extends");
    expect(extendsEdge).toBeDefined();
    expect(extendsEdge?.attributes["unresolved"]).toBe(true);
  });

  it("BillingService.charge has 'declared_in' edge to BillingService class", () => {
    const { edges } = buildEnvelopes(FIXTURE, SCOPE, COMMIT, EXTRACTOR, REPO_ROOT);
    const chargeUrn = "urn:cb:symbol:acme/web:src/billing.ts:BillingService.charge";
    const classUrn  = "urn:cb:symbol:acme/web:src/billing.ts:BillingService";
    const edge = edges.find(e =>
      e.type === "declared_in" &&
      e.source_id === chargeUrn &&
      e.target_id === classUrn
    );
    expect(edge).toBeDefined();
  });
});

// ── resolveImportPath tests ────────────────────────────────────────────────────

describe("resolveImportPath", () => {
  it("resolves relative import within same directory", () => {
    expect(resolveImportPath("src/billing.ts", "./types")).toBe("src/types.ts");
  });

  it("resolves relative import going up a directory", () => {
    expect(resolveImportPath("src/billing/handler.ts", "../utils")).toBe("src/utils.ts");
  });

  it("returns null for npm package imports", () => {
    expect(resolveImportPath("src/billing.ts", "react")).toBeNull();
    expect(resolveImportPath("src/billing.ts", "@org/pkg")).toBeNull();
  });

  it("handles paths without parent directory", () => {
    const result = resolveImportPath("index.ts", "./billing");
    expect(result).toBe("billing.ts");
  });
});
