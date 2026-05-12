# ADR-0061 — Iterative Exploration + Additional Claude-Code Patterns the Brain Lacks

**Status:** Proposed
**Date:** 2026-05-11
**Builds on:** ADR-0055 (cross-file pass), ADR-0056 (verifier), ADR-0057 (universal extraction), ADR-0058 (schema), ADR-0059 (temporal/domain), ADR-0060 (BC v2)
**Sequenced with:** the six-ADR set; this one ships LAST because it composes the rest.

---

## Context

The benchmark + learnings.md isolated 10 architectural failure modes. The other five ADRs in this set fix specific ones. This ADR catches the **remaining behaviours Claude Code does that the brain doesn't**, grouped into six smaller patterns that share a common architectural insight: **the brain extracts once, then becomes static. Claude Code re-explores constantly.**

The eleven Claude Code patterns the brain still lacks even after 0055–0060 land:

| # | Claude Code does | Brain currently does | This ADR's fix |
|---|---|---|---|
| 1 | Iterative tool-use loop (Glob → Grep → Read → reason) | Single-pass extraction | E1 — exploration agent for hard queries |
| 2 | Re-reads to verify mid-task | Trusts first read | E2 — re-read on confidence-flag |
| 3 | Speculative tool use ("let me check if there's a config") | No speculation | E1 — covered |
| 4 | Reads tests as spec | Treats tests as code | covered by ADR-0057 (BehavioralSpec) |
| 5 | Follows error stack traces | Has THROWS/CATCHES edges but no traversal tool | E3 — `trace_exception` MCP tool |
| 6 | Diff-aware reasoning ("this changed last week") | Has commit metadata, no diff query | E4 — `diff_since` MCP tool |
| 7 | Speaks domain language ("Payer" not "Class") | Uses entity types | covered by ADR-0059 (DomainEntity) |
| 8 | Asks user for clarification when ambiguous | Answers wrong question silently | E5 — clarification round-trip |
| 9 | Uses pattern recognition across repos | Per-repo only | E6 — cross-repo similarity surfacing |
| 10 | Synthesizes across docs + code together | Code-first; docs ignored at query time | covered by ADR-0057 + this ADR's E7 |
| 11 | Multi-modal (reads diagrams) | Text-only | E7 — vision sidecar (light) |

Patterns 4, 7, 10 are addressed by other ADRs. The remaining seven (E1–E7 above) are this ADR's scope.

---

## Decision

Seven small additions, each ~half day to one day of work. Each shippable independently if needed.

### E1 — Exploration Agent for hard queries

When `/query` receives a question SmartZoneAssembler can't answer with high confidence (zone is sparse OR initial Sonnet response has confidence < 0.6), spawn an **ExplorationAgent** sub-agent with tool access:

- `glob_files(pattern)` — find files by name pattern
- `grep_code(regex, scope)` — search content
- `read_file(path, line_range)` — read source
- `query_brain(sub_question)` — recursive query
- `list_callers(method_urn)` — graph traversal
- `read_git_blame(file)` — temporal data

The agent runs up to 8 tool calls (cost-capped), then synthesizes a final answer with citations. This is what Claude Code does when its initial context is insufficient.

Cost: ~$0.01 per "hard query". Triggered automatically; not user-visible (no interactive loop).

### E2 — Re-read on confidence-flag

If the initial Sonnet answer cites an entity with `confidence < 0.7`, the query path re-fetches that entity's source from disk and re-runs the answer with the higher-fidelity content. Cheap retry.

### E3 — `trace_exception` MCP tool

Given an exception type or class name, the tool walks THROWS edges backwards to find every method that throws it, and CATCHES edges to find handlers. Returns a tree:

```
trace_exception("DatabaseOperationException")
  → thrown_by: [JpaQueryExecutor.execute, JooqExecutor.fetch]
  → caught_by:
      [JooqExceptionTranslator.translate (file:line) — wraps in DatabaseOperationException]
      [GlobalExceptionHandler.handle (file:line) — returns 500 + alerts ops]
  → unhandled_at:
      [CompetitivenessController.getMetrics — propagates]
```

### E4 — `diff_since(date_or_commit)` MCP tool

Returns entities that have been modified since a given date or commit. Uses git log + entity file_path mappings. Useful for code review, incident post-mortems, and "what changed last week".

### E5 — Clarification round-trip on ambiguous queries

If the user asks "rename the lob column" without specifying WHICH lob (the JSON key or the DB column or both), the query path returns a structured clarification request:

```json
{
  "ambiguity": true,
  "interpretations": [
    {"id": "json_key", "description": "JSON property `lob` in API requests/responses"},
    {"id": "db_column", "description": "Database column `lob` in plan_info, comp_providers"},
    {"id": "both", "description": "Both — atomic rename across stack"}
  ],
  "suggested_followup": "/query --interpret=both 'rename the lob column'"
}
```

UI clients (VS Code extension, web) render this as quick-pick chips.

### E6 — Cross-repo similarity surfacing

When extracting repo A finds a `Pattern` (from ADR-0055), check Qdrant for similar patterns in OTHER workspaces. If a 90%+ match exists in repo B, attach a `SimilarTo` edge:

> *"This `getPayerCompetitors` method is structurally similar to `findActiveCustomers` in `acme-billing-service` (your other repo). That method had a known N+1 issue in 2024-Q4 — worth checking here."*

Surfaces in `/query` responses as `cross_repo_insights: [...]`.

### E7 — Vision sidecar for diagrams in docs/

Already proposed in ADR-0052 P6 as "image_extractor". Promote to scoped sub-feature here: only `docs/**/*.{png,svg}` get extracted; output is `Diagram { components, edges }` with REPRESENTS edges to DomainEntity (from ADR-0059). When `/query` asks "show the architecture", the diagram entity becomes a citable source alongside code.

---

## File ownership for THIS PR (parallel-safe)

```
company-brain-ai/src/companybrain/agents/exploration_agent.py            # NEW — E1
company-brain-ai/src/companybrain/api/routes/query_reread.py              # NEW — E2 wrapper
company-brain-ai/src/companybrain/mcp/tools/trace_exception.py            # NEW — E3
company-brain-ai/src/companybrain/mcp/tools/diff_since.py                 # NEW — E4
company-brain-ai/src/companybrain/api/routes/clarification.py             # NEW — E5
company-brain-ai/src/companybrain/retrieval/cross_repo_similarity.py      # NEW — E6
company-brain-ai/src/companybrain/extractors/diagram_extractor.py         # NEW — E7
tests/unit/test_iterative_patterns.py                                       # NEW
tests/acceptance/test_e1_through_e7.py                                      # NEW
```

Append-only edits to:

```
company-brain-ai/src/companybrain/api/routes/query.py    # invoke E1 on low-confidence answers
company-brain-ai/src/companybrain/mcp/server.py          # register E3 + E4 tools
company-brain-ai/src/companybrain/models/entities.py     # add Diagram, ClarificationOption, SimilarTo edge
```

Does NOT touch any file owned by ADR-0055/56/57/58/59/60.

---

## Acceptance test (one per E*)

```python
async def test_e1_exploration_agent_fires_on_hard_query():
    # Ask a question that requires multi-file exploration; assert agent fired
    response = await query("which 4 places in the codebase use a literal 'lob' instead of the constant?")
    assert response.telemetry["exploration_agent_invoked"] is True
    assert len(response.affected_entities) >= 4


async def test_e3_trace_exception_walks_throws_catches():
    tree = await mcp_call("trace_exception", {"name": "DatabaseOperationException"})
    assert "thrown_by" in tree
    assert "caught_by" in tree
    assert len(tree["thrown_by"]) >= 1


async def test_e4_diff_since_returns_recent_changes():
    entities = await mcp_call("diff_since", {"date": "2026-04-01"})
    assert all(e["last_touched_at"] >= "2026-04-01" for e in entities)


async def test_e5_clarification_returned_for_ambiguous_query():
    response = await query("rename the lob column")
    assert response.get("ambiguity") is True
    assert len(response["interpretations"]) >= 2


async def test_e6_cross_repo_similarity_surfaces():
    # seed two repos with similar patterns; assert second repo gets a SimilarTo edge
    pass


async def test_e7_diagram_extracted_and_queryable():
    # repo with docs/architecture.png; assert Diagram entity emitted with components
    diagrams = await brain_query("list Diagram entities")
    assert len(diagrams) >= 1
```

---

## Effort estimate

5 days total (slightly larger than peers). E1 is the heaviest (~1.5 days) — it's a real sub-agent. E7 needs the vision API wired up.

---

## Action items

1. [ ] `agents/exploration_agent.py` — sub-agent with 6 tools + 8-step cap.
2. [ ] `query.py` — invoke E1 when initial confidence < 0.6 OR zone empty.
3. [ ] `query_reread.py` — re-fetch source for cited entities with confidence < 0.7.
4. [ ] `mcp/tools/trace_exception.py` + `diff_since.py`.
5. [ ] `clarification.py` — detector for ambiguous queries; structured response shape.
6. [ ] `cross_repo_similarity.py` — Qdrant vector search across workspaces; emit SimilarTo edges.
7. [ ] `diagram_extractor.py` — vision-extract `docs/**/*.png|svg`; emit Diagram entities.
8. [ ] Acceptance tests E1-E7.
9. [ ] Telemetry: per-run count of E1 invocations, E5 clarifications returned, E6 cross-repo hits.
