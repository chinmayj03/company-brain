# ADR-0005: Confidence Scoring Rubric

**Status:** Accepted  
**Date:** 2026-05-03  
**Deciders:** Chinmay  
**Depends on:** ADR-0003 (extractor contract), ADR-0004 (tool honesty contract)

---

## Context

Every node and edge in the Company Brain graph carries a `confidence` field (0.0–1.0). This field is the primary signal agents use to decide how much to trust a fact. Without a consistent rubric, different extractors would assign arbitrary values and the field would become meaningless.

The rubric must be:
- **Deterministic:** the same extraction method always produces the same confidence class
- **Ordered:** higher confidence strictly means less likely to be wrong
- **Compositional:** when combining facts, the composite confidence is computable (use `Math.min` for AND, no operation defined for OR)
- **Transparent:** agents and humans can read the rubric and understand a confidence value without guessing

---

## Decision

### Primary confidence table

| Confidence | Derivation value      | Source | Example |
|------------|-----------------------|--------|---------|
| **1.00**   | `ast`                 | Deterministic parse from file AST | Function name, class inheritance, import specifier |
| **1.00**   | `lsp`                 | Language server protocol (type resolution) | Go-to-definition resolved cross-file type |
| **0.95**   | `framework_parser`    | Framework-specific heuristic with near-deterministic signal | Next.js route from file path, Prisma model from schema file |
| **0.95**   | `config`              | Read directly from config file | `package.json` dependencies, `.env` variable names |
| **0.95**   | `api`                 | Read from a well-structured API (git remote, GitHub API) | Commit SHA, branch name, PR number |
| **0.85**   | `static_analysis`     | Inferred from code patterns without full type resolution | Call-edge from text matching, import-path resolution |
| **0.70**   | `llm_with_evidence`   | LLM reasoning + explicit citation to source text | "This function handles billing" anchored to a JSDoc comment |
| **0.50**   | `llm_inference_only`  | LLM reasoning without grounding | "This file probably handles auth" with no anchoring evidence |
| **1.00**   | `human`               | Manually authored by a developer | NarrativeNote typed by a developer, manual tag |

### Composite confidence

When a query result depends on a chain of facts:

```ts
// AND: all facts must be correct — use minimum
confidence = Math.min(c1, c2, c3, ...);

// Example: find_callers traverses a `calls` edge (0.85) to reach
// a Function node (1.0). The result confidence = min(0.85, 1.0) = 0.85.
```

### Staleness discount

Facts become less reliable as the codebase drifts from the extraction commit. Tools apply a staleness multiplier when `valid_to_commit` is set or when the node was last extracted more than N commits ago:

```
staleness_factor = 0.95^(commits_since_extraction / 10)
```

This is advisory. Agents should treat `valid_to_commit IS NOT NULL` as a hard signal that the fact may no longer be true.

### Confidence floor by tool category

| Tool category | Min confidence to surface | Reason |
|---------------|---------------------------|--------|
| Structural (find_symbol, find_callers) | 0.70 | Lower would be noise |
| Contract (get_contract_for_endpoint) | 0.85 | Contract data shapes API consumers |
| Narrative (summarize_*) | 0.50 | Summaries are inherently LLM-generated |
| Drift signals | 0.70 | Below this, drift is speculative |

### Special values

- **0.0** — reserved for "we detected this but have no confidence it's correct" (e.g. a syntactically broken file was partially parsed)
- **-1.0** — invalid; any node with confidence outside [0, 1] is rejected by `NodeEnvelopeSchema`

---

## Consequences

- Every extractor MUST set `confidence` using one of the values above — no arbitrary floats
- The `derivation` field is the human-readable key; `confidence` is the numeric sort key
- Tools floor their results at the category minimum from the table above
- When agents receive confidence < 0.70, they should note the uncertainty in their response
- LLM-authored NarrativeNotes start at 0.70 (`llm_with_evidence`) and are discounted by staleness over time
