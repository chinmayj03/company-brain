# ADR-0007 — Drift Detection v1

**Status:** Accepted  
**Date:** 2026-05-03  
**Deciders:** Company Brain core team  
**Supersedes:** —  
**Related:** ADR-0005 (confidence rubric), ADR-0006 (framework extractor contract)

---

## Context

A common silent failure mode in API-driven products: the *implementation* of an endpoint diverges from its *contract* (OpenAPI spec). This happens gradually — a field gets renamed in the handler, a new required field is added to the spec but the handler still returns the old shape, a response code is removed from the spec but the handler still emits it.

Company Brain already stores both sides:
- `APIRoute` / `Function` nodes (from `FrameworkNextExtractor` + `CoreTsExtractor`) carry inferred response shapes
- `ContractEndpoint` / `ContractResponseSchema` nodes (from `FrameworkOpenApiExtractor`) carry declared response schemas

A post-extraction pass can compare these and surface discrepancies as first-class `DriftSignal` nodes — queryable, diffable over commits, and surfaceable by agent tools.

---

## Decision

### 1 — DriftSignal node

`DriftSignal` is a new node type in `schema.yaml`:

```yaml
- name: DriftSignal
  description: >
    Detected divergence between an implementation node and a contract node.
  attributes:
    severity:
      type: '"breaking" | "warning" | "info"'
      description: >
        breaking = field missing or type incompatible; warning = extra field or nullable mismatch;
        info = documentation-only discrepancy
    description:
      type: string
      description: Human-readable summary of the divergence
    implementation_urn:
      type: string
      description: URN of the implementation node (APIRoute or Function)
    contract_urn:
      type: string
      description: URN of the contract node (ContractEndpoint or ContractResponseSchema)
    detected_fields:
      type: "string[]"
      description: Field names involved in the drift
```

URN pattern: `urn:cb:drift:<scope>:signals/<implementationUrn-hash>`

### 2 — Edge types

Two new edges:

| Edge | From | To | Meaning |
|---|---|---|---|
| `signals_drift` | DriftSignal | APIRoute or Function | The signal was raised against this implementation |
| `signals_drift` | DriftSignal | ContractEndpoint | The signal was raised against this contract |

### 3 — Severity classification

| Severity | Condition |
|---|---|
| `breaking` | A field declared as required in the contract schema is absent in the inferred response shape; OR a field's inferred type is structurally incompatible (e.g., contract says `string`, implementation returns `number`) |
| `warning` | A field present in the implementation is not declared in the contract (extra field); OR a field is nullable in implementation but non-nullable in contract |
| `info` | Contract has `description`/`example` metadata that cannot be verified statically; OR enum values in implementation differ from contract enum |

### 4 — Matching algorithm (v1)

Phase 1 uses a **name-based structural comparison** (no runtime inspection):

1. For each `(APIRoute)-[:implemented_by]->(Function)` pair in the graph:
   a. Look up `ContractEndpoint` where `path ≈ route.path AND method = route.method`
   b. If no contract endpoint: skip (no contract to drift against)
   c. For each `ContractResponseSchema` with `status_code = "200"` linked to the contract endpoint:
      - Extract `declared_fields` from `ContractResponseSchema.schema_json` (top-level object properties)
      - Extract `inferred_fields` from `Function` node's `return_type` attribute (parsed from TypeScript)
      - Compute symmetric diff; classify by severity rules above
      - If any diff exists: emit a `DriftSignal` node + two `signals_drift` edges

2. Before emitting, call `graph.invalidateByPrefix("urn:cb:drift:<scope>:", currentSha)` to clear stale signals from the previous run.

### 5 — Confidence

All `DriftSignal` nodes are emitted with `confidence: 0.70` (`derivation: "static_analysis"`).

Rationale: both sides of the comparison are themselves inferred (return types from tree-sitter, schema from OpenAPI YAML parsing). Runtime behavior may differ. The 0.70 floor reflects this.

### 6 — Incremental behaviour

The drift detector runs as a full pass after all other extractors complete — it does not have a per-file dirty set. On each run it:
1. Invalidates all existing `DriftSignal` nodes for the scope
2. Re-computes the full set of signals from current graph state
3. Writes the new signal set

This is safe because `DriftSignal` nodes are cheap to regenerate (pure graph reads + compare) and do not carry user-authored data.

### 7 — Limitations (v1)

- Only compares HTTP 200 response shapes (2xx variants, error shapes: deferred to v2)
- No generic/template type resolution in TypeScript return types
- Path matching is exact after normalization; path aliases not resolved
- No support for `allOf` / `anyOf` / `oneOf` composition in OpenAPI schemas (treated as opaque)
- No runtime sampling — purely static

These limitations are acceptable for v1; the goal is surfacing obvious structural drift, not replacing a contract testing framework.

---

## Consequences

**Good:**
- Drift signals are queryable: `get_drift_signals(scope, severity="breaking")` gives immediate triage list
- Signals are versioned: comparing signal sets between commits reveals when drift was introduced
- Pure static analysis — no test infrastructure or runtime required

**Bad:**
- v1 false-positive rate may be high for codebases with polymorphic returns or heavy generics
- Re-running full pass on large graphs adds latency to the extraction pipeline (mitigated by async post-step)

**Neutral:**
- Agent tools can filter by severity; "breaking" signals are high-confidence enough for automated PR comments; "info" signals are advisory only
