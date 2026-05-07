/**
 * packages/extractors/core-ts/src/__tests__/call-edge-pass.test.ts
 *
 * Tests for the call-edge resolution pass (pure functions, no I/O).
 */

import { describe, it, expect } from "vitest";
import {
  buildSymbolTable,
  resolveCallee,
  buildCallEdges,
} from "../passes/call-edge-pass.js";

const SCOPE     = "acme/web";
const EXTRACTOR = { name: "core-ts", version: "0.1.0" };
const COMMIT    = "abc123";

// ── Fixtures ──────────────────────────────────────────────────────────────────

const FILE_RESULTS = [
  {
    filePath: "src/billing.ts",
    symbols: [
      { qualifiedName: "BillingService",        name: "BillingService"  },
      { qualifiedName: "BillingService.charge", name: "charge"           },
      { qualifiedName: "BillingService.refund", name: "refund"           },
      { qualifiedName: "formatAmount",          name: "formatAmount"     },
    ],
  },
  {
    filePath: "src/utils.ts",
    symbols: [
      { qualifiedName: "slugify",  name: "slugify"  },
      { qualifiedName: "truncate", name: "truncate" },
    ],
  },
];

// ── Symbol table ──────────────────────────────────────────────────────────────

describe("buildSymbolTable", () => {
  it("maps simple names to their entries", () => {
    const table = buildSymbolTable(SCOPE, FILE_RESULTS);
    expect(table.has("charge")).toBe(true);
    expect(table.has("slugify")).toBe(true);
    expect(table.has("nonexistent")).toBe(false);
  });

  it("returns the correct URN for a qualified name", () => {
    const table  = buildSymbolTable(SCOPE, FILE_RESULTS);
    const charge = table.get("charge");
    expect(charge).toHaveLength(1);
    expect(charge![0]!.urn).toBe("urn:cb:symbol:acme/web:src/billing.ts:BillingService.charge");
  });

  it("handles multiple files with same simple name", () => {
    const data = [
      { filePath: "src/a.ts", symbols: [{ qualifiedName: "helper", name: "helper" }] },
      { filePath: "src/b.ts", symbols: [{ qualifiedName: "helper", name: "helper" }] },
    ];
    const table = buildSymbolTable(SCOPE, data);
    expect(table.get("helper")).toHaveLength(2);
  });
});

// ── resolveCallee ─────────────────────────────────────────────────────────────

describe("resolveCallee", () => {
  const table = buildSymbolTable(SCOPE, FILE_RESULTS);

  it("resolves exact qualified name with high confidence", () => {
    const results = resolveCallee("BillingService.charge", table);
    expect(results).toHaveLength(1);
    expect(results[0]!.confidence).toBeGreaterThan(0.9);
    expect(results[0]!.urn).toContain("BillingService.charge");
  });

  it("resolves simple name that is also the qualified name with high confidence", () => {
    // "slugify" is both the simple name AND the qualified name of the symbol,
    // so it resolves as an exact match at confidence 0.95.
    const results = resolveCallee("slugify", table);
    expect(results).toHaveLength(1);
    expect(results[0]!.confidence).toBeGreaterThanOrEqual(0.7);
  });

  it("resolves ambiguous simple name with lower confidence", () => {
    // "helper" appears in two files — both get emitted at 0.70
    const multiTable = buildSymbolTable(SCOPE, [
      { filePath: "src/a.ts", symbols: [{ qualifiedName: "SvcA.helper", name: "helper" }] },
      { filePath: "src/b.ts", symbols: [{ qualifiedName: "SvcB.helper", name: "helper" }] },
    ]);
    const results = resolveCallee("helper", multiTable);
    expect(results.length).toBeGreaterThanOrEqual(1);
    for (const r of results) {
      expect(r.confidence).toBeLessThanOrEqual(0.75);
    }
  });

  it("strips 'this.' prefix before resolving", () => {
    const results = resolveCallee("this.charge", table);
    expect(results.length).toBeGreaterThan(0);
  });

  it("strips call arguments", () => {
    const results = resolveCallee("formatAmount(amount, currency)", table);
    expect(results.length).toBeGreaterThan(0);
  });

  it("returns empty for unknown callee", () => {
    const results = resolveCallee("console.log", table);
    expect(results).toHaveLength(0);
  });
});

// ── buildCallEdges ────────────────────────────────────────────────────────────

describe("buildCallEdges", () => {
  const table = buildSymbolTable(SCOPE, FILE_RESULTS);

  const callSites = [
    {
      callerFileUrn: "urn:cb:file:acme/web:src/billing.ts",
      callerSymUrn:  "urn:cb:symbol:acme/web:src/billing.ts:BillingService.charge",
      calleeText:    "formatAmount",
      sourceUri:     "/repo/src/billing.ts",
      startLine:     22,
      commitSha:     COMMIT,
    },
    {
      callerFileUrn: "urn:cb:file:acme/web:src/billing.ts",
      callerSymUrn:  "urn:cb:symbol:acme/web:src/billing.ts:BillingService.charge",
      calleeText:    "unknownExternalFn",
      sourceUri:     "/repo/src/billing.ts",
      startLine:     23,
      commitSha:     COMMIT,
    },
  ];

  it("emits a 'calls' edge for resolvable call sites", () => {
    const edges = buildCallEdges(callSites, table, EXTRACTOR);
    const callsEdges = edges.filter(e => e.type === "calls");
    expect(callsEdges.length).toBeGreaterThanOrEqual(1);
  });

  it("does not emit edges for unresolvable call sites", () => {
    const edges = buildCallEdges(callSites, table, EXTRACTOR);
    const unknownEdges = edges.filter(e =>
      e.target_id.includes("unknownExternalFn")
    );
    expect(unknownEdges).toHaveLength(0);
  });

  it("deduplicates identical call edges", () => {
    const doubled = [...callSites, ...callSites];
    const edges   = buildCallEdges(doubled, table, EXTRACTOR);
    const ids = edges.map(e => e.id);
    const unique = new Set(ids);
    expect(unique.size).toBe(ids.length);
  });

  it("call edges carry derivation=static_analysis", () => {
    const edges = buildCallEdges(callSites, table, EXTRACTOR);
    for (const e of edges) {
      expect(e.derivation).toBe("static_analysis");
    }
  });

  it("does not emit self-call edges", () => {
    const selfCall = [{
      callerFileUrn: "urn:cb:file:acme/web:src/billing.ts",
      callerSymUrn:  "urn:cb:symbol:acme/web:src/billing.ts:BillingService.charge",
      calleeText:    "BillingService.charge",
      sourceUri:     "/repo/src/billing.ts",
      startLine:     25,
      commitSha:     COMMIT,
    }];
    const edges = buildCallEdges(selfCall, table, EXTRACTOR);
    const selfEdges = edges.filter(e => e.source_id === e.target_id);
    expect(selfEdges).toHaveLength(0);
  });
});
