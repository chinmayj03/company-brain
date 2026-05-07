# ADR-0004: Agent Tool Naming Conventions and Honesty Contract

**Status:** Accepted  
**Date:** 2026-05-03  
**Deciders:** Chinmay  
**Depends on:** ADR-0001 (URN scheme), ADR-0003 (extractor plugin contract)  
**Unblocks:** Phase 2 (tools package, API server)

---

## Context

Company Brain's primary consumers are AI agents, not humans typing into a chat UI. Agents invoke tools programmatically: they select a tool name, supply typed inputs, receive typed outputs, and incorporate the result into their reasoning chain.

The tool surface design must therefore optimize for:
- **Machine-readable naming:** agents pick tools from a list and must understand intent from the name alone
- **Typed contracts:** both input and output must be Zod-validated so agents can compose them safely
- **Honest absence:** agents that receive misleading partial results hallucinate; tools that fail silently are dangerous
- **Citability:** every result must carry enough provenance for the agent to cite or discount it

---

## Decision

### 1. Naming convention

Tool names follow `verb_noun` in `snake_case`. The verb is always present-tense imperative from the agent's perspective:

| Verb | Meaning |
|------|---------|
| `find_` | Search across the graph; may return 0–N results |
| `get_` | Retrieve a known, specific thing; returns 1 or null |
| `list_` | Enumerate a collection under a scope; always returns an array |
| `summarize_` | Return an LLM-written or compressed summary of a node |
| `resolve_` | Cross-reference lookup (e.g. symbol → endpoint) |
| `diff_` | Compare two versions of a node or subgraph |

Examples:
```
find_symbol          find_callers         find_callees
get_function_signature  get_file_summary  get_repo_map
list_files_in        list_endpoints       list_screens_using
summarize_file       summarize_module
resolve_contract     resolve_table
```

### 2. Input schema — every tool

All tool inputs must be Zod-validated. Common fields:

```ts
// Every tool that scopes to a repo requires:
{ scope: z.string() }          // e.g. "acme/web"

// Tools that target a symbol require one of:
{ qualifiedName: z.string() }  // e.g. "BillingService.charge"
{ urn: z.string() }            // e.g. "urn:cb:symbol:acme/web:..."

// Pagination (for list_ tools):
{ limit: z.number().max(100).default(25) }
{ offset: z.number().default(0) }
```

### 3. Output shape — honesty contract

Every tool returns one of two shapes. **There is no third shape.** Tools never return partially-populated results or near-miss guesses.

**Success:**
```ts
{
  result:             T,             // the typed payload
  confidence:         number,        // 0.0–1.0 (from ADR-0005 rubric)
  source_uri:         string,        // the artifact this was derived from
  extracted_at_commit: string,       // git SHA of the extraction run
  extractor:          string,        // "core-ts@0.1.0" etc.
}
```

**Absence (not an error):**
```ts
{
  result:  null,
  reason:  AbsenceReason,
}

type AbsenceReason =
  | "no_match"                  // searched; nothing found
  | "too_many_matches"          // >N candidates; query must be narrowed
  | "no_extractor_for_language" // file exists but no extractor handles it
  | "stale_index"               // node exists but valid_to_commit is set
  | "scope_not_indexed"         // scope was never extracted
  | "ambiguous_symbol"          // name matches multiple symbols; use URN instead
```

**Error (network/DB failure — not a domain absence):**
Propagated as a tRPC error with code `INTERNAL_SERVER_ERROR`. Agents should NOT retry tool calls on errors; they should surface the failure.

### 4. Confidence tagging

See ADR-0005 for the confidence rubric. Tools must pass through the confidence value from the underlying node/edge provenance, not compute their own. When a result combines nodes of different confidence levels, use `Math.min()`.

### 5. No hallucination clause

Tools must never:
- Return a near-miss result as if it matched the query
- Omit `reason` when `result` is null
- Return confidence > 0.5 for `llm_inference_only` derivations in structural queries
- Fabricate `source_uri` or `extracted_at_commit` values

This is enforced at the type level: the TypeScript return type forces either `result: T` (non-null) or `result: null` with a mandatory `reason`.

### 6. Tool versioning

Tool names are versioned via the `@company-brain/tools` package semver. When a tool's output shape changes, `minor` bumps. When a tool is removed, it is deprecated for one `minor` cycle before removal.

---

## Consequences

- All tools are typed end-to-end (Zod input → TypeScript output → tRPC transport)
- Absence is a first-class value, not an exception
- Agents can programmatically inspect `reason` to decide how to proceed
- The honesty contract creates a trust boundary: if a tool returns `result: T`, the agent can cite it; if `result: null`, the agent must reason about absence explicitly
